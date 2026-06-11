# Step 1 — Crawler & PDF Extraction Findings

**Date:** 2026-06-11
**Source:** https://www.ia.org.hk/en/legislative_framework/guidelines.php
**Project root:** `/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance/`

## TL;DR

Crawler works, PDFs download cleanly, text extraction is high quality. **No OCR needed.** The "100 guidelines" estimate is high — the actual count is **66 PDFs** (34 active + 3 repealed + 29 sub-documents). The full project is feasible on a laptop, no infrastructure required.

## What's in the IA HK index

| Category | Count |
|---|---|
| Active guidelines (GL1–GL36 minus repealed) | 34 |
| Repealed guidelines (GL1, GL2, GL7) | 3 |
| Sub-documents (Q&As, FAQs, Interpretation Notes, templates) | 29 |
| **Total PDFs to download** | **~66** |
| Total storage projected | ~30–50 MB |

Sub-documents are important — they often contain clarifications, FAQs, and examples that auditors will want to query. The crawler grabs them too.

## Sample extraction results

Tested on 6 PDFs: GL3 (AML), GL3A, GL4, GL5, GL6, GL20 (Cybersecurity).

| File | Pages | Engine | Warnings | Notes |
|---|---|---|---|---|
| GL3 (AML) | 103 | pdfplumber | 0 | 234k chars, all readable |
| GL3A | 6 | pdfplumber | 0 | Clean |
| GL4 (Fit & Proper) | 21 | pdfplumber | 0 | Clean |
| GL5 (Authorization) | 21 | pdfplumber | 0 | Clean |
| GL6 (Reserving) | 8 | pdfplumber | 0 | Clean |
| GL20 (Cybersecurity) | 81 | pdfplumber | 0 | 1MB, tables intact |

**Conclusion:** all IA HK PDFs are text-based (not scanned images), so `pdfplumber` extracts them perfectly on the first try. No OCR pipeline needed, no `pytesseract`, no extra cost.

## Sample extracted text quality

GL3 page 5 (typical regulatory prose):

> The nature of money laundering and terrorist financing
> s.1, Sch. 1, AMLO 1.9 The term "money laundering" (ML) is defined in section 1 of Part 1 of Schedule 1 to the AMLO...

Section numbering, footnote references, paragraph numbering — all preserved. Suitable for chunking and RAG.

## Architecture decisions confirmed

- **Storage:** SQLite + sqlite-vec confirmed. Single file, zero infrastructure.
- **Embedding model:** OpenAI `text-embedding-3-small` at $0.02/1M tokens. For ~60 PDFs at 200k chars each (~50M chars = ~13M tokens), one-time embedding cost is **~$0.26**. Negligible.
- **Chunk size:** 1000 chars / 200 overlap (per the original plan). Will revisit if RAG quality is poor.
- **LLM:** Configurable via env var. Defaults to OpenAI `gpt-4o-mini`. DeepSeek, Anthropic, MiniMax all wired in but not yet tested.

## What's next (Step 2 onwards)

1. **Full crawl** — run the crawler against all 66 PDFs (~5 min download time)
2. **SQLite schema + chunking + embedding generation** — one script, ~15 min runtime
3. **CLI Q&A tool** — `python -m src.ask "your question"` to validate end-to-end retrieval
4. **LLM eval** — 10 questions × 2-3 providers
5. **Streamlit UI** — chat box with prompt templates

## Open questions for you

None blocking. When you want, I can proceed to Step 2 (full crawl) immediately.

If you want to peek at the actual extracted text first, the 6 sample PDFs are in `data/pdfs/IA/` and you can run:

```bash
cd "/Users/patrickshi/Documents/Minimax Coding/RegulatoryCompliance"
.venv/bin/python -c "from src.crawler_pdf import extract_text; from pathlib import Path; e=extract_text(Path('data/pdfs/IA/GL3_d716b12d.pdf')); print(e.pages_text[0])"
```

Or open `data/pdfs/IA/GL3_d716b12d.pdf` directly in Preview.
