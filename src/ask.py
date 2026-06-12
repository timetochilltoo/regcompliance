"""
CLI Q&A tool: ask a question, get an answer with citations.

Usage:
    python -m src.ask "What is the required audit frequency for AML compliance?"
    python -m src.ask --template control_extraction "What controls are expected for CDD?"
    python -m src.ask --template audit_planning "List all required audits"
    python -m src.ask --no-log "free-form question"      # don't write to query_log
    python -m src.ask --user "Patrick" "question here"   # tag the audit log

Each call:
  1. Embeds the question (current EMBEDDING_PROVIDER)
  2. Hybrid search returns top-5 chunks
  3. Picks a prompt template (auto or --template)
  4. Calls the LLM (current LLM_PROVIDER)
  5. Prints the answer with source citations
  6. Logs the question + answer to the query_log table
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from .config import (
    DB_PATH,
    EMBEDDING_PROVIDER,
    LLM_PROVIDER,
    LOG_DIR,
    TOP_K_CHUNKS,
)
from .crawl import setup_logging
from .db import connect, hybrid_search, log_query
from .embed import embed_query
from .llm import chat
from .prompts import TEMPLATES, pick_template

log = logging.getLogger(__name__)


def answer_question(
    question: str,
    template: Optional[str] = None,
    top_k: int = 5,
    user: str = "cli",
) -> dict:
    """
    Full Q&A pipeline. Returns a dict with the answer, sources, and metadata.
    """
    t0 = time.time()

    # 1. Auto-select template if not specified
    if template is None:
        template = pick_template(question)
    if template not in TEMPLATES:
        raise ValueError(
            f"Unknown template '{template}'. "
            f"Available: {sorted(TEMPLATES.keys())}"
        )

    # 2. Embed the question
    log.info("Embedding question with provider=%s", EMBEDDING_PROVIDER)
    qvec = embed_query(question)

    # 3. Hybrid search
    log.info("Hybrid search, top_k=%d", top_k)
    with connect() as conn:
        chunks = hybrid_search(conn, question, qvec, top_k=top_k)
        if not chunks:
            log.warning("No chunks retrieved. The question may be out of scope.")
            chunks = []

    retrieval_ms = int((time.time() - t0) * 1000)

    # 4. Build the prompt and call the LLM
    messages = TEMPLATES[template](question, chunks)
    log.info("Calling LLM provider=%s template=%s", LLM_PROVIDER, template)
    resp = chat(messages, temperature=0.2)
    llm_ms = resp.get("latency_ms", 0)

    # 5. Log to query_log
    retrieved_summary = [
        {
            "chunk_id": c.get("chunk_id"),
            "guideline_code": c.get("code"),
            "page": c.get("page_number"),
            "section": c.get("section_number", ""),
            "section_heading": c.get("section_heading", ""),
            "hybrid_score": c.get("hybrid_score"),
        }
        for c in chunks
    ]
    try:
        with connect() as conn:
            log_query(
                conn,
                user=user,
                question=question,
                retrieved_chunks=retrieved_summary,
                llm_provider=resp.get("provider", LLM_PROVIDER),
                llm_model=resp.get("model", ""),
                answer=resp.get("text", ""),
                latency_ms=retrieval_ms + llm_ms,
            )
            conn.commit()
    except Exception as e:
        log.warning("Failed to log query: %s", e)

    return {
        "question": question,
        "template": template,
        "answer": resp.get("text", ""),
        "sources": [
            {
                "code": c["code"],
                "page": c["page_number"],
                "section": c.get("section_number", ""),
                "heading": c.get("section_heading", ""),
                "score": round(c.get("hybrid_score", 0), 3),
            }
            for c in chunks
        ],
        "model": resp.get("model", ""),
        "provider": resp.get("provider", ""),
        "input_tokens": resp.get("input_tokens"),
        "output_tokens": resp.get("output_tokens"),
        "retrieval_ms": retrieval_ms,
        "llm_ms": llm_ms,
    }


def format_output(result: dict, verbose: bool = False) -> str:
    """Pretty-print the result for terminal display."""
    out = []
    out.append("=" * 70)
    out.append(f"Q: {result['question']}")
    out.append(f"   [template: {result['template']}, "
               f"provider: {result['provider']}, model: {result['model']}]")
    out.append("=" * 70)
    out.append("")
    out.append(result["answer"].strip())
    out.append("")
    out.append("-" * 70)
    out.append("SOURCES:")
    for i, s in enumerate(result["sources"], start=1):
        loc = f"{s['code']} p.{s['page']}"
        if s.get("section"):
            loc += f" §{s['section']}"
        if s.get("heading"):
            loc += f" ({s['heading']})"
        out.append(f"  [{i}] {loc}  (score: {s['score']})")
    if verbose:
        out.append("")
        out.append("-" * 70)
        out.append(f"Tokens: in={result.get('input_tokens')} out={result.get('output_tokens')}")
        out.append(f"Timing: retrieval={result['retrieval_ms']}ms  llm={result['llm_ms']}ms")
    out.append("=" * 70)
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(
        description="Ask a question about IA HK guidelines (CLI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python -m src.ask "What is the required audit frequency for AML compliance?"
  python -m src.ask --template control_extraction "What controls for CDD on PEPs?"
  python -m src.ask --template audit_planning "List all required audits"
  python -m src.ask --user "Patrick" --json "Compare GL15 and GL16"
""",
    )
    p.add_argument("question", nargs="+", help="The question to ask (can be multiple words)")
    p.add_argument("--template", "-t", choices=sorted(TEMPLATES.keys()),
                   help="Force a specific prompt template (default: auto-select)")
    p.add_argument("--top-k", type=int, default=5, help="Number of chunks to retrieve (default: 5)")
    p.add_argument("--user", default="cli", help="User name for the audit log")
    p.add_argument("--no-log", action="store_true", help="Don't write this question to query_log")
    p.add_argument("--json", action="store_true", help="Output as JSON instead of pretty text")
    p.add_argument("--verbose", "-v", action="store_true", help="Include token counts and timing")
    p.add_argument("--quiet", action="store_true", help="Suppress info logging")
    args = p.parse_args()
    setup_logging("DEBUG" if args.verbose else ("WARNING" if args.quiet else "INFO"))

    question = " ".join(args.question)

    if args.no_log:
        # Monkey-patch the logging function
        global log_query
        from . import db
        orig = db.log_query
        db.log_query = lambda *a, **kw: None  # no-op
        try:
            result = answer_question(
                question,
                template=args.template,
                top_k=args.top_k,
                user=args.user,
            )
        finally:
            db.log_query = orig
    else:
        result = answer_question(
            question,
            template=args.template,
            top_k=args.top_k,
            user=args.user,
        )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_output(result, verbose=args.verbose))


if __name__ == "__main__":
    main()
