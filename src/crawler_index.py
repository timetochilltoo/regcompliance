"""
IA HK guidelines index scraper.

Parses the guidelines page and returns a list of guideline descriptors:

    [
        {
            "code": "GL3",
            "title": "Guideline on Anti-Money Laundering and Counter-Terrorist Financing",
            "url": "https://www.ia.org.hk/en/legislative_framework/files/GL3_ENG_202505.pdf",
            "is_repealed": False,
            "sub_documents": [
                {"title": "Q&A on GL3", "url": "..."},
                ...
            ],
        },
        ...
    ]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import GUIDELINES_INDEX_URL, HTTP_USER_AGENT


@dataclass
class SubDocument:
    title: str
    url: str


@dataclass
class Guideline:
    code: str  # e.g. "GL3", "GL16"
    title: str
    url: Optional[str]  # None for repealed guidelines
    is_repealed: bool
    sub_documents: List[SubDocument] = field(default_factory=list)
    version_label: str = ""  # e.g. "effective from 31 March 2026"

    @property
    def slug(self) -> str:
        # Filesystem-safe slug
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.code)


def fetch_index(url: str = GUIDELINES_INDEX_URL) -> List[Guideline]:
    """Download and parse the IA HK guidelines index page."""
    resp = requests.get(url, headers={"User-Agent": HTTP_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return parse_index_html(resp.text, base_url=url)


def parse_index_html(html: str, base_url: str) -> List[Guideline]:
    """
    Parse the index HTML and return Guideline records.

    The page structure (as of 2026-06):
        <table class="table guideline">
            <tbody>
                <tr>
                    <td data-title="Guidelines">
                        <a href="./files/GL3_ENG_202505.pdf">GL3: Guideline on ...</a>
                        <div class="subitem"><ul>
                            <li><a href="...">Questions and Answers on ...</a></li>
                        </ul></div>
                    </td>
                </tr>
            </tbody>
        </table>

    Repealed guidelines have a plain text row with no <a> for the PDF.
    """
    soup = BeautifulSoup(html, "lxml")
    guidelines: List[Guideline] = []

    table = soup.select_one("table.guideline")
    if table is None:
        raise RuntimeError("Could not find the guidelines table in the page")

    for row in table.select("tbody > tr"):
        cell = row.select_one("td[data-title='Guidelines']")
        if cell is None:
            continue

        # The main guideline title is the first text in the cell, prefixed by
        # the code ("GL3: Guideline on ...")
        # Strip the leading code to keep the title clean.
        # Look for the first link (the PDF), or fall back to text-only (repealed).
        first_link = cell.find("a", href=True)

        if first_link is None or first_link.get("href", "").endswith("/files/"):
            # No real PDF link — this is a repealed guideline like GL1, GL2, GL7
            text = cell.get_text(" ", strip=True)
            code, title = _split_code_title(text)
            guidelines.append(
                Guideline(
                    code=code,
                    title=title,
                    url=None,
                    is_repealed=True,
                )
            )
            continue

        main_href = first_link["href"]
        main_url = urljoin(base_url, main_href)
        main_text = first_link.get_text(" ", strip=True)
        code, title = _split_code_title(main_text)
        version_label = _extract_version_label(cell.get_text(" ", strip=True))

        # Collect sub-documents (Q&As, FAQs, Interpretation Notes, templates)
        sub_docs: List[SubDocument] = []
        subitem = cell.select_one("div.subitem")
        if subitem is not None:
            for li in subitem.select("li"):
                a = li.find("a", href=True)
                if a is None:
                    continue
                sub_href = a["href"]
                sub_url = urljoin(base_url, sub_href)
                sub_title = a.get_text(" ", strip=True)
                # Skip duplicates of the main PDF
                if sub_url == main_url:
                    continue
                sub_docs.append(SubDocument(title=sub_title, url=sub_url))

        guidelines.append(
            Guideline(
                code=code,
                title=title,
                url=main_url,
                is_repealed=False,
                sub_documents=sub_docs,
                version_label=version_label,
            )
        )

    return guidelines


def _split_code_title(text: str) -> tuple[str, str]:
    """
    "GL3: Guideline on Anti-Money Laundering ..." -> ("GL3", "Guideline on Anti-Money Laundering ...")
    """
    # Look for the code at the very start
    m = re.match(r"^\s*(GL[A-Za-z0-9]+)\s*[:\-\u2013\u2014]?\s*(.*)$", text)
    if m:
        return m.group(1).upper(), m.group(2).strip()
    # Fall back: take the first token as code
    parts = text.split(":", 1)
    if len(parts) == 2:
        return parts[0].strip().upper(), parts[1].strip()
    return "UNKNOWN", text.strip()


def _extract_version_label(text: str) -> str:
    """Pull the 'effective from YYYY' or 'effective until YYYY' phrase if present."""
    m = re.search(r"effective\s+(from|until)\s+[0-9]{1,2}\s+\w+\s+[0-9]{4}", text)
    if m:
        return m.group(0)
    return ""


# CLI smoke test
if __name__ == "__main__":
    guidelines = fetch_index()
    print(f"Found {len(guidelines)} guidelines on the page")
    active = [g for g in guidelines if not g.is_repealed]
    repealed = [g for g in guidelines if g.is_repealed]
    print(f"  Active:    {len(active)}")
    print(f"  Repealed:  {len(repealed)}")
    sub_count = sum(len(g.sub_documents) for g in guidelines)
    print(f"  Sub-docs:  {sub_count}")
    print()
    print("First 5 active guidelines:")
    for g in active[:5]:
        print(f"  {g.code}: {g.title[:60]}")
        print(f"    URL: {g.url}")
        print(f"    Version: {g.version_label or '(unspecified)'}")
        for sd in g.sub_documents:
            print(f"      - sub: {sd.title[:60]}")
