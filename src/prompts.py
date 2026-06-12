"""
Prompt templates for the regulatory Q&A system.

Three flavors:
  1. SYSTEM_PROMPT — the auditor's assistant persona, always included
  2. format_audit_planning() — for "list required audits + frequency" questions
  3. format_control_extraction() — for "what controls are expected" questions
  4. format_general() — free-form Q&A

All templates end with "answer the question using ONLY the chunks provided
and cite them" — grounding in the retrieved chunks is mandatory.
"""
from __future__ import annotations

from typing import List


SYSTEM_PROMPT = """\
You are an AI assistant for the Internal Audit team at an insurance company \
in Hong Kong. You help auditors query the Insurance Authority (IA) HK \
guidelines.

Your answers must:
1. Be grounded in the source chunks provided. Do NOT use any outside knowledge.
2. Cite every claim with the format [GLxx, Section N, p. M] or [GLxx, p. M] \
   if no section number is present.
3. Be precise about regulatory terminology — paraphrase nothing.
4. Say "I don't have information about this in the indexed guidelines" if \
   the answer isn't in the chunks.
5. For audit planning questions, structure the answer as a clear table.
6. For control extraction questions, list each control as a separate item \
   with the testing objective.

You are not a lawyer. Your answers are for internal audit planning and \
execution, not legal advice. Auditors will independently verify all \
citations against the source PDFs.
"""


def _format_chunks(chunks: List[dict]) -> str:
    """Format retrieved chunks for inclusion in the LLM prompt."""
    if not chunks:
        return "(No relevant chunks found.)"
    parts = []
    for i, c in enumerate(chunks, start=1):
        loc = f"{c['code']} p.{c['page_number']}"
        if c.get("section_number"):
            loc += f" §{c['section_number']}"
        if c.get("section_heading"):
            loc += f" ({c['section_heading']})"
        parts.append(
            f"[Chunk {i}] {loc}\n{c['text'].strip()}"
        )
    return "\n\n---\n\n".join(parts)


def format_audit_planning(question: str, chunks: List[dict]) -> List[dict]:
    """
    For audit planning questions like "List all required audits and their
    frequency". Returns a markdown table-friendly response.
    """
    user = f"""\
## Source chunks (use ONLY these to answer)

{_format_chunks(chunks)}

## Question

{question}

## Required answer format

Respond in markdown with a single table that has these columns:
| Audit Area | Frequency | Source | Notes |

- "Audit Area": the area or process being audited
- "Frequency": how often the audit must be performed (e.g. "at least annually", "every 2 years", "ad-hoc")
- "Source": the exact citation (e.g. "GL3 §3.12")
- "Notes": any caveats (e.g. "subject to risk-based adjustment")

After the table, add a one-paragraph summary highlighting any audits with \
a frequency shorter than annually (these are higher priority for the audit \
plan).

If no relevant chunks are found, say so explicitly.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def format_control_extraction(question: str, chunks: List[dict]) -> List[dict]:
    """
    For "what controls are expected" type questions.
    Returns a structured list of controls with testing objectives.
    """
    user = f"""\
## Source chunks (use ONLY these to answer)

{_format_chunks(chunks)}

## Question

{question}

## Required answer format

List each control as a separate item in this format:

**Control 1: <short name>** [GLxx §N]
- **Description:** <one-sentence description of the control>
- **Source citation:** GLxx, Section N, p. M
- **Testing objective:** <how an auditor would test whether this control is operating effectively>

Group related controls together (e.g. all CDD controls under one heading). \
End with a one-sentence summary of the overall control expectation.

If the chunks do not contain specific control requirements, say so explicitly \
and quote the closest relevant guidance.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def format_general(question: str, chunks: List[dict]) -> List[dict]:
    """
    Free-form Q&A. Answer concisely, with citations.
    """
    user = f"""\
## Source chunks (use ONLY these to answer)

{_format_chunks(chunks)}

## Question

{question}

## Required answer format

Write a concise answer (1-3 paragraphs) that:
1. Directly addresses the question
2. Cites every claim with [GLxx, §N, p. M] format
3. Distinguishes what the guideline REQUIRES vs. RECOMMENDS vs. PERMITS \
   (regulatory language is precise)
4. Notes any conflicts or ambiguities between guidelines if you spot them

If the chunks do not contain the answer, say so explicitly.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def format_cross_guideline(question: str, chunks: List[dict]) -> List[dict]:
    """
    For questions that span multiple guidelines (e.g. "compare GL15 and GL16").
    """
    user = f"""\
## Source chunks (use ONLY these to answer)

{_format_chunks(chunks)}

## Question

{question}

## Required answer format

Structure your answer as:

1. **Per-guideline summary** — for each relevant guideline, 1-2 sentences \
   describing what it says about the topic
2. **Comparison** — how the guidelines differ, where they agree, and any \
   edge cases that need attention
3. **Recommendation for the auditor** — what to test, in what order

Cite every claim with [GLxx, §N, p. M] format.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# Router: pick the right template based on the question's keywords
def pick_template(question: str) -> str:
    """
    Return one of: 'audit_planning', 'control_extraction', 'cross_guideline', 'general'.
    Used by src/ask.py to auto-select the prompt template.
    """
    q = question.lower()
    if any(k in q for k in [
        "required audit", "audit frequency", "audit plan", "audit schedule",
        "list all audit", "what audit", "how often audit", "annual audit",
        "audit of ", "audit on ", "frequency of audit",
    ]):
        return "audit_planning"
    if any(k in q for k in [
        "control", "expected control", "test procedure", "test objective",
        "what should we test", "what do we need to check", "controls expected",
    ]):
        return "control_extraction"
    if any(k in q for k in [
        "compare", "difference between", " vs ", " versus ",
        "both gl", "between gl",
    ]):
        return "cross_guideline"
    return "general"


TEMPLATES = {
    "audit_planning": format_audit_planning,
    "control_extraction": format_control_extraction,
    "cross_guideline": format_cross_guideline,
    "general": format_general,
}
