# Regulatory Compliance Q&A

Internal Audit tool to query the Insurance Authority (IA) Hong Kong guidelines using natural language.

## What this does

1. **Crawls** the IA HK guidelines page and downloads all guidelines (PDFs) to `data/pdfs/IA/`.
2. **Extracts** text from each PDF and stores structured chunks in a local SQLite database (`data/regulatory.db`), with vector embeddings for semantic search.
3. **Answers** natural-language questions about the guidelines using a configurable LLM, with citations back to the source PDF, section, and page.

## Use cases

- **Audit planning** — "List all required audits and their frequency from the IA guidelines."
- **Audit execution** — "What controls are expected for AML customer due diligence on politically exposed persons?"
- **Cross-guideline comparison** — "Compare the cybersecurity requirements in GL20 with the outsourcing requirements in GL14."

Every answer comes with source citations (guideline code, section, page) so the auditor can verify against the original PDF.

## Quick start

```bash
# 1. Clone the repo
git clone https://github.com/timetochilltoo/regcompliance.git
cd regcompliance

# 2. Set up Python 3.12 (macOS)
brew install python@3.12

# 3. Create venv and install dependencies
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Configure providers
cp .env.example .env
# Edit .env and set at least:
#   LLM_PROVIDER=minimax
#   MINIMAX_API_KEY=sk-cp-...
#   EMBEDDING_PROVIDER=minimax  (or openai/jina)
#   OPENAI_API_KEY=sk-...       (if using OpenAI for embeddings)

# 5. Crawl the IA HK guidelines (~3-5 min, one-time cost ~$0.07 if using OpenAI)
.venv/bin/python -m src.crawl

# 6. Ask a question from the CLI
.venv/bin/python -m src.ask "What is the required audit frequency for AML compliance?"

# 7. Launch the chat UI
.venv/bin/streamlit run src/app.py
# Then open http://localhost:8501 in a browser
```

## Use the system

### CLI Q&A

```bash
# Auto-pick prompt template from question
.venv/bin/python -m src.ask "What is the audit frequency for AML?"

# Force a specific template
.venv/bin/python -m src.ask --template control_extraction "What controls for CDD?"

# Get JSON output (for scripting)
.venv/bin/python -m src.ask --json "Compare GL15 and GL16" | jq '.answer'

# Tag the audit log with a user name
.venv/bin/python -m src.ask --user "Patrick" "Your question"

# Don't write to audit log (for testing)
.venv/bin/python -m src.ask --no-log "experimental question"
```

### Streamlit UI

```bash
.venv/bin/streamlit run src/app.py
# Opens http://localhost:8501
```

Features:
- Chat input box with 4 prompt templates (auto-detect or force one)
- Top-K slider for retrieval (3-10 chunks)
- Source citations as expandable cards with PDF links
- Recent queries sidebar (read from `query_log` table)
- Download answer as Markdown
- All questions logged to the audit trail

## Switching embedding providers

The system supports three embedding providers. Default is OpenAI (industry standard, ~$0.07 one-time, requires VPN in HK).

```bash
# Edit .env:
#   EMBEDDING_PROVIDER=openai   # or minimax, or jina
#   OPENAI_API_KEY=sk-...

# Re-embed (one-time, 30-60 sec)
.venv/bin/python -m src.crawl --reembed-only

# (Optional) Compare providers on the same 10 test queries
.venv/bin/python -m src.benchmark
```

| Provider | Model | Cost | VPN in HK? | Notes |
|---|---|---|---|---|
| OpenAI (default) | text-embedding-3-small | $0.02/1M tokens | Yes | Industry standard |
| MiniMax | embo-01 | Free with M-series key | No | Direct HTTP, not OpenAI SDK |
| Jina | jina-embeddings-v3 | Free tier 1M tokens/month | No | Multilingual, task-aware |

## Refreshing the database

When IA HK publishes new or updated guidelines:

```bash
# Full refresh: re-download PDFs, re-extract, re-chunk, re-embed
.venv/bin/python -m src.crawl

# Re-embed only (if you only changed EMBEDDING_PROVIDER)
.venv/bin/python -m src.crawl --reembed-only

# Wipe everything and start fresh
.venv/bin/python -m src.crawl --reingest
```

The crawler is idempotent — re-running it skips unchanged PDFs and stores new versions alongside old ones (so past audits remain citeable).

## Project layout

```
RegulatoryCompliance/
├── .venv/                      # Python virtual environment (not in git)
├── data/
│   ├── regulatory.db           # SQLite + sqlite-vec + FTS5 database (not in git)
│   └── pdfs/IA/                # Downloaded PDFs (not in git)
├── logs/                       # Query audit log (not in git)
├── reports/                    # Step-by-step findings
├── docs/
│   └── PROJECT_SPEC.md         # Full project spec for agent handoff
├── src/
│   ├── config.py               # All configuration (paths, providers, models)
│   ├── crawler_index.py        # Parses the IA HK guidelines index page
│   ├── crawler_pdf.py          # Downloads PDFs and extracts text (pdfplumber)
│   ├── crawl.py                # Full ingest pipeline
│   ├── db.py                   # SQLite + sqlite-vec + FTS5 schema, hybrid search
│   ├── embed.py                # Section-aware chunking + multi-provider embeddings
│   ├── llm.py                  # Multi-provider chat completions (MiniMax/OpenAI/etc.)
│   ├── prompts.py              # 4 prompt templates + auto-router
│   ├── ask.py                  # CLI Q&A entry point
│   ├── benchmark.py            # Provider comparison harness
│   └── app.py                  # Streamlit chat UI
├── .env.example                # Template for API keys
├── .gitignore
├── requirements.txt
└── README.md
```

## What to back up

The "source of truth" for regulatory content is IA HK's website. Everything else is derived. To back up:

| What | Where | Size | How to back up |
|---|---|---|---|
| Code | this folder, minus the items below | ~100 KB | git push (or zip) |
| Downloaded PDFs | `data/pdfs/IA/` | ~30–50 MB | zip, or sync to iCloud/Dropbox |
| SQLite database | `data/regulatory.db` | ~29 MB | copy the file |
| Query audit log | inside `data/regulatory.db` (`query_log` table) | grows | included in the DB backup |

**Minimum viable backup:** `git push` for the code. Re-running the crawler rebuilds everything else.

**Laptop-loss-proof backup:** put the project folder in iCloud Drive or Dropbox. The `.gitignore` keeps secrets (`.env`) and the venv out of git, everything else syncs automatically.

## Cost estimate

| Component | Cost |
|---|---|
| Crawler (run once or on refresh) | $0 |
| Local storage | $0 |
| Embeddings (one-time, 62 PDFs) | $0 with MiniMax embo-01; ~$0.07 with OpenAI |
| LLM (per question, ~2k tokens in + 1k out) | $0.001–$0.01 with MiniMax M3 |
| **Estimated monthly (200 questions)** | **$0–$2** with MiniMax |

## Deployment to a VPS (when sharing with the team)

For a team of 3+ auditors, deploy to a small Linux VPS:

```bash
# On a fresh Ubuntu 22.04 VPS (Hetzner/DigitalOcean, $5-10/month, Singapore or Japan for HK latency):
sudo apt install -y python3.12 python3.12-venv nginx
git clone https://github.com/timetochilltoo/regcompliance.git
cd regcompliance
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # then edit

# Run the app
.venv/bin/streamlit run src/app.py --server.address 0.0.0.0 --server.port 8501

# For HTTPS + a real domain, add nginx reverse proxy + Let's Encrypt cert.
# See docs/PROJECT_SPEC.md "Setup from a clean machine" for full steps.
```

## Status

- [x] **Step 1** — Project scaffold + crawler + PDF extraction
- [x] **Step 2** — Database (SQLite + sqlite-vec + FTS5) + multi-provider embeddings + hybrid retrieval
- [x] **Step 3** — CLI Q&A tool (`src/ask.py`) with 4 prompt templates
- [x] **Step 4** — Streamlit UI (`src/app.py`) with chat, citations, audit log
- [ ] **Step 5** (optional) — Formal eval harness with 50+ labeled Q&A pairs
- [ ] **Step 6** (optional) — Dockerfile + nginx config for one-line VPS deploy
- [ ] **Step 7** (optional) — SFC and HKMA guideline crawlers

See `reports/` for detailed step-by-step findings and `docs/PROJECT_SPEC.md` for the full project specification.
