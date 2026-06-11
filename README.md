# Regulatory Compliance Q&A

Internal Audit tool to query the Insurance Authority (IA) Hong Kong guidelines using natural language.

## What this does

1. **Crawls** the IA HK guidelines page and downloads all guidelines (PDFs) to `data/pdfs/IA/`.
2. **Extracts** text from each PDF and stores structured chunks in a local SQLite database (`data/regulatory.db`).
3. **Answers** natural-language questions about the guidelines using a configurable LLM, with citations back to the source PDF and page.

## Use cases

- **Audit planning** — "List all required audits and their frequency from the IA guidelines."
- **Audit execution** — "What controls are expected for AML customer due diligence?"
- **Gap analysis** — "What does GL16 say about underwriting long-term insurance policies other than Class C?"

Every answer comes with source citations (guideline code, section, page) so the auditor can verify.

## Quick start

```bash
# 1. Set up the virtual environment (one time)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Configure LLM provider
cp .env.example .env
# Edit .env and add your API key (OpenAI / Anthropic / DeepSeek / MiniMax)

# 3. Crawl the IA HK guidelines
.venv/bin/python -m src.crawl

# 4. Ask a question (CLI)
.venv/bin/python -m src.ask "What is the required audit frequency for AML compliance?"

# 5. Launch the chat UI
.venv/bin/streamlit run src/app.py
```

## Project layout

```
RegulatoryCompliance/
├── .venv/                      # Python virtual environment (not in git)
├── data/
│   ├── regulatory.db           # SQLite database (not in git — can be regenerated)
│   └── pdfs/IA/                # Downloaded PDFs (not in git — can be re-downloaded)
├── logs/                       # Audit log of questions asked (not in git)
├── reports/                    # Step-by-step findings
├── src/
│   ├── config.py               # All configuration (paths, LLM provider, models)
│   ├── crawler_index.py        # Parses the IA HK guidelines index page
│   ├── crawler_pdf.py          # Downloads PDFs and extracts text
│   ├── crawl.py                # CLI entry point: run the full crawl
│   ├── db.py                   # SQLite schema + sqlite-vec setup
│   ├── embed.py                # Embedding generation (chunk → vector)
│   ├── llm.py                  # Configurable LLM client (OpenAI / Anthropic / etc.)
│   ├── ask.py                  # CLI Q&A entry point
│   ├── prompts.py              # Prompt templates
│   └── app.py                  # Streamlit chat UI
├── .env.example                # Template for API keys
├── .gitignore
├── requirements.txt
└── README.md
```

## Storage layout — what to back up

The "source of truth" for the regulatory content is IA HK's website. Everything else is derived. To back up the whole project:

| What | Where | Size | How to back up |
|---|---|---|---|
| Code | this folder, minus the items below | ~100 KB | git push (or zip) |
| Downloaded PDFs | `data/pdfs/IA/` | ~30–50 MB | zip, or sync to iCloud/Dropbox |
| SQLite database | `data/regulatory.db` | ~20 MB | copy the file |
| Query audit log | `logs/` | grows over time | zip |

**Minimum viable backup:** git push the code. Re-running the crawler rebuilds everything else.

**Laptop-loss-proof backup:** put the project folder in iCloud Drive or Dropbox. The `.gitignore` will keep secrets (.env) and the venv out of git, but everything else syncs automatically.

## LLM provider

Configurable via environment variables in `.env`. Default is OpenAI. Supported:

- `openai` — OpenAI API (gpt-4o-mini default)
- `deepseek` — DeepSeek API (OpenAI-compatible, set `OPENAI_BASE_URL=https://api.deepseek.com`)
- `anthropic` — Anthropic Claude (set `LLM_PROVIDER=anthropic`)
- `minimax` — MiniMax M2.7/M3 (set `LLM_PROVIDER=minimax` + endpoint)

See `src/config.py` for all options.

## Refreshing the database

IA HK updates guidelines periodically. To pick up new or updated guidelines:

```bash
.venv/bin/python -m src.crawl --refresh
```

The crawler compares SHA-256 hashes of existing files against the live page. New files get downloaded, new versions get stored alongside the old (so historical audits remain citeable).

## Cost estimate

| Component | Cost |
|---|---|
| Crawler (run once or on refresh) | $0 |
| Local storage | $0 |
| Embeddings (one-time, ~66 PDFs) | ~$0.26 with OpenAI text-embedding-3-small |
| LLM (per question, ~2k tokens) | $0.001–$0.01 with gpt-4o-mini |
| **Estimated monthly (200 questions)** | **$5–20** |

## Status

- [x] **Step 1** — Project scaffold + crawler + PDF extraction (smoke test on 6 PDFs passed)
- [ ] **Step 2** — Full crawl of all 66 IA HK PDFs
- [ ] **Step 3** — SQLite + chunking + embedding
- [ ] **Step 4** — CLI Q&A
- [ ] **Step 5** — LLM evaluation across providers
- [ ] **Step 6** — Streamlit UI

See `reports/` for detailed step-by-step findings.
