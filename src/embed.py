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
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    JINA_API_KEY,
    JINA_BASE_URL,
    JINA_EMBEDDING_DIM,
    JINA_EMBEDDING_MODEL,
    MINIMAX_API_KEY,
    MINIMAX_BASE_URL,
    MINIMAX_EMBEDDING_DIM,
    MINIMAX_EMBEDDING_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_EMBEDDING_DIM,
    OPENAI_EMBEDDING_MODEL,
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
# Embeddings — provider registry
# ---------------------------------------------------------------------------
# Each provider implements the same interface:
#   def name -> str
#   def dim -> int
#   def embed_texts(texts: List[str]) -> List[List[float]]
#   def embed_query(text: str) -> List[float]
#
# To add a new provider:
#   1. Write a class with those four things
#   2. Register it in _PROVIDERS below
#   3. Add its config block to src/config.py
#   4. Add a doc string to .env.example
#
# All providers use a fixed EMBEDDING_DIM. The sqlite-vec virtual table is
# sized at table-creation time, so changing dim requires re-initializing the
# schema (drop the vec_chunks table and re-crawl).

class EmbeddingProvider:
    """Base interface for all embedding providers."""

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def dim(self) -> int:
        raise NotImplementedError

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of document chunks. Returns one vector per text."""
        raise NotImplementedError

    def embed_query(self, text: str) -> List[float]:
        """Embed a single user query. Default: call embed_texts on a single item."""
        return self.embed_texts([text])[0]


class OpenAIProvider(EmbeddingProvider):
    """OpenAI embeddings — uses the openai SDK. Also covers OpenAI-compatible endpoints."""

    @property
    def name(self) -> str:
        return "openai"

    @property
    def dim(self) -> int:
        return OPENAI_EMBEDDING_DIM

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        from openai import OpenAI
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)
        BATCH = 96
        all_vectors: List[List[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            resp = client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=batch)
            for item in resp.data:
                all_vectors.append(item.embedding)
        return all_vectors


class MiniMaxProvider(EmbeddingProvider):
    """
    MiniMax M-series embeddings — direct HTTP (NOT via openai SDK).
    The MiniMax API uses 'texts' (not 'input') and requires a 'type' field
    ('db' for documents, 'query' for queries). The base URL is
    https://api.minimaxi.com/v1 (note: minimaxi.com, NOT minimax.com).
    """

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def dim(self) -> int:
        return MINIMAX_EMBEDDING_DIM

    def _call(self, texts: List[str], embed_type: str) -> List[List[float]]:
        import requests
        if not MINIMAX_API_KEY:
            raise RuntimeError("MINIMAX_API_KEY is not set. Add it to your .env file.")
        url = f"{MINIMAX_BASE_URL.rstrip('/')}/embeddings"
        payload = {
            "model": MINIMAX_EMBEDDING_MODEL,
            "texts": texts,
            "type": embed_type,
        }
        headers = {
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            # Query endpoint is newer/optional — fall back to db
            if embed_type == "query":
                log.warning("MiniMax query embedding failed, falling back to type=db")
                return self._call(texts, embed_type="db")
            raise RuntimeError(
                f"MiniMax embedding failed: status_code={base_resp.get('status_code')} "
                f"status_msg={base_resp.get('status_msg')} body={data}"
            )
        return data["vectors"]

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self._call(texts, embed_type="db")

    def embed_query(self, text: str) -> List[float]:
        return self._call([text], embed_type="query")[0]


class JinaProvider(EmbeddingProvider):
    """
    Jina AI embeddings — REST API with task-aware embeddings.
    Jina supports 'retrieval.passage' for documents and 'retrieval.query'
    for queries, which gives better retrieval quality than task-agnostic
    embeddings. Free tier: 1M tokens/month. Pro: $0.02/1M tokens.
    """

    @property
    def name(self) -> str:
        return "jina"

    @property
    def dim(self) -> int:
        return JINA_EMBEDDING_DIM

    def _call(self, texts: List[str], task: str) -> List[List[float]]:
        import requests
        if not JINA_API_KEY:
            raise RuntimeError("JINA_API_KEY is not set. Add it to your .env file.")
        url = f"{JINA_BASE_URL.rstrip('/')}/embeddings"
        payload = {
            "model": JINA_EMBEDDING_MODEL,
            "input": texts,
            "task": task,
            "dimensions": JINA_EMBEDDING_DIM,
            "embedding_type": "float",
        }
        headers = {
            "Authorization": f"Bearer {JINA_API_KEY}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data:
            raise RuntimeError(f"Jina embedding failed: {data}")
        return [item["embedding"] for item in data["data"]]

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self._call(texts, task="retrieval.passage")

    def embed_query(self, text: str) -> List[float]:
        return self._call([text], task="retrieval.query")[0]


# Provider registry — maps provider name (from env) to its class
_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "openai": OpenAIProvider,
    "minimax": MiniMaxProvider,
    "jina": JinaProvider,
}


def get_provider() -> EmbeddingProvider:
    """
    Return the configured embedding provider instance.
    Validates the provider is registered; raises NotImplementedError otherwise.
    """
    provider_name = (EMBEDDING_PROVIDER or "").lower().strip()
    if not provider_name:
        raise RuntimeError(
            "EMBEDDING_PROVIDER is not set in .env. "
            "Choose one of: openai, minimax, jina."
        )
    if provider_name not in _PROVIDERS:
        raise NotImplementedError(
            f"Embedding provider '{provider_name}' is not wired up. "
            f"Supported: {sorted(_PROVIDERS.keys())}."
        )
    return _PROVIDERS[provider_name]()


def list_providers() -> list[str]:
    """Names of all registered embedding providers. For CLI / help text."""
    return sorted(_PROVIDERS.keys())


# Public API (used by crawl.py, ask.py, eval.py)
def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of document chunks using the configured provider."""
    provider = get_provider()
    if provider.dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"EMBEDDING_DIM={EMBEDDING_DIM} doesn't match provider {provider.name} "
            f"dim={provider.dim}. Set EMBEDDING_DIM={provider.dim} in .env, or "
            "re-initialize the database (drop the vec_chunks table) before re-ingesting."
        )
    log.info("Embedding %d chunks with provider=%s (model_dim=%d, db_dim=%d)",
             len(texts), provider.name, provider.dim, EMBEDDING_DIM)
    return provider.embed_texts(texts)


def embed_query(text: str) -> List[float]:
    """Embed a single user query using the configured provider."""
    provider = get_provider()
    return provider.embed_query(text)
