"""
Crawl orchestrator: end-to-end ingest pipeline.

For each guideline discovered on the IA HK index:
  1. Download the PDF (skip if cached)
  2. Extract text per page
  3. Chunk the text
  4. Embed the chunks
  5. Store in SQLite (guidelines + chunks + vec_chunks)

Idempotent: re-running won't duplicate work, and will update changed PDFs.

Usage:
    .venv/bin/python -m src.crawl                # full run
    .venv/bin/python -m src.crawl --limit 5      # first 5 only
    .venv/bin/python -m src.crawl --reingest     # wipe + re-ingest all
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import LOG_DIR
from .crawler_index import fetch_index
from .crawler_pdf import (
    download_and_extract_guideline,
    local_path_for,
)
from .db import connect, init_db, insert_chunks, insert_vecs_bulk, stats, upsert_guideline
from .embed import chunk_pages, embed_texts

log = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "crawl.log"),
        ],
    )


def ingest_one(conn, guideline, sub_label: str = "", sub_url=None) -> dict:
    """
    Ingest one PDF (main or sub-document).
    Returns a dict with stats; logs progress.
    """
    label = sub_label or "main"
    log.info("--- %s [%s] ---", guideline.code, label)
    extracted = download_and_extract_guideline(
        guideline, sub_label=sub_label, sub_url=sub_url
    )

    # Store the guideline row
    guideline_id = upsert_guideline(
        conn,
        code=guideline.code,
        title=guideline.title,
        source_url=extracted.source_url,
        local_path=str(extracted.pdf_path),
        sha256=extracted.sha256,
        page_count=extracted.page_count,
        file_bytes=extracted.pdf_path.stat().st_size,
        is_main=(sub_label == ""),
        parent_code=None if sub_label == "" else guideline.code,
        version_label=guideline.version_label,
        is_repealed=guideline.is_repealed,
        extraction_engine=extracted.extraction_engine,
        extraction_warnings=extracted.extraction_warnings,
    )

    # Chunk the extracted text
    chunks = chunk_pages(extracted.pages_text)
    log.info("  pages=%d  chunks=%d  engine=%s  warnings=%d",
             extracted.page_count, len(chunks),
             extracted.extraction_engine, len(extracted.extraction_warnings))

    if not chunks:
        return {
            "code": guideline.code,
            "label": label,
            "pages": extracted.page_count,
            "chunks": 0,
            "vecs": 0,
        }

    # Store the chunks
    n_chunks = insert_chunks(conn, guideline_id, [c.to_dict() for c in chunks])

    # Get the just-inserted chunk rows so we have their ids
    chunk_rows = list(conn.execute(
        "SELECT id, text FROM chunks WHERE guideline_id = ? ORDER BY chunk_index",
        (guideline_id,),
    ))

    # Embed all chunks in one batch
    texts = [r["text"] for r in chunk_rows]
    t0 = time.time()
    vectors = embed_texts(texts)
    t_embed = time.time() - t0
    log.info("  embedded %d chunks in %.1fs", len(vectors), t_embed)

    # Insert vectors
    insert_vecs_bulk(conn, [(r["id"], v) for r, v in zip(chunk_rows, vectors)])
    conn.commit()

    return {
        "code": guideline.code,
        "label": label,
        "pages": extracted.page_count,
        "chunks": n_chunks,
        "vecs": len(vectors),
        "embed_seconds": round(t_embed, 1),
    }


def run(limit: int | None = None, reingest: bool = False) -> None:
    init_db()

    guidelines = fetch_index()
    active = [g for g in guidelines if not g.is_repealed and g.url]
    if limit:
        active = active[:limit]
    log.info("Discovered %d active guidelines (repealed=%d). Processing %d.",
             len(active), len(guidelines) - len(active), len(active))

    if reingest:
        log.warning("--reingest: wiping all data first")
        with connect() as conn:
            conn.execute("DELETE FROM vec_chunks")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM guidelines")
            conn.commit()

    t_start = time.time()
    summary = []
    with connect() as conn:
        for g in active:
            # Main document
            try:
                summary.append(ingest_one(conn, g))
            except Exception as e:
                log.exception("Failed on %s main: %s", g.code, e)
                conn.rollback()
            # Sub-documents
            for sd in g.sub_documents:
                try:
                    summary.append(ingest_one(conn, g, sub_label=sd.title, sub_url=sd.url))
                except Exception as e:
                    log.exception("Failed on %s sub '%s': %s", g.code, sd.title, e)
                    conn.rollback()

    elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("DONE in %.1fs", elapsed)
    log.info("PDFs processed: %d", len(summary))
    log.info("Total chunks:   %d", sum(s["chunks"] for s in summary))
    log.info("Total vectors:  %d", sum(s["vecs"] for s in summary))

    # Print DB stats
    s = stats()
    log.info("DB now holds: %d guidelines, %d chunks, %d vectors",
             s["guidelines"], s["chunks"], s["vec_chunks"])


def reembed_all() -> None:
    """
    Re-embed all existing chunks in the database with the CURRENT provider.
    Use this when:
      - You switch EMBEDDING_PROVIDER in .env (e.g. minimax -> openai)
      - You change EMBEDDING_MODEL within a provider
      - The vec_chunks table was dropped by init_db() due to a dim change

    This does NOT re-download or re-chunk anything. It just re-runs the
    embedding call for every existing chunk and replaces the vec_chunks rows.
    """
    from .db import clear_vec_chunks, get_chunk_id_to_row_mapping, insert_vecs_bulk
    from .embed import embed_texts, get_provider

    provider = get_provider()
    log.info("Re-embedding all chunks with provider=%s (dim=%d)",
             provider.name, provider.dim)

    init_db()  # drops + recreates vec_chunks if dim changed

    with connect() as conn:
        chunk_rows = get_chunk_id_to_row_mapping(conn)
        if not chunk_rows:
            log.warning("No chunks in DB. Run the full crawl first.")
            return

        log.info("Found %d chunks to re-embed", len(chunk_rows))
        clear_vec_chunks(conn)

        # Embed in batches of 96 to avoid rate limits
        BATCH = 96
        t0 = time.time()
        for i in range(0, len(chunk_rows), BATCH):
            batch = chunk_rows[i : i + BATCH]
            texts = [r["text"] for r in batch]
            vecs = embed_texts(texts)
            insert_vecs_bulk(conn, [(r["chunk_id"], v) for r, v in zip(batch, vecs)])
            log.info("  embedded %d / %d chunks", min(i + BATCH, len(chunk_rows)), len(chunk_rows))

        conn.commit()
        elapsed = time.time() - t0
        log.info("Re-embed done in %.1fs", elapsed)
        s = stats(conn)
        log.info("DB now holds: %d guidelines, %d chunks, %d vectors",
                 s["guidelines"], s["chunks"], s["vec_chunks"])


def main():
    p = argparse.ArgumentParser(description="Crawl IA HK guidelines and ingest into the DB")
    p.add_argument("--limit", type=int, default=None, help="Process only the first N guidelines")
    p.add_argument("--reingest", action="store_true", help="Wipe existing data before ingesting")
    p.add_argument("--reembed-only", action="store_true",
                   help="Re-embed existing chunks with the current EMBEDDING_PROVIDER "
                        "(no PDF re-download, no re-chunking). Use after switching providers.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()
    setup_logging("DEBUG" if args.verbose else "INFO")
    if args.reembed_only:
        reembed_all()
    else:
        run(limit=args.limit, reingest=args.reingest)


if __name__ == "__main__":
    main()
