"""Shared lexical-ranking upgrade for the document tiers (ADR-002, stage B).

BM25 (Okapi) + Reciprocal Rank Fusion for charter_rag and site_rag, kept
dependency-free beyond numpy (already required by the classifier stack).

Why the design is POOL ENRICHMENT rather than score replacement: both RAG
tiers gate *serving* on coverage-weighted cosine thresholds calibrated in
their own modules (AUGMENT_MIN_SCORE / QUOTE_MIN_SCORE). Raw BM25 scores
live on an unbounded, corpus-dependent scale, so swapping the score would
silently invalidate those calibrations — exactly the class of drift the
precision-first rule exists to prevent. Instead, BM25 widens WHICH chunks
enter the candidate pool (recovering what raw cosine under-ranks: long
chunks penalized by length, term-repetition saturation, and chunks whose
coverage-weighted score would pass the gate but whose raw cosine never made
the pool cut), while the calibrated score still decides what is served and
in what order. Served output remains gated and ordered by the same numbers
as before this module existed.

The BM25 index tokenizes with the SAME sklearn analyzer the tier's
TfidfVectorizer uses (unigrams + bigrams, english stopwords), so both
rankers see one vocabulary and the fusion compares like with like.

Env:
    LEXICAL_RANKER — "fused" (default): BM25 + cosine RRF candidate pool
                     "tfidf": legacy pure-cosine pool (pre-ADR-002 behavior)
"""
from __future__ import annotations

import math
import os
from collections import Counter
from typing import Sequence

import numpy as np

# Standard reciprocal-rank-fusion constant (Cormack et al. 2009): small enough
# that top ranks dominate, large enough that a #1 vote cannot be outvoted by
# two mid-list appearances.
RRF_K = 60

# BM25 defaults per the literature; not exposed as env on purpose — they are
# corpus-shape parameters, and tuning them belongs behind the gold eval, not
# in a deployment file.
_BM25_K1 = 1.5
_BM25_B = 0.75


def ranker_mode() -> str:
	"""Resolve LEXICAL_RANKER, defaulting unknown values to "fused"."""
	mode = os.environ.get("LEXICAL_RANKER", "fused").strip().lower()
	return mode if mode in ("fused", "tfidf") else "fused"


class BM25Index:
	"""Okapi BM25 over pre-tokenized documents.

	Build once next to the TF-IDF matrix; ~O(total tokens) memory. scores()
	returns a dense array aligned with the document order it was built from,
	on BM25's own scale (only the *ranking* is consumed — see module doc).
	"""

	def __init__(self, docs_tokens: Sequence[Sequence[str]],
	             k1: float = _BM25_K1, b: float = _BM25_B):
		self._k1 = float(k1)
		self._b = float(b)
		self._n_docs = len(docs_tokens)
		self._doc_len = np.array([len(t) for t in docs_tokens], dtype=np.float64)
		self._avg_len = float(self._doc_len.mean()) if self._n_docs else 0.0
		# postings: term -> (doc_index array, term-frequency array)
		tf_by_term: dict[str, dict[int, int]] = {}
		for i, toks in enumerate(docs_tokens):
			for t, tf in Counter(toks).items():
				tf_by_term.setdefault(t, {})[i] = tf
		self._postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
		self._idf: dict[str, float] = {}
		for term, docs in tf_by_term.items():
			idx = np.fromiter(docs.keys(), dtype=np.int64, count=len(docs))
			tfs = np.fromiter(docs.values(), dtype=np.float64, count=len(docs))
			self._postings[term] = (idx, tfs)
			df = len(docs)
			# BM25+-style floor: ln(1 + …) keeps very common terms at a small
			# positive weight instead of going negative and *subtracting*
			# evidence, which matters on a corpus this small.
			self._idf[term] = math.log(1.0 + (self._n_docs - df + 0.5) / (df + 0.5))

	@property
	def size(self) -> int:
		return self._n_docs

	def scores(self, query_tokens: Sequence[str]) -> np.ndarray:
		"""BM25 score per document for the tokenized query."""
		out = np.zeros(self._n_docs, dtype=np.float64)
		if not self._n_docs or self._avg_len == 0.0:
			return out
		norm = self._k1 * (1.0 - self._b + self._b * self._doc_len / self._avg_len)
		for term, qtf in Counter(query_tokens).items():
			posting = self._postings.get(term)
			if posting is None:
				continue
			idx, tfs = posting
			out[idx] += qtf * self._idf[term] * (tfs * (self._k1 + 1.0)) / (tfs + norm[idx])
		return out


def rrf_fuse(score_lists: Sequence[np.ndarray], pool: int) -> list[int]:
	"""Fuse rankings by Reciprocal Rank Fusion; return fused doc order.

	Each array is one ranker's scores over the same documents. Only each
	ranker's top-`pool` docs vote (zero/negative scores never vote — a doc no
	ranker matched must not ride in on rank position alone). Result is the
	union of voters ordered by summed 1/(RRF_K + rank), ties broken by doc id
	for determinism.
	"""
	fused: dict[int, float] = {}
	for scores in score_lists:
		if scores.size == 0:
			continue
		order = np.argsort(scores)[::-1][:pool]
		for rank, doc in enumerate(order):
			if scores[doc] <= 0.0:
				break  # argsort is descending: everything after is ≤ 0 too
			fused[int(doc)] = fused.get(int(doc), 0.0) + 1.0 / (RRF_K + rank)
	return [doc for doc, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]


def candidate_order(cosine_scores: np.ndarray, bm25: BM25Index | None,
                    query_tokens: Sequence[str], cap: int) -> list[int]:
	"""Candidate doc order for a retrieval pool of size `cap`.

	fused mode: RRF over (cosine, BM25), each voting its top 2×cap.
	tfidf mode (or no BM25 index): legacy order — cosine descending.
	Both modes return only docs with cosine > 0: the calibrated serving score
	is coverage-weighted *cosine*, so a zero-cosine doc can never be served
	and admitting it would only burn a pool slot.
	"""
	if ranker_mode() == "fused" and bm25 is not None and bm25.size == len(cosine_scores):
		order = rrf_fuse((cosine_scores, bm25.scores(query_tokens)), pool=max(cap * 2, 16))
	else:
		order = np.argsort(cosine_scores)[::-1].tolist()
	out = []
	for doc in order:
		if cosine_scores[doc] > 0.0:
			out.append(int(doc))
			if len(out) >= cap:
				break
	return out
