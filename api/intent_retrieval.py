"""Intent-retrieval tier — soft lexical matching over the intent patterns.

Sits between the NN tier and the LLM fallback in HybridChatbot.predict().
When the NB/NN classifiers under-score a phrasing they were never trained on
("complete list of courses" scored NB 0.28 / NN 0.37 while the patterns
corpus contains near-identical utterances), a TF-IDF nearest-pattern match
still identifies the right intent with high similarity — so the curated,
site-aligned response is served with zero LLM latency.

The index is built from the same SQLite intents DB the trainers read, so a
retrain/reload cycle keeps all tiers on one corpus. nlu_fallback's patterns
are excluded (matching the fallback intent is never useful).

Env:
    INTENT_RETRIEVAL_ENABLED — "0" disables the tier (default "1")
    INTENT_RETRIEVAL_MIN     — override the match floor (default 0.60)
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("diwa.intent_retrieval")

_ENABLED = os.environ.get("INTENT_RETRIEVAL_ENABLED", "1") == "1"
_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cavsu_intents.db"
_EXCLUDED_INTENTS = {"nlu_fallback"}

# Floors for serving a curated response, calibrated on the pattern corpus:
# unseen-but-close phrasings ("who is the cvsu president po") score 0.65-1.0;
# genuinely novel questions and off-topic text score <=0.45. Char-gram cosine
# rewards lexical look-alikes, so a match below HIGH_MATCH_SCORE is only
# served when it agrees with the Naive Bayes tier's top (sub-threshold) guess.
MATCH_MIN_SCORE = float(os.environ.get("INTENT_RETRIEVAL_MIN", "0.60"))
HIGH_MATCH_SCORE = 0.80

# Questions about other schools must never short-circuit into a CvSU intent
# ("list of courses at UP Diliman" is a lexical near-match of the catalog
# patterns). Mirrors the schools enumerated by the out_of_scope intent.
_OTHER_SCHOOL_RE = re.compile(
	r"\b(up diliman|diliman|ateneo|dlsu|de la salle|la salle|ust|santo tomas"
	r"|pup|tup|feu|adamson|mapua|letran|san beda|batangas state|laguna state"
	r"|another (university|school)|other (universities|schools))\b",
	re.IGNORECASE,
)


def mentions_other_school(query: str) -> bool:
	return bool(query and _OTHER_SCHOOL_RE.search(query))


@dataclass(frozen=True)
class Match:
	intent: str
	score: float
	pattern: str  # the training utterance that matched (for logs/telemetry)


class IntentPatternIndex:
	def __init__(self, db_path: Path = _DB_PATH):
		self._intents: list[str] = []
		self._patterns: list[str] = []
		self._vectorizer = None
		self._matrix = None
		try:
			conn = sqlite3.connect(db_path)
			rows = conn.execute(
				"SELECT i.tag, p.pattern_text FROM patterns p "
				"JOIN intents i ON i.id = p.intent_id WHERE i.active = 1"
			).fetchall()
			conn.close()
		except sqlite3.Error as exc:
			_logger.warning("intents DB not readable (%s) — intent retrieval disabled", exc)
			return
		for tag, pattern in rows:
			if tag in _EXCLUDED_INTENTS or not pattern or not pattern.strip():
				continue
			self._intents.append(tag)
			self._patterns.append(pattern.strip())
		if not self._patterns:
			return
		from sklearn.feature_extraction.text import TfidfVectorizer

		# Char 3-5 grams: robust to word-order shuffles, Taglish phrasing,
		# and small typos ("cvsu presidnet") that a word-gram view would miss.
		self._vectorizer = TfidfVectorizer(
			lowercase=True, analyzer="char_wb", ngram_range=(3, 5),
			sublinear_tf=True, min_df=1,
		)
		self._matrix = self._vectorizer.fit_transform(self._patterns)
		_logger.info(
			"intent-pattern index ready: %d patterns / %d intents",
			len(self._patterns), len(set(self._intents)),
		)

	@property
	def available(self) -> bool:
		return self._matrix is not None

	def retrieve(self, query: str) -> Optional[Match]:
		"""Best-matching intent for the query, regardless of the floor.

		Callers compare .score against MATCH_MIN_SCORE to decide whether to
		serve the curated response; sub-floor matches are still useful as
		"did you mean" suggestions in the LLM's don't-know instruction.
		"""
		if not self.available or not query or not query.strip():
			return None
		from sklearn.metrics.pairwise import linear_kernel

		q = self._vectorizer.transform([query])
		scores = linear_kernel(q, self._matrix)[0]
		best = int(scores.argmax())
		if scores[best] <= 0.0:
			return None
		return Match(
			intent=self._intents[best],
			score=float(scores[best]),
			pattern=self._patterns[best],
		)


_index: Optional[IntentPatternIndex] = None


def get_index() -> Optional[IntentPatternIndex]:
	"""Lazy singleton; returns None when disabled or the DB is unreadable."""
	global _index
	if not _ENABLED:
		return None
	if _index is None:
		_index = IntentPatternIndex()
	return _index if _index.available else None


def reload_index() -> Optional[IntentPatternIndex]:
	"""Drop the singleton and rebuild from the DB (used after retrain/reload)."""
	global _index
	_index = None
	return get_index()
