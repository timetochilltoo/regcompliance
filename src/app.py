"""
Streamlit UI for the Regulatory Compliance Q&A system.

Run with:
    .venv/bin/streamlit run src/app.py

Then open http://localhost:8501 in a browser.

Features:
- Chat input box
- 4 template buttons (auto / audit_planning / control_extraction / cross_guideline / general)
- Top-K slider (3-10)
- Streaming answer
- Source citations as expandable cards
- "Open source PDF" link per source
- Query history sidebar (read from query_log table)
- Download answer as Markdown
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# Make sure we can import src.* from the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from src.config import (
    DB_PATH, EMBEDDING_PROVIDER, LLM_PROVIDER, LOG_DIR,
)
from src.db import connect, hybrid_search
from src.embed import embed_query
from src.llm import chat
from src.prompts import TEMPLATES, SYSTEM_PROMPT, pick_template

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Regulatory Compliance Q&A",
    page_icon="\U0001F4D6",  # book emoji
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

if "history" not in st.session_state:
    # List of {question, answer, sources, template, model, ts}
    st.session_state.history = []
if "user_name" not in st.session_state:
    st.session_state.user_name = "Patrick"
if "template_choice" not in st.session_state:
    st.session_state.template_choice = "auto"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_recent_queries(limit: int = 20) -> list[dict]:
    """Read recent rows from query_log."""
    try:
        with connect() as conn:
            rows = list(conn.execute(
                """
                SELECT id, asked_at, user, question, llm_provider, llm_model,
                       latency_ms, substr(answer, 1, 120) as answer_preview
                FROM query_log
                ORDER BY asked_at DESC
                LIMIT ?
                """,
                (limit,),
            ))
        return [dict(r) for r in rows]
    except Exception as e:
        return []


def format_source_card(s: dict, idx: int) -> str:
    """Markdown for one source citation card."""
    loc = f"**{s['code']}**, p. {s['page']}"
    if s.get("section"):
        loc += f" \u00a7{s['section']}"
    if s.get("heading"):
        loc += f" \u2014 *{s['heading']}*"
    score = f"score: {s['score']:.3f}" if s.get("score") is not None else ""
    return f"{idx}. {loc}  \n   {score}"


def find_pdf_for_guideline(code: str) -> str | None:
    """
    Find the local PDF path for a given guideline code.

    Prefers the main guideline PDF (no sub-document suffixes like "Q&A" or
    "FAQ") over sub-document variants. Returns the path relative to the
    project root, or None if not found.
    """
    pdf_dir = _PROJECT_ROOT / "data" / "pdfs" / "IA"
    if not pdf_dir.exists():
        return None
    candidates = sorted(pdf_dir.glob(f"{code}_*.pdf"))
    if not candidates:
        return None
    # Prefer the shortest filename (usually the main guideline, not a sub-doc)
    # Sub-documents typically have extra suffixes like "Q&A", "FAQ", "FAQs"
    # which make the filename longer.
    main = min(candidates, key=lambda p: len(p.name))
    return str(main.relative_to(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("\U0001F4D6 Settings")

    st.session_state.user_name = st.text_input(
        "Your name (for audit log)",
        value=st.session_state.user_name,
        help="Logged with every question so we can trace who asked what.",
    )

    st.divider()

    st.subheader("Prompt template")
    template_options = {
        "auto": "Auto-detect from question",
        "audit_planning": "Audit planning (list audits + frequency)",
        "control_extraction": "Control extraction (what to test)",
        "cross_guideline": "Cross-guideline comparison",
        "general": "General Q&A",
    }
    st.session_state.template_choice = st.radio(
        "Choose how to format the answer",
        options=list(template_options.keys()),
        format_func=lambda k: template_options[k],
        index=list(template_options.keys()).index(st.session_state.template_choice),
        help="Auto-detect picks the right template from your question's keywords.",
    )

    st.divider()

    st.subheader("Retrieval")
    top_k = st.slider("Chunks to retrieve", min_value=3, max_value=10, value=5,
                      help="More chunks = better recall, slower LLM call.")

    st.divider()

    st.subheader("System info")
    st.markdown(f"- **Embedding:** `{EMBEDDING_PROVIDER}`")
    st.markdown(f"- **LLM:** `{LLM_PROVIDER}`")
    st.markdown(f"- **DB size:** `{Path(DB_PATH).stat().st_size / 1024 / 1024:.1f} MB`")
    if Path(DB_PATH).exists():
        from src.db import stats as db_stats
        s = db_stats()
        st.markdown(f"- **Guidelines:** {s['guidelines']}")
        st.markdown(f"- **Chunks:** {s['chunks']}")
        st.markdown(f"- **Vectors:** {s['vec_chunks']}")

    st.divider()

    st.subheader("Recent queries")
    recent = get_recent_queries(limit=15)
    if recent:
        for r in recent:
            with st.expander(f"{r['asked_at'][:16]} \u2014 {r['user']}",
                             expanded=False):
                st.markdown(f"**Q:** {r['question']}")
                st.markdown(f"**A:** {r['answer_preview']}...")
                st.caption(f"Model: {r['llm_provider']}/{r['llm_model']}  |  "
                           f"Latency: {r['latency_ms']}ms")
    else:
        st.caption("No queries logged yet.")


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("Regulatory Compliance Q&A")
st.caption(
    "Ask questions about the Insurance Authority (IA) Hong Kong guidelines. "
    "Every answer cites the source PDF, section, and page. "
    "Verify any citation against the original document before relying on it."
)

# Question input
question = st.text_area(
    "Your question",
    height=100,
    placeholder="e.g. What is the required audit frequency for AML compliance?",
    help="Plain English. The system will pick the right template automatically "
         "(or use the sidebar override).",
)

col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    submit = st.button("Ask", type="primary", use_container_width=True)
with col2:
    clear = st.button("Clear history", use_container_width=True)

if clear:
    st.session_state.history = []
    st.rerun()

if submit and question.strip():
    template = (None if st.session_state.template_choice == "auto"
                else st.session_state.template_choice)

    # Auto-detect if requested
    if template is None:
        template = pick_template(question)

    with st.status(f"Working on your question (template: {template})...", expanded=True) as status_box:
        st.write("1. Embedding your question...")
        t0 = time.time()
        qvec = embed_query(question)
        st.write(f"   Done in {int((time.time()-t0)*1000)}ms")

        st.write(f"2. Hybrid search (top_k={top_k})...")
        t1 = time.time()
        with connect() as conn:
            chunks = hybrid_search(conn, question, qvec, top_k=top_k)
        st.write(f"   Found {len(chunks)} chunks in {int((time.time()-t1)*1000)}ms")
        for c in chunks[:3]:
            st.caption(f"   - {c['code']} p.{c['page_number']} score={c['hybrid_score']:.3f}")

        st.write(f"3. Calling LLM ({LLM_PROVIDER})...")
        t2 = time.time()
        messages = TEMPLATES[template](question, chunks)
        resp = chat(messages, temperature=0.2)
        st.write(f"   Done in {resp.get('latency_ms', 0)}ms "
                 f"({resp.get('input_tokens', '?')} in, {resp.get('output_tokens', '?')} out)")
        status_box.update(label="Done", state="complete")

    # Display the answer
    st.divider()
    st.subheader("Answer")
    answer_text = resp.get("text", "")
    st.markdown(answer_text)

    # Sources
    st.divider()
    st.subheader(f"Sources ({len(chunks)})")
    sources_for_history = []
    for i, c in enumerate(chunks, start=1):
        s = {
            "code": c["code"],
            "page": c["page_number"],
            "section": c.get("section_number", ""),
            "heading": c.get("section_heading", ""),
            "score": c.get("hybrid_score", 0),
        }
        sources_for_history.append(s)
        with st.expander(f"[{i}] {s['code']} p.{s['page']} \u2014 "
                         f"{s.get('heading') or '(no heading)'}",
                         expanded=(i == 1)):
            st.markdown(format_source_card(s, i))
            pdf_path = find_pdf_for_guideline(s["code"])
            if pdf_path:
                st.caption(f"Source PDF: `{pdf_path}`  (open in Preview, navigate to p.{s['page']})")
            st.markdown("**Chunk text:**")
            st.text(c["text"][:1000] + ("..." if len(c["text"]) > 1000 else ""))

    # Save to session history
    st.session_state.history.append({
        "question": question,
        "answer": answer_text,
        "sources": sources_for_history,
        "template": template,
        "model": resp.get("model", ""),
        "provider": resp.get("provider", ""),
        "ts": datetime.now().isoformat(timespec="seconds"),
    })

    # Download as Markdown
    st.divider()
    md = f"""# Question\n\n{question}\n\n# Answer\n\n{answer_text}\n\n# Sources\n\n"""
    for s in sources_for_history:
        md += f"- **{s['code']}** p.{s['page']}"
        if s.get("section"):
            md += f" \u00a7{s['section']}"
        if s.get("heading"):
            md += f" ({s['heading']})"
        md += "\n"
    md += f"\n---\n*Generated by Regulatory Compliance Q&A ({resp.get('provider')}/{resp.get('model')}) at {datetime.now().isoformat()}*\n"
    st.download_button(
        label="Download answer as Markdown",
        data=md,
        file_name=f"regulatory_qa_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
        mime="text/markdown",
    )


# Session history (this browser session only)
if st.session_state.history:
    st.divider()
    st.subheader("This session")
    for i, h in enumerate(reversed(st.session_state.history[-10:]), start=1):
        with st.expander(f"Q{i}: {h['question'][:80]}...", expanded=False):
            st.markdown(f"**Question:** {h['question']}")
            st.markdown(f"**Answer:**\n\n{h['answer']}")
            st.caption(f"Template: {h['template']}  |  Model: {h['provider']}/{h['model']}  |  "
                       f"Asked at: {h['ts']}")
