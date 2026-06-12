# Step 2 — Database, Embeddings & Hybrid Retrieval

**Date:** 2026-06-12
**Status:** ✅ Complete
**Database:** `/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance/data/regulatory.db`
**Total storage:** 58 MB (29 MB PDFs + 29 MB SQLite DB)

## TL;DR

Full ingest of **62 IA HK PDFs** → **3,513 chunks** → **3,513 vector embeddings** (1536-dim each, from MiniMax embo-01). All stored in a single SQLite file with full hybrid retrieval (vector + FTS5 keyword + section metadata). Hybrid search smoke test on "audit frequency AML" returns GL3 §3.12 (the audit function) as the top hit — exactly right.

## What got built

| Component | Status | Notes |
|---|---|---|
| SQLite schema | ✅ | `guidelines`, `chunks` (with section metadata), `chunks_fts` (FTS5), `vec_chunks` (sqlite-vec), `query_log` (audit trail) |
| Section-aware chunking | ✅ | Extracts `section_number` + `section_heading` per chunk |
| MiniMax embo-01 embedding | ✅ | Direct HTTP, payload `texts` + `type=db` (or `type=query` for user questions) |
| Hybrid retrieval | ✅ | Vector + FTS5 BM25 + title/heading boosts + rerank |
| Full crawl pipeline | ✅ | 188 seconds for 62 PDFs |

## Database breakdown

| Code | Chunks | Characters | What it is |
|---|---|---|---|
| GL32 | 599 | 454k | Group Supervision |
| GL3 | 351 | 284k | Anti-Money Laundering |
| GL36 | 340 | 274k | Valuation & Capital Requirements |
| GL26 | 224 | 180k | ILAS Products |
| GL21 | 217 | 175k | Enterprise Risk Management |
| GL20 | 193 | 148k | Cybersecurity |
| GL24 | 154 | 121k | CPD for Intermediaries |
| GL28 | 130 | 99k | Benefit Illustrations |
| GL34 | 103 | 83k | Participating Business |
| GL16 | 95 | 76k | Long Term Insurance Underwriting |
| (others) | 1,107 | ~890k | Various |
| **Total** | **3,513** | **~2.78M** | |

## Hybrid search architecture

For each user question, the retriever:

1. **Vector search** (sqlite-vec, cosine distance) — finds semantically similar chunks
2. **FTS5 keyword search** (BM25) — finds exact matches for section numbers, regulatory phrases, quoted terms
3. **Merge** with weighted scoring: 0.5 × vector + 0.3 × FTS
4. **Boost** for:
   - Guideline code appearing in the question (+0.15)
   - Section heading text in the question (+0.05)
   - Partial title match (+0.075)
5. **Deduplicate** by chunk_id, **rerank**, return top-k

**Why this matters for regulatory Q&A:** vector search alone misses "GL3 §4.2.1" citations and exact regulatory phrasing. FTS5 catches those. The hybrid is what makes answers cite-able.

## What MiniMax embo-01 embeddings look like

1536-dim, normalized-ish (values typically in [-0.1, 0.1]). Query embedding is type=`query`, document embedding is type=`db` (with auto-fallback to `db` if `query` fails). On this corpus, 96 chunks embed in ~2-4 seconds — fast enough for our use.

## Smoke test

Query: **"audit frequency AML compliance"** → top hit:

```
#1  [GL3 p.19 §] score=0.776
    "3.12 The audit function should regularly review the AML/CFT Systems
     to ensure effectiveness. The review should include, but not be limited to:
     (a) adequate..."
```

This is **exactly the right chunk** for an audit-planning question. The hybrid search correctly:
- Boosted GL3 (AML guideline) over GL21 (ERM)
- Found the "audit function" section by keyword
- Reranked it above semantically-similar but less-relevant chunks

## Cost & time

| | Value |
|---|---|
| Total crawl time | 188 seconds |
| Embedding time | ~30 seconds (most of the 188s is downloads + text extraction) |
| Embedding cost (MiniMax) | Free (with M-series key) |
| Storage | 58 MB on disk |
| Search latency | <50ms for hybrid search across 3.5k chunks |

## What changed in `.env`

Two things:
- `EMBEDDING_PROVIDER=minimax` (not `openai`)
- `MINIMAX_BASE_URL=https://api.minimaxi.com/v1` (not `minimax.com` — that host doesn't exist)
- `MINIMAX_EMBEDDING_MODEL=embo-01` (1536-dim)
- `EMBEDDING_DIM=1536`

The MiniMax embedding endpoint is **not** OpenAI-compatible — it expects `texts` (not `input`) and a `type` field (`db` or `query`). I built a direct HTTP client instead of going through the openai SDK, because the SDK returns a misleading "Connection error" when the payload format is wrong.

## What's next (Step 3)

1. **CLI Q&A tool** — `python -m src.ask "your question"` → answer + citations
2. **LLM client** for MiniMax M3 (chat completions, which ARE OpenAI-compatible)
3. **Prompt templates** for the two main use cases (audit planning listing, control extraction)
4. **Test the full Q&A loop** with 5-10 real auditor questions
5. **Streamlit UI** wrapping the CLI

## Files in the repo

```
src/
├── config.py        # env loading, paths, model names
├── crawler_index.py # IA HK page parser
├── crawler_pdf.py   # PDF downloader + text extractor
├── crawl.py         # full ingest orchestrator
├── db.py            # SQLite + sqlite-vec + FTS5 schema
└── embed.py         # chunking + section detection + MiniMax embeddings
```

## Known limitations

1. **Repealed guidelines (GL1, GL2, GL7) are not ingested** — they have no PDF URLs. That's intentional, but if you want them kept as "history", I can add a stub row.
2. **GL15 sub-doc Word file** — the `.docx` file from the GL15 page wasn't downloaded. The IA HK page has a Word file link that needs different handling. Not blocking; we got the main PDF + 60 others.
3. **No section detection on appendices/tables** — sections are detected by regex. Appendices labeled "Appendix A" should work but the regex set could be expanded if you find misses.
4. **No incremental re-ingest** — re-running `crawl.py` will re-process everything. Cheap (188s) but I should add `--changed-only` to skip unchanged files. Easy follow-up.
