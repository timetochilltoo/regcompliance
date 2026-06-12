"""
Embedding generation + text chunking.

Two responsibilities:
  1. Chunk PDF text into retrieval-sized pieces (with page tracking).
  2. Generate vector embeddings for chunks using the configured provider.

Chunking strategy:
  - Walk through pages sequentially.
  - On each page, build overlapping windows of ~CHUNK_SIZE characters
    with CHUNK_OVERLAP overlap.
  - Never split mid-sentence if we can avoid it (snap to the nearest
    paragraph break before CHUNK_SIZE).
  - Record the page number and char offset for each chunk so we can
    cite (GL3, p.5) back to the auditor.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)

log = logging.getLogger(__name__)


@dataclass
class Chunk:
    chunk_index: int
    page_number: int
    char_start: int  # within the page
    char_end: int
    text: str
    section_number: str = ""   # e.g. "4.2.1" or "" if not detected
    section_heading: str = ""  # e.g. "Customer due diligence" or ""

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": self.text,
            "section_number": self.section_number,
            "section_heading": self.section_heading,
            "char_count": len(self.text),
        }


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_pages(
    pages_text: List[str],
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[Chunk]:
    """
    Split a list of page-text strings into overlapping chunks.

    Each chunk carries:
      - chunk_index: global ordinal (0, 1, 2, ...)
      - page_number: the 1-indexed page where the chunk starts
      - char_start / char_end: offset within that page's text
      - text: the chunk content

    The chunker snaps to paragraph breaks (double newline) or sentence
    boundaries when possible, so chunks don't end mid-word.
    """
    chunks: List[Chunk] = []
    chunk_index = 0
    step = max(1, chunk_size - chunk_overlap)

    for page_idx, page_text in enumerate(pages_text, start=1):
        if not page_text or not page_text.strip():
            continue

        # Normalize whitespace but keep paragraph breaks
        normalized = re.sub(r"[ \t]+", " ", page_text)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)

        n = len(normalized)
        if n == 0:
            continue

        # If the page fits in one chunk, emit it as-is
        if n <= chunk_size:
            sec_num, sec_head = _detect_section_at(normalized, 0)
            chunks.append(Chunk(
                chunk_index=chunk_index,
                page_number=page_idx,
                char_start=0,
                char_end=n,
                text=normalized,
                section_number=sec_num,
                section_heading=sec_head,
            ))
            chunk_index += 1
            continue

        # Walk the page with overlapping windows
        cursor = 0
        while cursor < n:
            end = min(cursor + chunk_size, n)

            # Try to snap 'end' backwards to a paragraph break or sentence end
            if end < n:
                snap = _snap_backward(normalized, cursor, end)
                if snap > cursor + chunk_size // 2:
                    end = snap

            text = normalized[cursor:end].strip()
            if text:
                sec_num, sec_head = _detect_section_at(normalized, cursor)
                chunks.append(Chunk(
                    chunk_index=chunk_index,
                    page_number=page_idx,
                    char_start=cursor,
                    char_end=end,
                    text=text,
                    section_number=sec_num,
                    section_heading=sec_head,
                ))
                chunk_index += 1

            if end >= n:
                break
            cursor = end - chunk_overlap
            if cursor <= 0:
                cursor = end  # safety, avoid infinite loop

    return chunks


def _snap_backward(text: str, start: int, end: int) -> int:
    """
    Try to find a good break point at or before 'end' (and after start +
    half-chunk). Look for: paragraph break (\\n\\n), sentence end (.!?)
    followed by whitespace, or failing those, a whitespace.
    """
    window = text[start:end]
    # Paragraph break
    idx = window.rfind("\n\n")
    if idx != -1 and idx > len(window) // 2:
        return start + idx + 2
    # Sentence end
    for m in re.finditer(r"[.!?]\s+", window):
        pass  # we want the LAST one
    matches = list(re.finditer(r"[.!?]\s+", window))
    if matches:
        last = matches[-1]
        if last.start() > len(window) // 2:
            return start + last.end()
    # Whitespace fallback
    idx = window.rfind(" ")
    if idx != -1 and idx > len(window) // 2:
        return start + idx + 1
    return end


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------
# IA HK guidelines number sections in several common styles. We catch the
# most common ones; anything we miss falls back to "" (no section metadata).
_SECTION_PATTERNS = [
    # 1.2.3  Title           (numbered top-level)
    re.compile(r"^\s*(\d+(?:\.\d+){0,4})\s+([A-Z][^\n]{2,120})$", re.MULTILINE),
    # 1.2.3  Title           (no period at end)
    re.compile(r"^\s*(\d+(?:\.\d+){0,4})\s+([A-Z][^\n]{2,120})(?=\n|$)", re.MULTILINE),
    # §4.2.1   or  Article 5
    re.compile(r"^\s*(?:§|Section|Article|Clause)\s+(\d+(?:\.\d+){0,4})[\s.:]+([A-Z][^\n]{2,120})?",
               re.IGNORECASE | re.MULTILINE),
    # GL3-5.3   (some guidelines prefix with the code)
    re.compile(r"^\s*(GL\d+[A-Za-z]?)-(\d+(?:\.\d+){0,4})[\s.:]+([A-Z][^\n]{2,120})?",
               re.MULTILINE),
    # Chapter X
    re.compile(r"^\s*Chapter\s+(\d+)\s*[:\-]?\s*([A-Z][^\n]{2,120})?",
               re.IGNORECASE | re.MULTILINE),
]


def _detect_section_at(page_text: str, char_offset: int) -> tuple[str, str]:
    """
    Look backwards from char_offset in page_text to find the most recent
    section heading. Returns (section_number, section_heading). Both can be "".
    """
    # Look at a window before char_offset (cap to avoid huge scans)
    window_start = max(0, char_offset - 2000)
    window = page_text[window_start:char_offset]
    if not window.strip():
        return "", ""

    # Find the last match of any pattern
    best_pos = -1
    best_number = ""
    best_heading = ""
    for pat in _SECTION_PATTERNS:
        for m in pat.finditer(window):
            if m.start() > best_pos:
                best_pos = m.start()
                # The group containing the number is whichever index has it
                if m.lastindex and m.lastindex >= 2:
                    best_number = m.group(1) or ""
                    best_heading = (m.group(2) or "").strip().rstrip(".")
                else:
                    best_number = m.group(1) or ""
                    best_heading = ""
    return best_number, best_heading


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def _get_openai_compatible_client(api_key: str, base_url: Optional[str] = None):
    """Return an OpenAI client pointed at any OpenAI-API-compatible endpoint."""
    from openai import OpenAI
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _provider_config() -> tuple[str, str, str]:
    """
    Return (provider_name, api_key, model_name) for the configured embedding
    provider. Raises if the provider isn't wired up.
    """
    from .config import MINIMAX_API_KEY, MINIMAX_BASE_URL, MINIMAX_EMBEDDING_MODEL

    if EMBEDDING_PROVIDER == "minimax":
        if not MINIMAX_API_KEY:
            raise RuntimeError(
                "MINIMAX_API_KEY is not set. Add it to your .env file."
            )
        return ("minimax", MINIMAX_API_KEY, MINIMAX_EMBEDDING_MODEL)
    if EMBEDDING_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to your .env file."
            )
        return ("openai", OPENAI_API_KEY, EMBEDDING_MODEL)
    raise NotImplementedError(
        f"Embedding provider '{EMBEDDING_PROVIDER}' is not wired up yet. "
        "Supported: 'openai', 'minimax'."
    )


def embed_texts(texts: List[str], model: Optional[str] = None) -> List[List[float]]:
    """
    Embed a batch of strings. Returns one vector per input string.
    """
    provider, api_key, default_model = _provider_config()
    model = model or default_model

    from .config import MINIMAX_BASE_URL
    base_url = MINIMAX_BASE_URL if provider == "minimax" else OPENAI_BASE_URL
    client = _get_openai_compatible_client(api_key, base_url)

    log.info("Embedding %d chunks with %s (provider=%s, dim=%d)",
             len(texts), model, provider, EMBEDDING_DIM)

    # The OpenAI API caps batch size; chunk to be safe.
    BATCH = 96
    all_vectors: List[List[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        resp = client.embeddings.create(model=model, input=batch)
        for item in resp.data:
            all_vectors.append(item.embedding)
    return all_vectors


def embed_query(text: str) -> List[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]
