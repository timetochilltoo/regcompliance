# Regulatory Compliance Q&A — Project Specification

**Version:** 2.2 (Step 3 CLI Q&A + Step 4 Streamlit UI complete)
**Date:** 2026-06-12
**Audience:** Future agents, including Mavis with a fresh context window, picking up this project cold.
**Repository:** https://github.com/timetochilltoo/regcompliance
**Project root:** `/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance/`
**Owner:** Patrick (Internal Audit, Hong Kong)

---

## 1. What this project is

A tool for **Patrick's Internal Audit team** to query the **Insurance Authority (IA) Hong Kong guidelines** using natural language. Two halves:

1. **Crawler** — downloads IA HK guideline PDFs, extracts text, chunks with section metadata, embeds via a **swap-able embedding provider** (OpenAI / MiniMax / Jina), stores in SQLite.
2. **Q&A app** — auditor asks a question in plain English, gets an LLM-generated answer with citations (guideline code, section, page) to the source PDF.

**Use cases:**
- **Audit planning:** "List all required audits and their frequency from the IA guidelines."
- **Audit execution:** "What controls are expected for AML customer due diligence on PEPs?"
- **Cross-guideline comparison:** "Compare underwriting requirements in GL15 vs GL16."

Every answer MUST cite the source. No exceptions — this is for an audit team and citations are non-negotiable for defensibility.

**Embedding providers are swappable.** The default in `.env.example` is OpenAI's `text-embedding-3-small` (industry standard, ~$0.07 one-time, requires VPN in HK). MiniMax `embo-01` (no VPN, free with M-series key) and Jina `jina-embeddings-v3` (free tier 1M tokens/month, multilingual) are drop-in alternatives. Switching is a one-line `.env` change + `python -m src.crawl --reembed-only`. The retrieval pipeline is provider-agnostic; use `python -m src.benchmark` to compare providers on the same 10 test queries. See §5.3.

---

## 2. Current state (what's done)

| Step | Status | Output |
|---|---|---|
| 1. Project scaffold | ✅ | `.venv` (Python 3.12), `requirements.txt`, `.gitignore`, `.env.example` |
| 2. IA HK index parser | ✅ | `src/crawler_index.py` — parses 37 entries (34 active + 3 repealed) |
| 3. PDF downloader + extractor | ✅ | `src/crawler_pdf.py` — pdfplumber, 0 OCR needed |
| 4. Database schema | ✅ | `src/db.py` — SQLite + sqlite-vec + FTS5 + section metadata + query log |
| 5. Section-aware chunker | ✅ | `src/embed.py` — extracts `section_number` + `section_heading` per chunk |
| 6. Multi-provider embeddings | ✅ | `src/embed.py` — registry pattern; OpenAI / MiniMax / Jina. Switch via `.env` |
| 7. Hybrid retriever | ✅ | `db.hybrid_search()` — vector (0.5) + FTS (0.3) + title boost (0.15) + heading boost (0.05) |
| 8. Full ingest of 62 PDFs | ✅ | 3,513 chunks, 3,513 vectors, 58 MB on disk |
| 9. Provider benchmark harness | ✅ | `src/benchmark.py` — 10 test queries, side-by-side comparison |
| 10. `--reembed-only` mode | ✅ | `python -m src.crawl --reembed-only` — re-embeds without re-downloading PDFs |
| 11. CLI Q&A tool | ✅ | `src/ask.py` — `python -m src.ask "question"`. Auto-picks prompt template, returns cited answer |
| 12. LLM chat client | ✅ | `src/llm.py` — provider dispatcher (MiniMax / OpenAI / Anthropic / DeepSeek) |
| 13. Prompt templates | ✅ | `src/prompts.py` — audit_planning, control_extraction, cross_guideline, general |
| 14. Query audit log | ✅ | Every CLI question logged to `query_log` table (user, chunks, model, answer, latency) |
| 15. Streamlit UI | ✅ | `src/app.py` — chat box, template buttons, source citations, history sidebar |
| 16. GitHub repo + keychain auth | ✅ | https://github.com/timetochilltoo/regcompliance |

**What's NOT done yet:**
- CLI Q&A tool (`src/ask.py` and `src/llm.py`)
- Prompt templates (`src/prompts.py`)
- LLM client for MiniMax M3 chat (`src/llm.py`) — chat endpoint IS OpenAI-compatible, easy
- Streamlit UI (`src/app.py`)
- Audit log integration (table exists, no UI/logging yet)
- Backup script (just docs, no automation)

**Side-by-side embedding storage: NOT used.** We picked Option A (single active provider, destructive re-embed on dim change) because re-embedding 3.5k chunks takes 30-60 sec — keeping both providers' vectors side-by-side in separate `vec_chunks_*` tables would double DB complexity for no real benefit at our scale. The `benchmark.py` script lets you compare providers without committing to keeping both.

---

## 3. Architecture overview

```
┌────────────────────────────────────────────────────────────────┐
│  PHASE 1: CRAWLER (one-time + on-demand refresh)               │
│                                                                │
│  IA HK index page                                              │
│      ↓                                                         │
│  crawler_index.py (BeautifulSoup)                              │
│      ↓ list of {code, title, url, sub_docs, version_label}     │
│  crawler_pdf.py (pdfplumber)                                   │
│      ↓ PDFs to data/pdfs/IA/, text per page                    │
│  embed.py (chunk_pages)                                        │
│      ↓ 3513 chunks with section metadata                       │
│  embed.py (MiniMax embo-01, direct HTTP)                       │
│      ↓ 1536-dim vectors                                        │
│  db.py (SQLite + sqlite-vec + FTS5)                            │
└────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────┐
│  PHASE 2: Q&A (pending implementation)                         │
│                                                                │
│  Auditor question                                              │
│      ↓                                                         │
│  Hybrid retrieval:                                             │
│    - Vector search (sqlite-vec cosine)  → top-K                │
│    - FTS5 BM25 keyword search           → top-K                │
│    - Merge with weights + boosts + dedup                       │
│      ↓ top-5 chunks                                            │
│  LLM call (MiniMax M3 chat, OpenAI-compatible)                 │
│      ↓                                                         │
│  Answer + citations                                            │
│  Logged to query_log table                                     │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. File-by-file reference

### `requirements.txt`
```
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
python-dotenv>=1.0.0
pdfplumber>=0.10.0
pypdf>=4.0.0
sqlite-vec>=0.1.0
numpy>=1.24.0
openai>=1.30.0
anthropic>=0.30.0
streamlit>=1.35.0
```

### `.env` (in `.gitignore`, must be created by user)
```bash
# LLM (answer generation) — MiniMax is HK-accessible
LLM_PROVIDER=minimax
MINIMAX_API_KEY=<user's key>
MINIMAX_MODEL=MiniMax-M3
MINIMAX_BASE_URL=https://api.minimaxi.com/v1   # NOTE: minimaxi.com, NOT minimax.com

# Embedding (vector search) — default is OpenAI, requires VPN in HK
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
OPENAI_API_KEY=<user's OpenAI key>

# Alternative: switch to MiniMax embo-01 (no VPN)
# EMBEDDING_PROVIDER=minimax
# MINIMAX_EMBEDDING_MODEL=embo-01

# Alternative: Jina (free tier 1M tokens/month, multilingual)
# EMBEDDING_PROVIDER=jina
# JINA_API_KEY=<user's Jina key>
# JINA_EMBEDDING_MODEL=jina-embeddings-v3
# JINA_EMBEDDING_DIM=1024
```

Only fill in the keys for providers you've actually selected. See `.env.example` for the full annotated template.

### `.env.example` (committed, template for users)
Same as above with empty key fields.

### `src/config.py`
- Loads `.env` at import time via `python-dotenv`
- Defines all paths (`PROJECT_ROOT`, `DATA_DIR`, `PDF_DIR`, `DB_PATH`, `LOG_DIR`, `REPORT_DIR`)
- Defines all model names and provider selection
- Defines chunking parameters (`CHUNK_SIZE=1000`, `CHUNK_OVERLAP=200`)
- Defines retrieval parameters (`TOP_K_CHUNKS=8`, vec/fts weights)
- **Crawler config:** `GUIDELINES_INDEX_URL`, `CRAWL_DELAY_SEC=1.0`, `HTTP_USER_AGENT`

### `src/crawler_index.py`
- `fetch_index()` → `requests.get()` + `BeautifulSoup` parse of IA HK page
- Returns `List[Guideline]` dataclass with: `code`, `title`, `url`, `is_repealed`, `sub_documents[]`, `version_label`
- **Page structure to parse:** `<table class="table guideline">` with `<tr><td data-title="Guidelines">` rows
- **Repealed detection:** row has no `<a href>` (text-only) → marked `is_repealed=True`
- **Sub-doc detection:** look for `<div class="subitem"><ul><li><a>` inside the cell

### `src/crawler_pdf.py`
- `local_path_for(code, url, title, sub_label, version_label)` → human-readable filename
  - Format: `{code}_{slug_from_title}[_{sub_label}][_v{YYYY-MM-DD}[-prev]].pdf`
  - e.g. `GL3_Guideline_on_Anti-Money_Laundering.pdf`, `GL16_..._v2026-03-31.pdf`, `GL14_..._Q&A.pdf`
- `download_pdf(url, dest)` → streams PDF, skips if already exists
- `extract_text(pdf_path)` → returns `ExtractedPdf` with pages_text list (one string per page)
  - Tries `pdfplumber` first (best for tables), falls back to `pypdf`
- `download_and_extract_guideline(guideline, sub_label, sub_url)` → orchestrates the two
- `ExtractedPdf` dataclass: `code`, `title`, `source_url`, `pdf_path`, `sha256`, `page_count`, `pages_text`, `extraction_engine`, `extraction_warnings`

### `src/db.py`
- `connect()` context manager → loads sqlite-vec, sets WAL + foreign keys, row_factory
- `init_db()` → creates schema (idempotent)
- `upsert_guideline()` → inserts/updates by `source_url`
- `insert_chunks()` → wipes old chunks for guideline, inserts new
- `vector_search(query_embedding, top_k)` → sqlite-vec cosine, returns chunks with distance
- `fts_search(query, top_k)` → FTS5 BM25, returns chunks with fts_score
- `hybrid_search(query, query_embedding, top_k, vec_weight=0.5, fts_weight=0.3, title_boost=0.15, heading_boost=0.05)` → merged & reranked
- `log_query()` → records Q&A to audit log
- `stats()` → DB summary for sanity checks

**Schema highlights:**
- `guidelines` table: id, code, title, source_url (UNIQUE), local_path, sha256, page_count, file_bytes, is_main, parent_code, version_label, is_repealed, extraction_engine, extraction_warnings, crawled_at, updated_at
- `chunks` table: id, guideline_id (FK), chunk_index, page_number, char_start, char_end, text, section_number, section_heading, char_count
- `chunks_fts` FTS5 virtual table, kept in sync via triggers
- `vec_chunks` sqlite-vec virtual table, float[1536], cosine distance
- `query_log` table: id, asked_at, user, question, retrieved_chunks (JSON), llm_provider, llm_model, answer, latency_ms

### `src/embed.py`
- `Chunk` dataclass: chunk_index, page_number, char_start, char_end, text, section_number, section_heading
- `chunk_pages(pages_text, chunk_size=1000, overlap=200)` → List[Chunk]
  - Walks pages sequentially
  - Overlapping windows snapped to paragraph breaks > sentence ends > whitespace
  - **Section detection** via regex on a 2000-char window before each chunk's start
  - Patterns: numbered (`1.2.3 Title`), `§/Section/Article N`, `GL3-5.3`, `Chapter N`
- `_detect_section_at(page_text, char_offset)` → (section_number, section_heading)
- **Embedding provider registry** — `OpenAIProvider`, `MiniMaxProvider`, `JinaProvider`
  - All implement the same `EmbeddingProvider` interface (name, dim, embed_texts, embed_query)
  - To add a new provider: write a class with those 4 things + register in `_PROVIDERS` dict + add config block to `src/config.py`
- `get_provider()` → returns the configured provider instance
- `list_providers()` → names of all registered providers (for CLI help)
- `embed_texts(texts)` → dispatches to current provider, validates dim matches DB
- `embed_query(text)` → single query embed; each provider uses its preferred query format
  - OpenAI: just `embed_texts([text])[0]`
  - MiniMax: uses `type=query` with auto-fallback to `type=db`
  - Jina: uses `task=retrieval.query` (better quality than task-agnostic)

### `src/crawl.py`
- `run(limit, reingest)` → end-to-end ingest pipeline (download + extract + chunk + embed + store)
- `reembed_all()` → re-embed all existing chunks with the current provider (no re-download, no re-chunking)
- `ingest_one(conn, guideline, sub_label, sub_url)` → download + extract + chunk + embed + store for one PDF
- `main()` → CLI entry point with flags:
  - `--limit N` — process only the first N guidelines
  - `--reingest` — wipe everything and start fresh
  - `--reembed-only` — re-embed existing chunks with the current provider
  - `--verbose` — debug logging
- Logs to `logs/crawl.log` AND stdout

**Run:**
```bash
.venv/bin/python -m src.crawl              # full pipeline
.venv/bin/python -m src.crawl --reembed-only   # just re-embed (provider swap)
```

### `src/benchmark.py`
- 10 hand-picked test queries covering the main audit use cases (audit frequency, controls, outsourcing, cybersecurity, ERM, CPD, underwriting, governance, record-keeping, capital)
- For each query: a list of `(guideline_code, page)` tuples considered "relevant"
- Metrics: `recall_at_5` (fraction of relevant guidelines found in top-5), `top1_accuracy` (was #1 hit relevant), `avg_first_relevant_rank`
- `python -m src.benchmark` → re-embeds with each configured provider, runs queries, prints comparison table
- `python -m src.benchmark --query-only` → skip re-embed, just test current provider (30 sec)
- Results written to `reports/benchmark_results.json`

**Run:** `.venv/bin/python -m src.benchmark [--providers openai minimax jina] [--query-only]`

**When to use:** once when first picking a provider, and again any time you change providers / chunking / retrieval weights. NOT meant for every CI run — takes ~2-3 min per provider due to re-embed.

### `src/llm.py`
- Chat completions client with provider dispatcher
- Supports: MiniMax, OpenAI, Anthropic, DeepSeek
- MiniMax, OpenAI, DeepSeek all use the `openai` Python SDK (chat endpoints are OpenAI-compatible)
- Anthropic uses the `anthropic` SDK (different message format — handled internally)
- Public API: `chat(messages, temperature=0.2) -> {text, model, provider, input_tokens, output_tokens, latency_ms}`
- Choose provider via `LLM_PROVIDER` env var; choose model via `<PROVIDER>_MODEL` env var

### `src/prompts.py`
- `SYSTEM_PROMPT` — auditor's assistant persona, enforces citation discipline and honesty ("say I don't know")
- 4 task-specific templates:
  - `format_audit_planning()` — returns markdown table with columns: Audit Area, Frequency, Source, Notes
  - `format_control_extraction()` — lists each control with description + citation + testing objective
  - `format_cross_guideline()` — per-guideline summary + comparison + auditor recommendation
  - `format_general()` — concise 1-3 paragraph answer with citations
- `pick_template(question)` — keyword router, picks the right template from the question text
- `TEMPLATES` — dict mapping template name to its format function

### `src/ask.py`
- CLI Q&A entry point: `python -m src.ask "your question"`
- Pipeline: embed query → hybrid search (top-5 chunks) → pick template → call LLM → print answer + sources → log to query_log
- Flags:
  - `--template T` — force a specific template (skip auto-router)
  - `--top-k N` — number of chunks to retrieve (default 5)
  - `--user NAME` — tag the audit log
  - `--no-log` — don't write to query_log
  - `--json` — output as JSON
  - `--verbose` — include token counts and timing
  - `--quiet` — suppress info logging
- Latency: 25-90s per question (mostly LLM generation time)
- All output written to `query_log` table for the audit trail

**Run:** `.venv/bin/python -m src.ask "What is the required audit frequency for AML compliance?"`

### `src/app.py` (Streamlit UI)
- Chat-style web interface for the audit team
- Sidebar:
  - User name input (defaults to "Patrick")
  - 4 template buttons (Audit planning, Control extraction, Cross-guideline, Free-form)
  - Top-K slider (3-10 chunks)
  - Recent queries from `query_log`
- Main area:
  - Question input box
  - Submit button
  - Streaming answer display (chunk-by-chunk as LLM generates)
  - Source citations as expandable cards (guideline, page, section, score)
  - "Open source PDF" link per source
  - "Copy answer" / "Download as Markdown" buttons
- Run with: `.venv/bin/streamlit run src/app.py`
- Default port: 8501. To expose to network: add `--server.address 0.0.0.0`

### Not yet written
- `src/eval.py` — formal evaluation harness with 50+ labeled Q&A pairs
- (Optional) `Dockerfile` + `docker-compose.yml` for one-line deploy
- (Optional) nginx reverse-proxy config for HTTPS on a VPS
- (Optional) `--max-tokens` flag on `ask.py` for faster responses
- (Optional) multi-turn conversation support (currently each ask is stateless)

---

## 5. Multi-provider embedding design

### Why multiple providers?

Three reasons:
1. **Hong Kong + OpenAI = VPN friction.** OpenAI's `text-embedding-3-small` is the industry standard but needs VPN. The workflow is: connect VPN → re-embed corpus (30-60 sec) → disconnect VPN → use system normally. Acceptable for a one-time cost, but having a no-VPN fallback (MiniMax) is convenient.
2. **Unknown which model is best for regulatory text.** OpenAI is the safe default, but MiniMax `embo-01` was specifically trained on Chinese + English (IA HK is bilingual) — it might actually be better. Jina's `jina-embeddings-v3` is the strongest multilingual model available. Without testing on our actual content, we're guessing.
3. **Benchmarking without rewriting the app.** If a future LLM provider adds a better embedding model, dropping it in is a 50-line code change (one new class, one entry in `_PROVIDERS`).

### The three supported providers

| Provider | Model | Dim | Cost | VPN in HK? | Notes |
|---|---|---|---|---|---|
| **OpenAI** (default) | `text-embedding-3-small` | 1536 | $0.02/1M tokens (~$0.07 one-time for our corpus) | Yes | Industry standard; well-tested |
| **MiniMax** | `embo-01` | 1536 | Free with M-series key | No | Direct HTTP, NOT OpenAI SDK (see §7) |
| **Jina** | `jina-embeddings-v3` | 1024 | Free 1M tokens/month; $0.02/1M after | No | Multilingual, task-aware (retrieval.passage / retrieval.query) |

### How to switch providers

```bash
# 1. Edit .env — set EMBEDDING_PROVIDER (and provider-specific key)
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...

# 2. Re-embed (one-time, 30-60 sec, requires VPN for OpenAI)
python -m src.crawl --reembed-only

# 3. (Optional) Compare against other providers
python -m src.benchmark
```

The system handles dim changes automatically — `init_db()` detects when `vec_chunks` was created with a different `EMBEDDING_DIM` than the current config, drops the old table, and recreates it empty. Re-embedding then fills it back up. **This is destructive for the current provider's vectors** but they're regeneratable from the same `.env` in 30-60 sec.

### Why we did NOT use side-by-side provider storage

Considered a `vec_chunks_openai` / `vec_chunks_minimax` design where both providers' vectors live simultaneously. Rejected because:

- Re-embedding 3,500 chunks takes 30-60 sec. The "cost" of switching is negligible.
- Side-by-side doubles DB complexity (two tables, two embed pipelines, pick-one-at-query-time logic).
- The benchmark script already lets you compare providers without committing to keeping both.
- At 1M+ chunks we'd revisit this. At 3.5k it's overkill.

### What a future agent should NOT change

- The `EmbeddingProvider` interface shape (`name`, `dim`, `embed_texts`, `embed_query`) — adding a new provider means implementing this exactly, not extending it.
- The `EMBEDDING_DIM` env var — must match the active provider's model dim, or `embed_texts()` raises and the corpus is unusable until re-embedded.
- The chunking/retrieval logic is provider-agnostic. Do NOT add provider-specific logic to `db.hybrid_search()` or `embed.chunk_pages()`.

### 5.1 Other key technical decisions (non-embedding)

| Decision | Choice | Rationale |
|---|---|---|
| Storage | SQLite + sqlite-vec | Single file, zero infra, 58 MB fits the whole corpus, vector search works |
| Chunk size | 1000 chars / 200 overlap | Regulatory text is dense; this size keeps individual sections intact while giving the LLM enough context |
| Retrieval | Hybrid (vector + FTS5 + boosts) | Vector misses exact section numbers; FTS5 misses paraphrases; both together catch citations and semantics |
| Vector dim | Provider-dependent (1536 for OpenAI/MiniMax, 1024 for Jina) | Set in `.env` as `EMBEDDING_DIM`; sqlite-vec table is auto-recreated on dim change |
| Embedding provider | Swappable via `.env` (OpenAI / MiniMax / Jina) | One-time cost per corpus; re-embed in 30-60 sec when switching. Default OpenAI for quality; MiniMax for HK accessibility. |
| Provider side-by-side storage | Single active provider (Option A) | Re-embed cost is trivial at 3.5k chunks; keeping both adds DB complexity. Benchmark script lets us compare without committing to both. |
| Frontend | Streamlit | Pure Python, no JS/HTML needed, single command to run |
| PDF extraction | pdfplumber (pypdf fallback) | Best table handling for regulatory control matrices |
| Section detection | Regex on each chunk's preceding window | Cheap, no LLM call, ~85% recall on common IA HK formats |
| Crawler politeness | 1-second delay between downloads | Respectful to IA HK; trivial cost |
| Versioning | Keep both versions of a guideline (current + previous) | Past audits must cite the version in force at audit time |

---

## 6. Setup from a clean machine

```bash
# 1. Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Install Python 3.12 (system Python doesn't have sqlite-vec support)
brew install python@3.12

# 3. Clone the repo
cd "/Users/patrickshi/Documents/Minimax Coding"
git clone https://github.com/timetochilltoo/regcompliance.git RegulatoryCompliance
cd RegulatoryCompliance

# 4. Create venv and install deps
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 5. Set up git credential helper
git config --global credential.helper osxkeychain
git config --global user.name "Patrick"

# 6. Create .env from template
cp .env.example .env
# Edit .env and fill in MINIMAX_API_KEY=... and LLM_PROVIDER=minimax

# 7. Run the full ingest (3-5 minutes)
.venv/bin/python -m src.crawl

# 8. Verify
.venv/bin/python -c "from src.db import stats; import json; print(json.dumps(stats(), indent=2, default=str))"
```

**Expected output of step 8:** 62+ guidelines, 3500+ chunks, 3500+ vectors.

**Backup of local data (not in git):**
```bash
# Zip everything except venv and .env
cd "/Users/patrickshi/Documents/Minimax Coding"
tar --exclude='RegulatoryCompliance/.venv' --exclude='RegulatoryCompliance/.env' \
    -czf regulatory_compliance_backup.tar.gz RegulatoryCompliance/
# Or sync to iCloud/Dropbox
```

---

## 7. ⚠️ MiniMax API gotchas (READ THIS BEFORE TOUCHING LLM/EMBED CODE)

This is the section that took an hour to debug. Future agents: don't relearn it.

### 7.1 Embeddings endpoint is NOT OpenAI-compatible

The MiniMax M-series chat endpoint is OpenAI-API-compatible. **The embeddings endpoint is NOT.**

**Wrong (will fail with misleading "Connection error"):**
```python
from openai import OpenAI
client = OpenAI(api_key=KEY, base_url="https://api.minimaxi.com/v1")
client.embeddings.create(model="embo-01", input=["text"])  # ❌ WRONG PAYLOAD
```

**Right (use direct HTTP):**
```python
import requests

r = requests.post(
    "https://api.minimaxi.com/v1/embeddings",
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    json={
        "model": "embo-01",
        "texts": ["text here"],   # NOT "input"
        "type": "db"               # "db" for documents, "query" for queries
    },
    timeout=60
)
r.raise_for_status()
data = r.json()
if data["base_resp"]["status_code"] != 0:
    raise RuntimeError(data)
vectors = data["vectors"]  # List[List[float]], 1536-dim
```

**Why this matters:** the openai SDK call returns `openai.APIConnectionError: Connection error` for the wrong payload. This looks like a network problem but is actually a payload format problem. Don't waste time debugging network/firewall/VPN.

### 7.2 Base URL is `minimaxi.com`, not `minimax.com`

`https://api.minimax.com/v1` → connection refused (host doesn't exist)
`https://api.minimaxi.com/v1` → 401 (live, needs valid key)
`https://api.MiniMax.com/v1` → connection refused (also doesn't exist)

**Always use `https://api.minimaxi.com/v1`**. The endpoint path is `/embeddings` for embeddings, `/chat/completions` for chat.

### 7.3 Embedding type: `db` vs `query`

- `type=db` — for document chunks being stored
- `type=query` — for user queries
- The two are different models internally (or different prefixes); using the wrong one gives worse retrieval
- If `type=query` fails, **fall back to `type=db`** — `query` is a newer/optional endpoint and may not be available on all keys/tiers
- The implementation in `src/embed.py::embed_query()` already handles this fallback

### 7.4 Chat endpoint IS OpenAI-compatible

For the chat (answer generation) endpoint, the openai SDK works fine:

```python
from openai import OpenAI
client = OpenAI(api_key=KEY, base_url="https://api.minimaxi.com/v1")
resp = client.chat.completions.create(
    model="MiniMax-M3",
    messages=[{"role": "user", "content": "..."}]
)
```

Use this pattern when building `src/llm.py`. Same key, same base URL, just a different endpoint and the openai SDK.

### 7.5 Other things to know

- **Hong Kong user + OpenAI = need VPN.** Don't suggest OpenAI as a default fallback for Patrick. Use MiniMax, DeepSeek, or another Chinese provider.
- **API key is for a Chinese product.** Sometimes the dashboard gives a "login fail" error if the key doesn't have the right product enabled (e.g. you have chat access but not embedding access). Check the dashboard for the right product.
- **Rate limits:** unspecified but our batched approach (96 chunks/request) seems to work fine for our scale. If you hit 429s, lower the batch size in `src/embed.py::embed_texts()`.

---

## 8. Step-by-step plan to complete the project

### Step 3 — CLI Q&A + LLM client ✅ DONE

**Files created:**
1. `src/llm.py` — chat completions dispatcher (MiniMax, OpenAI, Anthropic, DeepSeek)
2. `src/prompts.py` — `SYSTEM_PROMPT` + 4 templates (audit_planning, control_extraction, cross_guideline, general) + auto-router
3. `src/ask.py` — CLI entry point with full audit logging

**Smoke test results (commit `b9d81bc`):**
- Q1 "audit frequency AML" → audit_planning template, returns GL3 §3.12-3.13 with risk-based table
- Q2 "CDD controls on PEPs" → honest "I don't have info" + closest chunks
- Q3 "Cybersecurity controls GL20" → 9 controls, each with citation + testing objective

Latency: 25-90s per question. All 3 questions logged to `query_log` table.

### Step 4 — Streamlit UI ✅ DONE

**Files created:**
- `src/app.py` — full chat UI

**Features:**
- Chat input with 4 template buttons (auto-detect or force one)
- Top-K slider (3-10)
- Source citations as expandable cards with PDF links
- Query history sidebar (from `query_log` table)
- Download answer as Markdown
- Streaming output (so users see the answer being generated)

**Run:** `cd /Users/patrickshi/Documents/Minimax\ Coding/RegulatoryCompliance && .venv/bin/streamlit run src/app.py`

**Deployment options** (when sharing with the team):
- **Local only:** run on Patrick's laptop, just for him
- **Shared VM:** $5-10/mo Hetzner/DigitalOcean, expose on 0.0.0.0:8501
- **nginx + HTTPS:** standard reverse proxy for a public URL
- See `docs/PROJECT_SPEC.md` Section "How to deploy to a VPS" (TODO: write this)

### Step 5 — Future work (optional)

- `src/eval.py` — formal evaluation harness with 50+ labeled Q&A pairs
- `Dockerfile` for one-line deploy to any cloud
- Multi-turn conversation support (currently each `ask` is stateless)
- Conversation history sidebar in the UI
- SFC / HKMA crawlers (originally out of scope, but easy to add — same pattern)

---

## 9. Known issues / TODO list

| Issue | Severity | Notes |
|---|---|---|
| GL1, GL2, GL7 (repealed) not ingested | Low | Intentional. Can add as stub rows if Patrick wants |
| GL15 sub-doc Word file not downloaded | Low | The IA HK page has a `.docx` link. Would need python-docx to extract text |
| Section detection regex misses some formats | Medium | Easy fix: expand `_SECTION_PATTERNS` in `embed.py` if Patrick finds misses |
| No `crawl --changed-only` mode | Low | Re-running takes 3 min currently, fine for now |
| No scheduled refresh | Low | Patrick can run `python -m src.crawl` monthly, or we add a cron |
| No SFC / HKMA support | Out of scope | Original plan deferred this to "later" |
| `.docx` not supported | Low | Add `python-docx` if needed |
| No authentication on Streamlit | Low | Add basic auth or deploy behind VPN if needed |
| No streaming in CLI output | Cosmetic | Print answer all at once is fine for now |

---

## 10. Where to find what

- **Code:** https://github.com/timetochilltoo/regcompliance
- **Project root:** `/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance/`
- **Downloaded PDFs:** `data/pdfs/IA/` (29 MB, 62 files)
- **SQLite database:** `data/regulatory.db` (29 MB, single file)
- **Query audit log:** SQLite `query_log` table (empty until CLI Q&A is built)
- **Crawl logs:** `logs/crawl.log`
- **Step reports:** `reports/step1_findings.md`, `reports/step2_findings.md`
- **This spec:** `docs/PROJECT_SPEC.md` (or wherever it's saved)

---

## 11. Contact / handoff notes

**Original spec source:** Patrick walked me through the requirements verbally. The full design discussion is in conversation history, but the key points are:
- Hong Kong-based internal audit team
- Budget: very low (~$5-20/mo LLM cost)
- Initial scope: IA HK guidelines only (extend to SFC/HKMA later)
- Use cases: audit planning (list required audits + frequency) and audit execution (control extraction)
- Strong preference for detailed specs before implementation
- User explicitly wants to be asked before cutting features, not assumed
- The hybrid retrieval architecture was Patrick's idea — keep it, don't simplify

**If you're a new agent and you need to ask Patrick something:** he responds well to direct questions with concrete options. He doesn't want technical jargon. He values "give me a recommendation, not a buffet." He has a Galaxy S24 Ultra and lives in Hong Kong (UTC+8).

**If something is unclear:** read the conversation history in this project's git commit messages — they explain the why, not just the what.

**If you break something:** the git history is clean. Every commit is small and self-contained. `git log --oneline` to see the progression, `git revert <commit>` to undo safely.

---

*End of spec. Last updated: 2026-06-12, after Step 3 (CLI Q&A) and Step 4 (Streamlit UI) completion. Project is feature-complete for solo use. Optional next steps: formal eval harness, Dockerfile, multi-turn chat, SFC/HKMA crawlers.*
