# Step 4 — Streamlit UI

**Date:** 2026-06-12
**Status:** ✅ Complete
**Pushed to:** commit `62cd6f1` on `main`

## TL;DR

The audit team now has a **browser-based chat interface** for the Q&A system. Launch with one command, use it like ChatGPT, every question is logged.

```bash
.venv/bin/streamlit run src/app.py
# Opens http://localhost:8501
```

## What's in the UI

### Sidebar
- **Your name** input → tagged in the audit log on every question
- **Prompt template** radio (5 options):
  - Auto-detect from question
  - Audit planning (table output)
  - Control extraction (list + testing objective)
  - Cross-guideline comparison
  - General Q&A
- **Top-K slider** (3-10 chunks) — more chunks = better recall, slower
- **System info panel** — current embedding provider, LLM provider, DB size, chunk count
- **Recent queries** (last 15) — read from `query_log` table

### Main area
- **Question input** (multi-line text area)
- **Ask** / **Clear history** buttons
- **Status panel** during processing — shows each pipeline step with timing:
  1. Embedding the question (~50ms)
  2. Hybrid search (~50ms)
  3. LLM call (25-90s)
- **Answer** rendered as markdown
- **Sources** as expandable cards — one per chunk, with:
  - Citation: GLxx, p. M, §N, heading
  - Hybrid score
  - Path to the source PDF (open in Preview to verify)
  - Full chunk text (so auditor can scan without leaving the UI)
- **Download as Markdown** button — saves the full Q&A as a `.md` file
- **This session** history — last 10 questions, expandable

## What it looks like

```
┌─────────────────────────────────────────────────────────────────────┐
│ 📖 Settings              │ Regulatory Compliance Q&A                │
│ ─────────────────        │ Ask questions about the IA HK guidelines │
│ Your name: Patrick       │                                         │
│                          │ ┌────────────────────────────────────┐  │
│ Prompt template          │ │ Your question                      │  │
│ ○ Auto-detect            │ │ What is the audit frequency for AML?│  │
│ ● Audit planning         │ └────────────────────────────────────┘  │
│ ○ Control extraction     │ [Ask]              [Clear history]      │
│ ○ Cross-guideline        │                                         │
│ ○ General                │ ─────────────────────────────────────── │
│                          │ Answer                                 │
│ Chunks: 5                │ ┌────────────────────────────────────┐ │
│                          │ │ | Audit Area | Frequency | Source |│ │
│ ─────────────────        │ │ | AML/CFT    | Regular   | GL3 §3.12│ │
│ Embedding: minimax       │ └────────────────────────────────────┘ │
│ LLM: minimax             │                                         │
│ Guidelines: 62           │ ─────────────────────────────────────── │
│ Chunks: 3513             │ Sources (5)                            │
│ Vectors: 3513            │ ▼ [1] GL3 p.19 §3.12 (score: 0.771)     │
│                          │   Source PDF: data/pdfs/IA/GL3_...pdf   │
│ ─────────────────        │   [chunk text preview]                  │
│ Recent queries           │ ▼ [2] GL27 p.7  (score: 0.476)         │
│ ▼ 2026-06-12 — Patrick   │   ...                                  │
│   Q: What is the audit.. │                                         │
└─────────────────────────────────────────────────────────────────────┘
```

## How to run

### Local (just for Patrick)
```bash
cd "/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance"
.venv/bin/streamlit run src/app.py
# Browser opens at http://localhost:8501
```

### Shared (for the audit team, future)
```bash
# On a VPS:
.venv/bin/streamlit run src/app.py --server.address 0.0.0.0 --server.port 8501
# Team visits http://your-vps-ip:8501
```

For HTTPS + a real domain, add nginx reverse proxy + Let's Encrypt cert (standard, ~30 min setup).

## Key UX decisions

### 1. Status panel shows what's happening
LLM calls take 25-90 seconds. A black screen with "Loading..." is anxiety-inducing. The status panel shows each pipeline step in real time so the auditor knows the system is working, not stuck.

### 2. Source cards are first-class
Each source is an **expandable card**, not a tiny footnote. The auditor clicks to see:
- The exact citation
- The full chunk text (truncated to 1000 chars in the preview)
- The path to the source PDF

This is the **audit defensibility** feature: every answer is one click away from the original source.

### 3. Hybrid scores shown
Each source shows its `hybrid_score` (0-1, higher is more relevant). The auditor can judge which sources the LLM weighted most heavily. If the top source has a low score, the LLM was probably guessing — the auditor should verify more carefully.

### 4. Auto-template is the default
Auditors don't have to think about which template to use. The keyword router picks the right one. If they want to force a specific template, the sidebar has them.

### 5. Recent queries in the sidebar
Internal audit often means "I asked something similar last week, what was the answer?" The sidebar shows the last 15 queries, expandable, with answer preview. This is the team's **institutional memory**.

### 6. Download as Markdown
Auditors need to copy answers into working papers. The download button gives them a clean `.md` file with the question, answer, and all citations — ready to paste into Word/Excel/Notion.

## Smoke test (imports + helpers)

The UI module imports cleanly, all helper functions work:

```
app.py imports cleanly
  EMBEDDING_PROVIDER: minimax
  LLM_PROVIDER: minimax
  TEMPLATES: ['audit_planning', 'control_extraction', 'cross_guideline', 'general']

source card rendering: OK
PDF finder (GL3, GL20, GL14): all find the main guideline PDF, not sub-doc variants
query_log reader: returns 3 recent queries
```

Browser-side testing not done in this session (no browser automation available), but the import path and helper functions are all verified.

## Code stats

- **`src/app.py`**: 250 lines
  - 2 helper functions
  - 1 sidebar section
  - 1 main area section
  - Uses the same `embed_query` + `hybrid_search` + `chat` + `TEMPLATES` from the CLI tool — no duplicated logic

## Status — feature complete for solo use

The project is now **feature-complete** for a single auditor (Patrick) using it on his own laptop. What works:

| Capability | Status |
|---|---|
| Download all 62 IA HK guidelines | ✅ |
| Extract text with section metadata | ✅ |
| Embed with MiniMax / OpenAI / Jina (swap with one .env change) | ✅ |
| Hybrid search (vector + keyword + boosts) | ✅ |
| 4 prompt templates for different audit tasks | ✅ |
| CLI Q&A (`python -m src.ask "question"`) | ✅ |
| Streamlit UI (`streamlit run src/app.py`) | ✅ |
| Every question logged to `query_log` | ✅ |
| Compare embedding providers | ✅ |
| Re-embed when provider changes (30-60 sec) | ✅ |
| On GitHub with keychain auth | ✅ |

## What's left (all optional, none blocking)

| Item | Effort | Why you'd do it |
|---|---|---|
| `src/eval.py` (formal eval harness with 50+ Q&A pairs) | 1-2 days | Production-quality scoring; benchmark against other LLM providers |
| `Dockerfile` for one-line VPS deploy | 30 min | When you actually deploy to a shared server |
| nginx + HTTPS config | 30 min | When you want a real domain + cert |
| Multi-turn chat history | 2-3 hours | So the auditor can have a conversation, not just single Q&As |
| SFC / HKMA crawlers | 1-2 days each | Original plan deferred these to "later" |
| Auto-refresh on IA HK updates | 2 hours (cron) | Re-run the crawler monthly so the DB stays current |

## Try it

```bash
cd "/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance"
.venv/bin/streamlit run src/app.py
```

The browser will open automatically. Try asking:
- "What is the required audit frequency for AML compliance?" (auto → audit_planning)
- "What controls are expected for cybersecurity?" (auto → control_extraction)
- "Compare GL15 and GL16" (auto → cross_guideline)

**The project is done for the use case you described.** Anything beyond this is polish.
