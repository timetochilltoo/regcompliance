"""
Benchmark embedding providers against each other.

For each provider in {openai, minimax, jina}:
  1. Save the current EMBEDDING_PROVIDER setting
  2. Re-embed the corpus with the test provider
  3. Run a fixed set of test queries, recording top-5 hits
  4. Score the top-5 (1 point per relevant chunk in top-5)
  5. Restore the original provider

This is meant to be run ONCE when you want to pick a provider, not on every
ingest. It takes ~2-3 min per provider (mostly the re-embed step).

Usage:
    python -m src.benchmark                  # benchmark all configured providers
    python -m src.benchmark --providers openai minimax
    python -m src.benchmark --query-only     # don't re-embed, just re-run queries
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# Bootstrap: load .env so config picks up values
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from src.config import (
    DB_PATH,
    EMBEDDING_DIM,
    EMBEDDING_PROVIDER as CURRENT_PROVIDER,
    EMBEDDING_MODEL as CURRENT_MODEL,
)
from src.crawl import reembed_all, setup_logging
from src.db import connect, hybrid_search, stats
from src.embed import embed_query, get_provider, list_providers

log = logging.getLogger(__name__)


# A small but representative set of auditor queries. Each has a list of
# (guideline_code, page) pairs that we consider a "correct" answer. We
# score by how many of the top-5 retrieved chunks come from any of these.
# These are intentionally conservative — top-5 might include other relevant
# chunks we didn't list, but we only penalize missing the known-good ones.
TEST_QUERIES: List[dict] = [
    {
        "q": "What is the required audit frequency for AML compliance?",
        "relevant": [("GL3", None)],  # any page in GL3
        "notes": "Should hit GL3 (AML guideline), specifically the audit section",
    },
    {
        "q": "List the controls expected for customer due diligence",
        "relevant": [("GL3", None)],
        "notes": "GL3 §3-4 area",
    },
    {
        "q": "What are the requirements for outsourcing by insurers?",
        "relevant": [("GL14", None)],
        "notes": "GL14 is the Outsourcing guideline",
    },
    {
        "q": "Cybersecurity controls for insurance companies",
        "relevant": [("GL20", None)],
        "notes": "GL20 is the Cybersecurity guideline",
    },
    {
        "q": "Enterprise risk management requirements",
        "relevant": [("GL21", None)],
        "notes": "GL21 is ERM",
    },
    {
        "q": "Continuing professional development for insurance intermediaries",
        "relevant": [("GL24", None)],
        "notes": "GL24 is CPD",
    },
    {
        "q": "Underwriting requirements for long term insurance business",
        "relevant": [("GL16", None), ("GL15", None)],
        "notes": "GL15 (Class C) and GL16 (other long term)",
    },
    {
        "q": "Corporate governance of authorized insurers",
        "relevant": [("GL10", None)],
        "notes": "GL10 is Corporate Governance",
    },
    {
        "q": "Record keeping requirements for insurance intermediaries",
        "relevant": [("GL23", None), ("GL14", None)],
        "notes": "GL23 fit-and-proper; GL14 may also cover",
    },
    {
        "q": "Solvency and capital requirements",
        "relevant": [("GL36", None), ("GL5", None), ("GL32", None)],
        "notes": "GL36 valuation/capital; GL32 group supervision; GL5 authorization",
    },
]


def is_relevant(chunk: dict, relevant: list[tuple]) -> bool:
    """Check if a chunk matches any of the (code, page) relevant criteria."""
    code = (chunk.get("code") or "").upper()
    page = chunk.get("page_number")
    for rel_code, rel_page in relevant:
        if code != rel_code.upper():
            continue
        if rel_page is None or rel_page == page:
            return True
    return False


def score_query(hits: list[dict], relevant: list[tuple]) -> dict:
    """
    Score top-5 hits. Metrics:
      - recall_at_5: fraction of unique relevant guidelines found in top-5
      - top1_relevant: bool, was the top-1 hit relevant?
      - first_relevant_rank: 1-indexed rank of first relevant hit (5 if none)
    """
    if not hits:
        return {"recall_at_5": 0.0, "top1_relevant": False, "first_relevant_rank": 5}

    # Track which relevant guidelines have been hit
    hit_codes = set()
    first_rank = 5
    for i, h in enumerate(hits[:5], start=1):
        if is_relevant(h, relevant):
            hit_codes.add(h["code"].upper())
            if first_rank == 5:
                first_rank = i

    recall = len(hit_codes) / max(1, len({c for c, _ in relevant}))
    return {
        "recall_at_5": round(recall, 2),
        "top1_relevant": first_rank == 1,
        "first_relevant_rank": first_rank,
    }


def run_queries_for_provider(provider_name: str, model_name: str) -> dict:
    """
    For the current (already-embedded) corpus, run TEST_QUERIES via hybrid
    search and return aggregate metrics.
    """
    log.info("=" * 60)
    log.info("BENCHMARK: provider=%s model=%s", provider_name, model_name)
    log.info("=" * 60)

    with connect() as conn:
        results = []
        for tq in TEST_QUERIES:
            qvec = embed_query(tq["q"])
            hits = hybrid_search(conn, tq["q"], qvec, top_k=5)
            s = score_query(hits, tq["relevant"])
            results.append({
                "query": tq["q"],
                "score": s,
                "top1_code": hits[0]["code"] if hits else None,
            })
            log.info("  Q: %s", tq["q"][:60])
            log.info("    top1=%s  recall=%.2f  first_rel=%d  %s",
                     results[-1]["top1_code"], s["recall_at_5"],
                     s["first_relevant_rank"],
                     "✓" if s["top1_relevant"] else "✗")

        # Aggregate
        avg_recall = sum(r["score"]["recall_at_5"] for r in results) / len(results)
        top1_rate = sum(1 for r in results if r["score"]["top1_relevant"]) / len(results)
        avg_first_rank = sum(r["score"]["first_relevant_rank"] for r in results) / len(results)

        summary = {
            "provider": provider_name,
            "model": model_name,
            "avg_recall_at_5": round(avg_recall, 3),
            "top1_accuracy": round(top1_rate, 3),
            "avg_first_relevant_rank": round(avg_first_rank, 2),
            "queries": results,
        }
        log.info("AGGREGATE for %s: avg_recall=%.3f  top1=%.0f%%  avg_rank=%.2f",
                 provider_name, avg_recall, top1_rate * 100, avg_first_rank)
        return summary


def main():
    p = argparse.ArgumentParser(description="Benchmark embedding providers")
    p.add_argument("--providers", nargs="*", default=None,
                   help="Subset of providers to test. Default: all registered.")
    p.add_argument("--query-only", action="store_true",
                   help="Skip re-embedding; just run queries against current corpus")
    p.add_argument("--out", default="reports/benchmark_results.json",
                   help="Where to write the JSON results")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()
    setup_logging("DEBUG" if args.verbose else "INFO")

    if not Path(DB_PATH).exists():
        log.error("No database at %s. Run 'python -m src.crawl' first.", DB_PATH)
        sys.exit(1)

    providers = args.providers or list_providers()
    log.info("Will benchmark providers: %s", providers)

    summaries = []

    if args.query_only:
        # Just run the current provider's queries
        p_name = get_provider().name
        summaries.append(run_queries_for_provider(p_name, CURRENT_MODEL))
    else:
        # Re-embed + query for each provider in turn
        for prov in providers:
            log.info("--- Switching to provider=%s ---", prov)
            # We override the env var before any config import
            os.environ["EMBEDDING_PROVIDER"] = prov
            # Pick a sensible default model for each provider
            from src.config import (
                OPENAI_EMBEDDING_MODEL, MINIMAX_EMBEDDING_MODEL, JINA_EMBEDDING_MODEL,
                OPENAI_EMBEDDING_DIM, MINIMAX_EMBEDDING_DIM, JINA_EMBEDDING_DIM,
            )
            if prov == "openai":
                os.environ["EMBEDDING_MODEL"] = OPENAI_EMBEDDING_MODEL
                os.environ["EMBEDDING_DIM"] = str(OPENAI_EMBEDDING_DIM)
                model = OPENAI_EMBEDDING_MODEL
            elif prov == "minimax":
                os.environ["EMBEDDING_MODEL"] = MINIMAX_EMBEDDING_MODEL
                os.environ["EMBEDDING_DIM"] = str(MINIMAX_EMBEDDING_DIM)
                model = MINIMAX_EMBEDDING_MODEL
            elif prov == "jina":
                os.environ["EMBEDDING_MODEL"] = JINA_EMBEDDING_MODEL
                os.environ["EMBEDDING_DIM"] = str(JINA_EMBEDDING_DIM)
                model = JINA_EMBEDDING_MODEL
            else:
                continue

            # Re-import config and embed so the new env takes effect
            import importlib
            import src.config, src.embed, src.db
            importlib.reload(src.config)
            importlib.reload(src.embed)
            importlib.reload(src.db)

            # Re-embed the corpus with the new provider
            reembed_all()

            # Run the queries
            summaries.append(run_queries_for_provider(prov, model))

        # Restore the original provider setting
        os.environ["EMBEDDING_PROVIDER"] = CURRENT_PROVIDER
        os.environ["EMBEDDING_MODEL"] = CURRENT_MODEL
        os.environ["EMBEDDING_DIM"] = str(EMBEDDING_DIM)

    # Write results
    out_path = Path(__file__).resolve().parent.parent / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summaries, f, indent=2)
    log.info("Results written to %s", out_path)

    # Final summary
    print()
    print("=" * 60)
    print(f"{'Provider':<10}  {'Model':<30}  {'Recall@5':<10}  {'Top-1 %':<8}  {'Avg Rank':<8}")
    print("=" * 60)
    for s in summaries:
        print(f"{s['provider']:<10}  {s['model']:<30}  "
              f"{s['avg_recall_at_5']:<10}  {s['top1_accuracy']*100:<8.0f}  "
              f"{s['avg_first_relevant_rank']:<8.2f}")
    print("=" * 60)
    if len(summaries) > 1:
        winner = max(summaries, key=lambda s: (s["avg_recall_at_5"], s["top1_accuracy"]))
        print(f"\n→ Winner by combined score: {winner['provider']} ({winner['model']})")
        print(f"  To use it permanently, set in .env:")
        print(f"    EMBEDDING_PROVIDER={winner['provider']}")
        print(f"    EMBEDDING_MODEL={winner['model']}")
        print(f"    EMBEDDING_DIM=<check the provider's docs>")


if __name__ == "__main__":
    main()
