"""What a cited Citizens' Charter page actually is — served from a static index.

"p. 997" on its own is unusable to a student: it says nothing about what is on
the page, and 997 is the PDF's page *index*, not the number printed on the page
(the charter's front matter is unnumbered, so the two differ). This module turns
a PDF page number into something the reader can act on — the service that page
documents, the office that owns it, the number printed on it, and a deep link
that opens the PDF at that page:

    CvSU Citizens' Charter, FY 2026 edition, p. 948 — "Registration of
    Continuing Students (via Portal)" (Office of the Campus Registrar)

The structure is READ from data/charter_structure.json, never derived here. The
charter is reissued regularly, and recovering page structure from OCR text takes
heuristics that fail in ways nobody can see from inside a chat reply — a client
step read as a service title, an appendix inheriting the procedure above it.
Generating that file offline (scripts/build_charter_structure.py) makes the
structure a reviewable artifact: a diff to read before an edition ships, and a
file to hand-edit when the OCR mangled a heading. Same contract as
data/intent_sources.json, for the same reason.

So a new edition is a data change, not a code change: regenerate the JSON, read
the diff, point CHARTER_EDITION / CHARTER_PDF_PATH at the new document.

This module also owns page splitting for the whole charter pipeline (charter_rag
imports it), which is what keeps the retrieval tier and the per-intent bindings
citing a page identically.

Env:
    CHARTER_EDITION    — edition label used in citations (default "FY 2026")
    CHARTER_RAG_PATH   — page-marked charter text (shared with charter_rag)
    CHARTER_PDF_PATH   — the PDF served at GET /sources/citizens-charter.pdf
    CHARTER_STRUCTURE  — override the structure file location
    CHARTER_PDF_URL    — public URL of that PDF. Optional: when unset, citations
                         link to the API origin that served the request (see
                         set_request_origin), so deep links work out of the box.
                         Set it to publish the charter somewhere else.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from bisect import bisect_right
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger("diwa.charter_pages")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT_PATH = os.environ.get(
	"CHARTER_RAG_PATH", os.path.join(_ROOT, "docs", "citizens_charter_text.txt")
)
STRUCTURE_PATH = os.environ.get(
	"CHARTER_STRUCTURE", os.path.join(_ROOT, "data", "charter_structure.json")
)
PDF_PATH = os.environ.get(
	"CHARTER_PDF_PATH", os.path.join(_ROOT, "docs", "citizens-charter-2026-edition.pdf")
)
# Where this API serves the PDF (api.app). A citation must carry an ABSOLUTE
# URL — the web app runs on a different origin, so a relative link would
# resolve against the wrong host.
ROUTE_PATH = "/sources/citizens-charter.pdf"
# Explicit override: publish the charter elsewhere, or pin the origin behind a
# proxy that does not forward its own scheme/host.
PDF_URL = os.environ.get("CHARTER_PDF_URL", "").strip()

# Fallbacks only — the structure file carries the real values, so a new edition
# never needs these touched.
DOC_NAME = "CvSU Citizens' Charter"
EDITION = os.environ.get("CHARTER_EDITION", "FY 2026").strip()

# Origin of the request being served, so citations deep-link without any
# configuration. Deliberately request-scoped rather than cached at startup: it
# derives from the request's Host, and a forged Host must not be able to poison
# the links in anyone else's reply — only in the reply to the forged request.
# asyncio.to_thread copies the context, so this survives into the worker thread
# where the chatbot renders the citation.
_request_origin: ContextVar[str] = ContextVar("charter_request_origin", default="")

_PAGE_MARKER_RE = re.compile(r"^----- PAGE (\d+) -----\s*$", re.MULTILINE)


# ── page splitting (shared with charter_rag) ─────────────────────────────────

def clean_text(text: str) -> str:
	"""Normalise OCR text: straight quotes, collapsed runs of whitespace."""
	text = text.replace("’", "'").replace("‘", "'")
	text = re.sub(r"[ \t]+", " ", text)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()


def split_pages(raw: str) -> list[tuple[int, str]]:
	"""Return (page_number, page_text) pairs from the page-marked OCR text."""
	pages: list[tuple[int, str]] = []
	matches = list(_PAGE_MARKER_RE.finditer(raw))
	for i, m in enumerate(matches):
		end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
		body = clean_text(raw[m.end():end])
		if body:
			pages.append((int(m.group(1)), body))
	if not pages:  # no markers — treat the whole file as one page
		body = clean_text(raw)
		if body:
			pages.append((1, body))
	return pages


# ── citations ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PageInfo:
	"""What is on one page of the charter. Fields are None when unknown —
	front matter has no service heading, and an unnumbered page has no printed
	number. A citation degrades one field at a time rather than all at once."""
	pdf_page: int
	printed_page: Optional[int] = None
	title: Optional[str] = None
	office: Optional[str] = None
	document: str = DOC_NAME
	edition: str = EDITION

	@property
	def page_label(self) -> str:
		"""How to write the page in a citation — the number the reader will see
		printed on the page when they get there, falling back to the PDF index."""
		return f"p. {self.printed_page}" if self.printed_page else f"p. {self.pdf_page}"

	def reference(self) -> str:
		"""One-line human citation, e.g.

		CvSU Citizens' Charter, FY 2026 edition, p. 948 — "Registration of
		Continuing Students (via Portal)" (Office of the Campus Registrar)
		"""
		out = f"{self.document}, {self.edition} edition, {self.page_label}"
		if self.title:
			out += f" — “{self.title}”"
			if self.office:
				out += f" ({self.office})"
		elif self.office:
			out += f" — {self.office}"
		return out

	@property
	def url(self) -> Optional[str]:
		"""Deep link that opens the PDF at this page, or None when no PDF is
		published. The fragment uses the PDF index — that is what a viewer
		counts — even though the citation shows the printed number."""
		base = resolved_pdf_url()
		return f"{base}#page={self.pdf_page}" if base else None


class PageIndex:
	"""data/charter_structure.json, queryable by PDF page number.

	Sections are an inclusive [first, last] page range each, so the file stays
	short enough to read and hand-edit; lookup binary-searches their start pages
	rather than expanding them into a per-page dict.
	"""

	def __init__(self, path: str = STRUCTURE_PATH):
		self._starts: list[int] = []
		self._sections: list[dict] = []
		self._offset = 0
		self._printed_from = 1
		self._pdf_pages = 0
		self.document = DOC_NAME
		self.edition = EDITION
		try:
			with open(path, encoding="utf-8") as fh:
				raw = json.load(fh)
		except (OSError, ValueError) as exc:
			_logger.warning(
				"charter structure not readable (%s) — citations will carry the "
				"page number only; run scripts/build_charter_structure.py", exc
			)
			return
		self.document = raw.get("document") or DOC_NAME
		self.edition = raw.get("edition") or EDITION
		self._offset = int(raw.get("page_offset") or 0)
		self._printed_from = int(raw.get("printed_from") or 1)
		self._pdf_pages = int(raw.get("pdf_pages") or 0)
		# Sorted by start page so bisect can find the section covering a page.
		sections = sorted(
			(s for s in raw.get("sections") or [] if s.get("pages")),
			key=lambda s: s["pages"][0],
		)
		self._sections = sections
		self._starts = [s["pages"][0] for s in sections]
		_logger.info(
			"charter structure ready: %d sections, %s %s, page offset %d",
			len(sections), self.document, self.edition, self._offset,
		)

	@property
	def available(self) -> bool:
		return bool(self._sections)

	def printed_page(self, pdf_page: int) -> Optional[int]:
		"""The number printed on the page, or None when there isn't one —
		unnumbered front matter, or a page beyond the end of the document
		(a stale binding must not be dressed up as a plausible page number)."""
		if pdf_page < self._printed_from or (self._pdf_pages and pdf_page > self._pdf_pages):
			return None
		printed = pdf_page - self._offset
		return printed if printed > 0 else None

	def describe(self, pdf_page: int) -> PageInfo:
		"""Metadata for a page. A page inside no section — front matter, an
		appendix — keeps its number and simply has nothing else to say."""
		info = {"title": None, "office": None}
		i = bisect_right(self._starts, pdf_page) - 1
		if i >= 0:
			section = self._sections[i]
			first, last = section["pages"][0], section["pages"][-1]
			if first <= pdf_page <= last:
				info = {"title": section.get("title"), "office": section.get("office")}
		return PageInfo(
			pdf_page=pdf_page, printed_page=self.printed_page(pdf_page),
			document=self.document, edition=self.edition, **info,
		)

	def snapshot(self) -> dict:
		"""Operational summary for /admin/status."""
		pages = sum(s["pages"][-1] - s["pages"][0] + 1 for s in self._sections)
		return {
			"available": self.available,
			"sections": len(self._sections),
			"pages_in_a_section": pages,
			"edition": self.edition,
			"page_offset": self._offset,
			"pdf_present": pdf_available(),
			"pdf_url_configured": PDF_URL or None,   # CHARTER_PDF_URL override
			"pdf_url": resolved_pdf_url() or None,   # what citations actually link
		}


_index: Optional[PageIndex] = None
# Guards the build/swap of _index. get_index is a check-then-set, so without
# this the first concurrent burst after a restart has every thread build its
# own copy inside one 2G container.
_index_lock = threading.Lock()


def get_index() -> Optional[PageIndex]:
	"""Lazy singleton; None when the structure file is missing or empty."""
	global _index
	if _index is None:
		with _index_lock:
			# Double-checked: another thread may have built it while we waited.
			if _index is None:
				_index = PageIndex()
	return _index if _index.available else None


def reload_index() -> Optional[PageIndex]:
	"""Rebuild from the structure file and swap in — for /model/reload and for
	tests that write a structure file. Build first, swap second, so readers keep
	serving from the old index instead of seeing None mid-rebuild."""
	global _index
	fresh = PageIndex()
	with _index_lock:
		_index = fresh
	return fresh if fresh.available else None


def set_request_origin(origin: str) -> None:
	"""Bind the origin serving the current request (api.app middleware), so a
	citation can deep-link this API's own copy of the PDF with no configuration.
	Request-scoped by design — see _request_origin."""
	_request_origin.set(origin.rstrip("/"))


def resolved_pdf_url() -> str:
	"""Public URL of the charter PDF, or "" when there is nothing to link to.

	CHARTER_PDF_URL wins — an operator who set it has taken responsibility for
	where it points (it may be CvSU's own copy, not ours). Otherwise we link to
	the PDF this API serves, and only if it is actually on disk: a link we
	generated ourselves must not 404 because the build dropped the file.
	"""
	if PDF_URL:
		return PDF_URL
	origin = _request_origin.get()
	return f"{origin}{ROUTE_PATH}" if origin and pdf_available() else ""


def describe(pdf_page: int) -> PageInfo:
	"""PageInfo for a charter page — never raises, never returns None."""
	index = get_index()
	return index.describe(pdf_page) if index else PageInfo(pdf_page=pdf_page)


def cite_page(pdf_page: int) -> str:
	"""The one-line citation for a charter page. Single source of truth: both
	the RAG tier (charter_rag.Passage) and the per-intent bindings
	(intent_grounding.SourceRef) render charter pages through this."""
	return describe(pdf_page).reference()


def page_url(pdf_page: int) -> Optional[str]:
	"""Deep link to a charter page, or None when no PDF URL is configured."""
	return describe(pdf_page).url


def pdf_available() -> bool:
	"""Whether the PDF this API offers to serve is actually on disk."""
	return os.path.isfile(PDF_PATH)
