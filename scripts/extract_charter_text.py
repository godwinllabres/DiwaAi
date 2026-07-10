"""Regenerate docs/citizens_charter_text.txt from the charter PDF.

Extracts every page of docs/citizens-charter-2026-edition.pdf into the
page-marked format api/charter_rag.py indexes:

    ----- PAGE <n> -----
    <page text>

Usage:  python scripts/extract_charter_text.py
Requires:  pip install pypdf
"""
from __future__ import annotations

import os

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(ROOT, "docs", "citizens-charter-2026-edition.pdf")
OUT = os.path.join(ROOT, "docs", "citizens_charter_text.txt")


def main() -> None:
    reader = PdfReader(PDF)
    lines: list[str] = []
    empty = 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            empty += 1
        lines.append(f"----- PAGE {number} -----")
        lines.append(text)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"{len(reader.pages)} pages -> {OUT} ({empty} empty)")


if __name__ == "__main__":
    main()
