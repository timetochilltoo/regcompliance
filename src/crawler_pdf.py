"""
PDF downloader + text extractor for IA HK guidelines.

Two responsibilities:
  1. Download a PDF from a URL to data/pdfs/IA/<code>_<version>.pdf
  2. Extract text from a PDF (per-page) using pdfplumber (primary) and
     pypdf (fallback). pdfplumber handles tables better — important for
     regulatory text with control matrices.

Skips download if a file with the same SHA-256 already exists.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pdfplumber
import pypdf
import requests

from .config import CRAWL_DELAY_SEC, HTTP_USER_AGENT, PDF_DIR
from .crawler_index import Guideline, SubDocument

log = logging.getLogger(__name__)


@dataclass
class ExtractedPdf:
    """Result of extracting one PDF."""
    code: str            # GL3, GL16-prev, etc.
    title: str
    source_url: str
    pdf_path: Path
    sha256: str
    page_count: int
    pages_text: List[str]      # one entry per page
    extraction_engine: str     # "pdfplumber" or "pypdf"
    extraction_warnings: List[str]


def _slugify_for_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")


def local_path_for(
    code: str,
    url: str,
    title: str = "",
    sub_label: str = "",
    version_label: str = "",
) -> Path:
    """
    Compute a human-readable local filename for a given guideline.

    Examples:
        GL3 + "Guideline on Anti-Money Laundering..." -> "GL3_Guideline_on_Anti-Money_Laundering.pdf"
        GL16 + title + "effective from 31 March 2026" -> "GL16_..._v2026-03-31.pdf"
        GL16 (previous version)                       -> "GL16_..._v-prev-2026-03-30.pdf"
        GL14 sub-doc "Q&A"                            -> "GL14_..._Q&A.pdf"
    """
    parts = [code]
    if title:
        # Strip the leading "GL3: " code prefix from the title if present
        cleaned = re.sub(r"^\s*GL[A-Za-z0-9]+\s*[:\-\u2013\u2014]?\s*", "", title).strip()
        if cleaned:
            parts.append(_slugify_for_filename(cleaned)[:80])

    if sub_label:
        parts.append(_slugify_for_filename(sub_label)[:40])

    if version_label:
        # Try to extract a date for compact disambiguation
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", version_label)
        if m:
            day, month_name, year = m.group(1), m.group(2), m.group(3)
            month_num = _month_to_num(month_name)
            tag = f"v{year}-{month_num:02d}-{int(day):02d}"
            if "until" in version_label.lower():
                tag += "-prev"
            parts.append(tag)

    filename = "_".join(parts) + ".pdf"
    return PDF_DIR / filename


def _month_to_num(name: str) -> int:
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    return months.get(name.lower(), 0)


def download_pdf(url: str, dest: Path) -> Path:
    """Download a PDF; skip if the destination already has content."""
    if dest.exists() and dest.stat().st_size > 0:
        log.debug("Already have %s, skipping", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s -> %s", url, dest.name)
    resp = requests.get(
        url,
        headers={"User-Agent": HTTP_USER_AGENT},
        timeout=60,
        stream=True,
    )
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)
    time.sleep(CRAWL_DELAY_SEC)  # be polite
    return dest


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text(pdf_path: Path) -> ExtractedPdf:
    """
    Extract text per page. Try pdfplumber first (better at tables), fall back
    to pypdf if pdfplumber returns empty (e.g. encrypted/odd PDFs).
    """
    warnings: List[str] = []
    sha = sha256_of_file(pdf_path)
    pages_text: List[str] = []
    engine = ""

    # --- pdfplumber pass ---
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as e:  # some pages blow up
                    warnings.append(f"pdfplumber page {i} failed: {e}")
                    text = ""
                pages_text.append(text)
        engine = "pdfplumber"
    except Exception as e:
        warnings.append(f"pdfplumber open failed: {e}")
        pages_text = []
        page_count = 0

    # If pdfplumber produced no usable text, try pypdf
    non_empty = sum(1 for p in pages_text if p.strip())
    if non_empty == 0:
        log.info("pdfplumber yielded no text for %s, falling back to pypdf", pdf_path.name)
        try:
            reader = pypdf.PdfReader(str(pdf_path))
            page_count = len(reader.pages)
            pages_text = []
            for i, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as e:
                    warnings.append(f"pypdf page {i} failed: {e}")
                    text = ""
                pages_text.append(text)
            engine = "pypdf"
        except Exception as e:
            warnings.append(f"pypdf open failed: {e}")
            pages_text = []
            page_count = 0

    return ExtractedPdf(
        code="",  # filled by caller
        title="",
        source_url="",
        pdf_path=pdf_path,
        sha256=sha,
        page_count=page_count,
        pages_text=pages_text,
        extraction_engine=engine,
        extraction_warnings=warnings,
    )


def download_and_extract_guideline(
    guideline: Guideline,
    sub_label: str = "",
    sub_url: Optional[str] = None,
) -> ExtractedPdf:
    """
    Download the PDF (main or sub) and extract its text.
    """
    url = sub_url or guideline.url
    if url is None:
        raise ValueError(f"Guideline {guideline.code} has no URL (repealed?)")

    dest = local_path_for(
        code=guideline.code,
        url=url,
        title=guideline.title,
        sub_label=sub_label,
        version_label=guideline.version_label,
    )
    download_pdf(url, dest)
    extracted = extract_text(dest)
    extracted.code = guideline.code
    extracted.title = (
        f"{guideline.code} {sub_label}: {guideline.title}".strip()
        if sub_label
        else f"{guideline.code}: {guideline.title}"
    )
    extracted.source_url = url
    return extracted


def download_sample(guidelines: List[Guideline], n: int) -> List[ExtractedPdf]:
    """Download and extract the first N active guidelines — for the smoke test."""
    active = [g for g in guidelines if not g.is_repealed and g.url]
    results: List[ExtractedPdf] = []
    for g in active[:n]:
        try:
            results.append(download_and_extract_guideline(g))
        except Exception as e:
            log.exception("Failed on %s: %s", g.code, e)
    return results
