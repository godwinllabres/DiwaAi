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

Page splitting and everything a *reader* needs from a page number — the service
documented there, the owning office, the number printed on the page, the deep
link into the PDF — live in charter_pages, which is also what makes this tier
and the per-intent bindings cite a page identically.

Env:
    CHARTER_RAG_ENABLED  — "0" disables both consumers (default "1")
    CHARTER_RAG_PATH     — override the charter text location
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

from . import charter_pages
from .charter_pages import split_pages
from . import lexical_rank

_logger = logging.getLogger("diwa.charter_rag")

_ENABLED = os.environ.get("CHARTER_RAG_ENABLED", "1") == "1"
_PATH = charter_pages.TEXT_PATH

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
# Raised 0.12 -> 0.15 (2026-08-12) to match site_rag, after UAT caught the
# verbatim tier quoting "Issuance of School Identification Card (Replacement)"
# at 0.1211 for "How do I process the payment for my OJT Fee?" — a confident
# citation, page number and all, to an unrelated procedure. That is worse than
# no answer: the reader has no way to tell a 0.12 quote from a 0.6 one.
#
# Measured on the 268-question gold set before moving it. The [0.12, 0.15) band
# holds exactly 3 charter hits — seagrass research, a financial report, and the
# university mission — none of which the Citizens' Charter is the right source
# for. All 3 are already served by site_rag at HIGHER scores (0.164, 0.199,
# 0.202) from the pages that actually contain them, so gold coverage is
# unchanged at 172/268 and source selection improves.
#
# The floor is doing what it can, and no more: the same UAT round produced
# "can i still be laude if i shift..." matching a 2021 course-list page at
# 0.189 on the SITE corpus, and the [0.18, 0.20) site band holds 14 good gold
# answers. Score does not separate those two — a distinctive-term (high-IDF)
# filter was prototyped and rejected, because on this corpus the highest-IDF
# tokens of a Taglish question are its interrogatives ("ano", "yung", "sino"),
# so it cut 38 mostly-good quotes while keeping both bad ones.
QUOTE_MIN_SCORE = 0.15


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
		"""Names the service and the printed page, not just the PDF index —
		see charter_pages, which renders this for every charter citation."""
		return charter_pages.cite_page(self.page)

	@property
	def url(self) -> Optional[str]:
		"""Deep link opening the charter PDF at this page, when one is published."""
		return charter_pages.page_url(self.page)


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
		self._bm25 = None
		try:
			with open(path, encoding="utf-8") as fh:
				raw = fh.read()
		except OSError as exc:
			_logger.warning("charter text not readable (%s) — RAG tier disabled", exc)
			return
		self._chunks = _chunk_pages(split_pages(raw))
		if not self._chunks:
			return
		from sklearn.feature_extraction.text import TfidfVectorizer

		self._vectorizer = TfidfVectorizer(
			lowercase=True, ngram_range=(1, 2), sublinear_tf=True, min_df=1,
			stop_words="english",
		)
		self._matrix = self._vectorizer.fit_transform(t for _, t in self._chunks)
		# BM25 twin index over the same analyzer's tokens (ADR-002). Pool
		# enrichment only — serving stays gated on the calibrated cosine
		# thresholds above; see api/lexical_rank.py for the full rationale.
		analyzer = self._vectorizer.build_analyzer()
		self._bm25 = lexical_rank.BM25Index([analyzer(t) for _, t in self._chunks])
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
		# Candidate pool: fused BM25+cosine order (LEXICAL_RANKER=fused,
		# the default) or the legacy pure-cosine order (=tfidf). Only
		# cosine-positive docs are admitted, so the calibrated serving
		# score below keeps its meaning in both modes.
		order = lexical_rank.candidate_order(
			scores, self._bm25, query_terms, cap=max(k * 4, 8))
		passages = []
		for i in order:
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
# Guards the build/swap of _index. get_index is a check-then-set, so
# without this the first concurrent burst after a restart has every
# thread build its own copy inside one 2G container.
_index_lock = threading.Lock()


def get_index() -> Optional[CharterIndex]:
	"""Lazy singleton; returns None when disabled or the text is missing."""
	global _index
	if not _ENABLED:
		return None
	if _index is None:
		with _index_lock:
			# Double-checked: another thread may have built it while we waited.
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
	# The quote is an excerpt of one page — point the reader at that exact page
	# so they can read the fees and processing times we had to cut.
	url = passage.url
	tail = (
		f"[Open this page of the charter]({url})"
		if url else "see the full document"
	)
	return (
		f"From the {passage.citation()}:\n\n{text}\n\n"
		"(Quoted directly from the official charter — for the complete "
		f"procedure, fees, and processing times, {tail}.)"
	)
