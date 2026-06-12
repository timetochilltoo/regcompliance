# Step 3 — CLI Q&A Tool

**Date:** 2026-06-12
**Status:** ✅ Complete
**Pushed to:** commit `b9d81bc` on `main`

## TL;DR

You can now ask natural-language questions about the IA HK guidelines from the terminal and get cited answers:

```bash
cd "/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance"
.venv/bin/python -m src.ask "What is the required audit frequency for AML compliance?"
```

**Result (excerpt):**

```
======================================================================
Q: What is the required audit frequency for AML compliance?
   [template: audit_planning, provider: minimax, model: MiniMax-M3]
======================================================================

| Audit Area | Frequency | Source | Notes |
|---|---|---|---|
| AML/CFT Systems review | Regular (no fixed interval) | GL3 §3.12–3.13, p.19 | Risk-based, scaled to ML/TF risk profile |

SOURCES:
  [1] GL3 p.19 §3.12 (score: 0.771)
  [2] GL27 p.7  (score: 0.476)
  [3] GL21 p.1  (score: 0.475)
  ...
```

Every question + answer is automatically written to the `query_log` table for the audit trail.

## What got built

| File | Purpose | Lines |
|---|---|---|
| `src/llm.py` | LLM chat client with provider dispatcher (MiniMax / OpenAI / Anthropic / DeepSeek) | ~180 |
| `src/prompts.py` | 4 prompt templates (audit_planning, control_extraction, cross_guideline, general) + auto-router | ~150 |
| `src/ask.py` | CLI Q&A entry point — hybrid search → prompt → LLM → answer + sources + audit log | ~200 |

## Architecture of one Q&A call

```
User question
    ↓
[prompts.pick_template()] → "audit_planning" | "control_extraction" | "cross_guideline" | "general"
    ↓
[embed.embed_query()] → vector (1536-dim, MiniMax embo-01)
    ↓
[db.hybrid_search()] → top-5 chunks with citations
    ↓
[prompts.{template}()] → messages for LLM (system + user with chunks)
    ↓
[llm.chat()] → answer text (provider: MiniMax M3, latency: ~25-90s)
    ↓
[print formatted output] + [db.log_query()] for audit trail
```

## Smoke test results (3 real auditor questions)

### Q1: Audit frequency for AML
- Template auto-selected: **audit_planning**
- Answer: a markdown table with one row, citing GL3 §3.12-3.13
- LLM behavior: correctly noted that the chunks say "regularly" with risk-based adjustment, NOT a fixed sub-annual frequency
- Latency: 50.7s
- Verdict: ✅ Perfect — defensible, cited, honest about the risk-based nature

### Q2: CDD controls for PEPs
- Template auto-selected: **control_extraction** (matched "controls expected")
- Answer: "I don't have information about specific controls for customer due diligence on politically exposed persons (PEPs) in the indexed guidelines"
- LLM behavior: **exactly what we want** — said it didn't know, quoted the closest relevant chunks (GL3 §4.3.8 about partnerships), and recommended the auditor check the full GL3 text
- Latency: 24.9s (faster, short answer)
- Verdict: ✅ Audit-defensible behavior — would NOT be a hallucinated answer

### Q3: Cybersecurity controls in GL20
- Template auto-selected: **general** (no specific keyword match)
- Answer: 9 detailed controls, each with description + source citation + testing objective
- LLM behavior: structured the answer like an internal audit memo, included "Testing objective" for each control (auditor-actionable)
- Latency: 89.6s (long answer, 9 controls)
- Verdict: ✅ Production-quality — this is what an auditor would actually use

## Key design decisions

### 1. Citations are mandatory
The system prompt explicitly says: "Cite every claim with [GLxx, Section N, p. M]". When the model forgets, the source list at the end of the output makes verification easy.

### 2. Honesty over hallucination
The system prompt says: "Say 'I don't have information' if the answer isn't in the chunks." This was validated by Q2 — better to admit a gap than fabricate a citation.

### 3. Auto-template routing
Four templates, picked by keyword matching. Saves the auditor from having to know the difference between "audit_planning" and "control_extraction" — they just ask in plain English.

### 4. Risk-based distinction
The system prompt tells the model to distinguish "REQUIRES vs. RECOMMENDS vs. PERMITS" — regulatory language is precise, and conflating these would mislead auditors.

### 5. Every query is logged
The `query_log` table captures: user, question, retrieved chunks (with scores), LLM provider + model, answer, total latency. This is the **audit trail** — the system itself is auditable.

## Latency profile

| Question type | Typical latency | Why |
|---|---|---|
| Short answer ("I don't know") | 20-30s | Quick generation, small token count |
| Medium answer (table, 1-2 paragraphs) | 40-60s | Standard generation |
| Long answer (9 controls with details) | 80-90s | Many tokens, model takes longer |

**Optimization options (not done yet):**
- `max_tokens` cap (currently unlimited; cap at 2000 for faster responses)
- Switch to gpt-4o-mini (faster than MiniMax M3, but needs VPN for API)
- Parallel retrieval + embedding (already fast, ~50ms, not the bottleneck)

## Known limitations

| Issue | Severity | Notes |
|---|---|---|
| PEPs/CDD example missed the right chunk | Medium | GL3 has PEPs but they didn't make the top-5. Fix: bump `top_k` to 10, or boost FTS5 weight for regulatory-term-heavy queries |
| MiniMax M3 emits `` tags in output | Cosmetic | The thinking is visible in stdout. Could be stripped or hidden. Not blocking. |
| No streaming output | Cosmetic | Whole answer appears at once. Streamlit UI will add this. |
| No `max_tokens` cap | Low | Long answers can be slow. Trivial to add. |
| No conversation history | Out of scope | Each `ask` is stateless. Streamlit UI will add chat history. |

## Usage examples

```bash
# Auto-pick template
.venv/bin/python -m src.ask "What is the audit frequency for AML?"

# Force a specific template
.venv/bin/python -m src.ask --template control_extraction "What controls for CDD?"

# Get JSON output for scripting
.venv/bin/python -m src.ask --json "Compare GL15 and GL16" | jq '.answer'

# Tag the audit log with a user name
.venv/bin/python -m src.ask --user "Patrick" "What is the IA's view on record keeping?"

# Don't log (for testing)
.venv/bin/python -m src.ask --no-log "experimental question"

# Get more chunks (better recall, slower)
.venv/bin/python -m src.ask --top-k 10 "complex question"
```

## What's next

**Step 4: Streamlit UI** — wraps `ask.py` in a chat interface. The CLI tool is functional for solo use; the UI is what the audit team will actually use day-to-day.

Features for the UI:
- Chat input box
- 4 template buttons (audit planning, control extraction, cross-guideline, free-form)
- Source citations with clickable links to the source PDF
- Query history sidebar (read from `query_log` table)
- Download answer as Markdown / Excel
- Streaming output (so the user sees the answer being generated)

Estimated time: 2-3 hours. Should I keep going?
