"""Citizens' Charter retrieval tier ("document tier" of the hybrid brain).

TF-IDF retrieval over docs/citizens_charter_text.txt (FY 2026 edition, page-
marked OCR text). Two consumers in HybridChatbot.predict():

  • LLM augmentation — when the LLM fallback fires and a passage scores above
    a low threshold, the passage is prepended to the prompt so the model
    answers from the charter instead of general knowledge.
  • Verbatim fallback — when NO LLM is available and a passage scores above a
    higher threshold, the passage itself (with a page citation) is returned
    instead of the static "I didn't understand" fallback.

Deliberately dependency-free beyond scikit-learn, which the chatbot already
requires. The index builds once, lazily, in a few hundred ms.

Env:
    CHARTER_RAG_ENABLED  — "0" disables both consumers (default "1")
    CHARTER_RAG_PATH     — override the charter text location
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger("diwa.charter_rag")

_ENABLED = os.environ.get("CHARTER_RAG_ENABLED", "1") == "1"
_DEFAULT_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
	"docs", "citizens_charter_text.txt",
)
_PATH = os.environ.get("CHARTER_RAG_PATH", _DEFAULT_PATH)

_PAGE_MARKER_RE = re.compile(r"^----- PAGE (\d+) -----\s*$", re.MULTILINE)
# Line-window chunking (the OCR text has no reliable paragraph breaks):
# accumulate lines to ~_CHUNK_CHARS per chunk with a few lines of overlap so
# a procedure that straddles a boundary is still retrievable as one hit.
_CHUNK_CHARS = 700
_OVERLAP_LINES = 3

# Coverage-weighted cosine thresholds, calibrated on charter queries (TOR,
# enrollment, ID replacement, complaints: ~0.08-0.16; off-topic after the
# coverage penalty: <=0.09). Augmentation can afford to be permissive (the
# LLM ignores an irrelevant excerpt); verbatim quoting cannot — and the
# verbatim tier additionally sits behind the Nonsense/Scope gates.
AUGMENT_MIN_SCORE = 0.08
QUOTE_MIN_SCORE = 0.12


@dataclass(frozen=True)
class Passage:
	score: float
	page: int
	text: str
	# Number of the query's content BIGRAMS found in this passage. Verbatim
	# quoting requires >= 1: single common words ("time", "delivery") can win
	# the cosine ranking, but a real charter question phrase-matches the text.
	bigram_hits: int = 0

	def citation(self) -> str:
		return f"CvSU Citizens' Charter, FY 2026 edition, p. {self.page}"


def _clean(text: str) -> str:
	text = text.replace("’", "'").replace("‘", "'")
	text = re.sub(r"[ \t]+", " ", text)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()


def _split_pages(raw: str) -> list[tuple[int, str]]:
	"""Return (page_number, page_text) pairs from the page-marked OCR text."""
	pages: list[tuple[int, str]] = []
	matches = list(_PAGE_MARKER_RE.finditer(raw))
	for i, m in enumerate(matches):
		end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
		body = _clean(raw[m.end():end])
		if body:
			pages.append((int(m.group(1)), body))
	if not pages:  # no markers — treat the whole file as one page
		body = _clean(raw)
		if body:
			pages.append((1, body))
	return pages


def _chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
	"""Sliding line-windows of ~_CHUNK_CHARS per page, with line overlap."""
	chunks: list[tuple[int, str]] = []
	for page, body in pages:
		lines = [ln for ln in body.split("\n") if ln.strip()]
		if not lines:
			continue
		start = 0
		while start < len(lines):
			size = 0
			end = start
			while end < len(lines) and size < _CHUNK_CHARS:
				size += len(lines[end]) + 1
				end += 1
			chunk = "\n".join(lines[start:end]).strip()
			if chunk:
				chunks.append((page, chunk))
			if end >= len(lines):
				break
			start = max(end - _OVERLAP_LINES, start + 1)
	return chunks


class CharterIndex:
	def __init__(self, path: str = _PATH):
		self._chunks: list[tuple[int, str]] = []
		self._vectorizer = None
		self._matrix = None
		try:
			with open(path, encoding="utf-8") as fh:
				raw = fh.read()
		except OSError as exc:
			_logger.warning("charter text not readable (%s) — RAG tier disabled", exc)
			return
		self._chunks = _chunk_pages(_split_pages(raw))
		if not self._chunks:
			return
		from sklearn.feature_extraction.text import TfidfVectorizer

		self._vectorizer = TfidfVectorizer(
			lowercase=True, ngram_range=(1, 2), sublinear_tf=True, min_df=1,
			stop_words="english",
		)
		self._matrix = self._vectorizer.fit_transform(t for _, t in self._chunks)
		_logger.info("charter index ready: %d chunks", len(self._chunks))

	@property
	def available(self) -> bool:
		return self._matrix is not None

	def retrieve(self, query: str, k: int = 3) -> list[Passage]:
		if not self.available or not query or not query.strip():
			return []
		from sklearn.metrics.pairwise import linear_kernel

		q = self._vectorizer.transform([query])
		scores = linear_kernel(q, self._matrix)[0]
		# Coverage penalty: cosine over TF-IDF ignores query terms missing
		# from the vocabulary, so "pizza delivery" collapses onto whichever
		# in-vocab word remains and scores like a real match. Scale by the
		# fraction of the query's content words the passage actually contains.
		analyzer = self._vectorizer.build_analyzer()
		query_terms = list(analyzer(query))
		terms = {t for t in query_terms if " " not in t}
		query_bigrams = {t for t in query_terms if " " in t}
		order = scores.argsort()[::-1][: max(k * 4, 8)]
		passages = []
		for i in order:
			if scores[i] <= 0.0:
				break
			text_lc = self._chunks[i][1].lower()
			coverage = (
				sum(1 for t in terms if t in text_lc) / len(terms) if terms else 0.0
			)
			chunk_terms = set(analyzer(self._chunks[i][1]))
			passages.append(
				Passage(
					score=float(scores[i]) * (0.5 + 0.5 * coverage),
					page=self._chunks[i][0],
					text=self._chunks[i][1],
					bigram_hits=len(query_bigrams & chunk_terms),
				)
			)
		passages.sort(key=lambda p: p.score, reverse=True)
		return passages[:k]


_index: Optional[CharterIndex] = None


def get_index() -> Optional[CharterIndex]:
	"""Lazy singleton; returns None when disabled or the text is missing."""
	global _index
	if not _ENABLED:
		return None
	if _index is None:
		_index = CharterIndex()
	return _index if _index.available else None


def augment_prompt(user_input: str, passages: list[Passage]) -> str:
	"""Wrap the user's question with charter excerpts for the LLM tier."""
	excerpts = "\n\n".join(
		f"[{p.citation()}]\n{p.text[:_CHUNK_CHARS]}" for p in passages
	)
	return (
		"Excerpts from the official CvSU Citizens' Charter are provided below. "
		"When they answer the question, base your reply on them and mention the "
		"page. When they are irrelevant, ignore them.\n\n"
		f"{excerpts}\n\nQuestion: {user_input}"
	)


def verbatim_reply(passage: Passage) -> str:
	"""Format a passage as a direct answer for the no-LLM fallback path."""
	text = passage.text
	if len(text) > 900:
		text = text[:900].rsplit(" ", 1)[0] + " …"
	return (
		f"From the {passage.citation()}:\n\n{text}\n\n"
		"(Quoted directly from the official charter — for the complete "
		"procedure, fees, and processing times, see the full document.)"
	)
