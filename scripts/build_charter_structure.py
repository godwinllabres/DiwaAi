"""Generate data/charter_structure.json — the charter's page structure.

    python scripts/build_charter_structure.py            # report only
    python scripts/build_charter_structure.py --write    # write the JSON

Why this is a build step and not runtime code: what is on a charter page can
only be recovered from OCR text by heuristics, and heuristics are wrong in ways
you cannot see from inside a chat reply. Deriving the structure here means it is
a reviewable artifact — a diff to read before a new edition ships, and a file to
hand-edit when the OCR mangled a heading — instead of a regex that silently
mislabels a page in production. Same contract as data/intent_sources.json.

A new edition is a re-run, not a code change:

    1. drop the new PDF + page-marked text into docs/
    2. python scripts/build_charter_structure.py --write
    3. read the diff, fix any bad title by editing the JSON directly
    4. update CHARTER_EDITION / CHARTER_PDF_PATH

Derivation rules, and why each one:

  • A service heading is a numbered line ("3. Registration of Continuing
    Students") in the first few lines of a page THAT ALSO CARRIES an
    "Office or Division:" row. The row appears on a procedure's opening page
    and nowhere else, which is what separates a real heading from a numbered
    client step continuing the table on the next page ("5. Fill out
    counselling/" is a step, not a service).
  • A section runs until the next heading or an ALL-CAPS banner. The banner is
    a new chapter or a standalone appendix; without it, the contact tables at
    the back of the document inherit whichever procedure finished last.
  • The printed page number is pdf_page - page_offset, with page_offset the
    mode of (pdf page - footer number) across the document. Measured, not
    assumed, so a new edition with different front matter self-calibrates.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.charter_pages import (  # noqa: E402
	EDITION, DOC_NAME, TEXT_PATH, STRUCTURE_PATH, split_pages,
)

# "3. Registration of Continuing Students (via Portal)"
HEADING_RE = re.compile(r"^(\d{1,2})\.\s+([A-Z].{4,150}?)\s*$")
OFFICE_RE = re.compile(r"Office or Division:\s*([^\n]+)")
FOOTER_RE = re.compile(r"^(\d{1,4})$")
BANNER_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9][A-Z0-9 &'/().,\-]{9,89}$")
# The running header, and the procedure table's column headings repeated on a
# continuation page. Neither opens a section.
NOT_A_BANNER_RE = re.compile(
	r"CITIZEN'?S CHARTER|CLIENT STEPS|AGENCY ACTION|CHECKLIST OF REQUIREMENTS"
	r"|WHERE TO SECURE|FEES TO BE|PROCESSING TIME|PERSON RESPONSIBLE|^TOTAL"
)

HEADING_SCAN_LINES = 3
FOOTER_SCAN_LINES = 4
MAX_TITLE_CHARS = 100


def _tidy(value: str) -> str:
	"""Drop OCR replacement chars and collapse the whitespace they leave."""
	return re.sub(r"\s{2,}", " ", value.replace("�", " ")).strip(" -–—")


def scan(pages):
	"""Per page: {page, title, office, footer, banner}."""
	rows = []
	for pdf_page, body in pages:
		lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
		head = lines[:HEADING_SCAN_LINES]
		title = None
		# The "Office or Division:" row is what makes a numbered line a service
		# heading rather than a client step (see module docstring).
		office_row = OFFICE_RE.search(body)
		if office_row:
			for line in head:
				m = HEADING_RE.match(line)
				if m:
					title = _tidy(m.group(2))[:MAX_TITLE_CHARS].rstrip()
					break
		banner = title is None and any(
			BANNER_RE.match(ln) and not NOT_A_BANNER_RE.search(ln) for ln in head
		)
		footer = None
		for line in reversed(lines[-FOOTER_SCAN_LINES:]):
			m = FOOTER_RE.match(line)
			if m:
				footer = int(m.group(1))
				break
		rows.append({
			"page": pdf_page, "title": title or None,
			"office": _tidy(office_row.group(1)) if office_row else None,
			"footer": footer, "banner": banner,
		})
	return rows


def measure_offset(rows) -> tuple[int, int]:
	"""(page_offset, printed_from) — printed page = pdf page - page_offset,
	valid from the first pdf page where that arithmetic is positive."""
	counts = Counter(r["page"] - r["footer"] for r in rows if r["footer"] is not None)
	if not counts:
		return 0, 1
	offset, agreeing = counts.most_common(1)[0]
	print(f"  page offset {offset}: {agreeing}/{sum(counts.values())} footers agree", file=sys.stderr)
	for other, n in counts.most_common()[1:6]:
		print(f"    (dissenting offset {other}: {n} page(s) — front matter/TOC leaders)", file=sys.stderr)
	return offset, offset + 1


def build_sections(rows) -> list[dict]:
	"""Compress the per-page scan into contiguous {pages, title, office} runs."""
	sections: list[dict] = []
	current = None
	for row in rows:
		if row["title"]:
			current = {
				"pages": [row["page"], row["page"]],
				"title": row["title"],
				"office": row["office"],
			}
			sections.append(current)
		elif row["banner"]:
			current = None            # a new chapter/appendix — inherit nothing
		elif current is not None:
			current["pages"][1] = row["page"]
			if row["office"] and not current["office"]:
				current["office"] = row["office"]
	return sections


def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument("--write", action="store_true", help="write data/charter_structure.json")
	args = ap.parse_args()

	with open(TEXT_PATH, encoding="utf-8") as fh:
		pages = split_pages(fh.read())
	print(f"charter text: {len(pages)} pages from {TEXT_PATH}", file=sys.stderr)

	rows = scan(pages)
	offset, printed_from = measure_offset(rows)
	sections = build_sections(rows)

	covered = sum(s["pages"][1] - s["pages"][0] + 1 for s in sections)
	print(f"  {len(sections)} sections covering {covered}/{len(pages)} pages "
	      f"({covered / len(pages):.1%})", file=sys.stderr)
	print(f"  {sum(1 for s in sections if not s['office'])} section(s) with no office",
	      file=sys.stderr)
	longest = max(sections, key=lambda s: s["pages"][1] - s["pages"][0], default=None)
	if longest:
		span = longest["pages"][1] - longest["pages"][0] + 1
		print(f"  longest section: {span} pages — {longest['title'][:60]!r} "
		      f"(review if this looks wrong)", file=sys.stderr)

	doc = {
		"_README": (
			"Generated by scripts/build_charter_structure.py — but hand-edits are "
			"expected and preserved by review, not by the generator. Fix a bad "
			"title here rather than in the code. printed page = pdf page - "
			"page_offset, for pdf pages >= printed_from. 'pages' is an inclusive "
			"[first, last] range of PDF page numbers."
		),
		"document": DOC_NAME,
		"edition": EDITION,
		"pdf_pages": len(pages),
		"page_offset": offset,
		"printed_from": printed_from,
		"sections": sections,
	}

	if not args.write:
		print("\n-- sample --", file=sys.stderr)
		for s in sections[:3] + sections[-3:]:
			print(f"  pp.{s['pages'][0]}-{s['pages'][1]}  {s['title'][:56]!r}  {s['office']}",
			      file=sys.stderr)
		print("\n(dry run — pass --write to save)", file=sys.stderr)
		return 0

	with open(STRUCTURE_PATH, "w", encoding="utf-8") as fh:
		json.dump(doc, fh, ensure_ascii=False, indent=1)
		fh.write("\n")
	print(f"\nwrote {STRUCTURE_PATH}", file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
