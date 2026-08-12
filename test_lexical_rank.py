"""Regression tests — ADR-002 stage B lexical ranking (BM25 + RRF pooling).

Pins the three contracts that make the upgrade safe under the precision-first
rule:

  1. BM25 behaves like BM25 (rare-term weighting, tf saturation, length
     normalization) — the properties that justify adding it at all.
  2. Fusion only widens the CANDIDATE POOL. Serving scores stay the
     calibrated coverage-weighted cosine, zero-cosine docs are never
     admitted, and LEXICAL_RANKER=tfidf reproduces the legacy order exactly.
  3. The real tiers (CharterIndex / SiteIndex) build their BM25 twin and
     still serve calibrated scores in both modes.

Run:  python test_lexical_rank.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

from api import lexical_rank
from api.lexical_rank import BM25Index, candidate_order, ranker_mode, rrf_fuse

_failures = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _failures.append(name)


def main() -> int:
    # ── 1. BM25 core properties ─────────────────────────────────────────
    docs = [
        "the registrar office handles transcript of records requests".split(),
        "the library keeps books and journals for students".split(),
        "zebra".split(),
        ("transcript " * 50 + "office").split(),          # tf-spam doc
        ("the registrar office " + "filler " * 120).split(),  # long doc, tf=1
    ]
    idx = BM25Index(docs)

    check("rare term ranks its only document first",
          int(np.argmax(idx.scores(["zebra"]))) == 2)

    s = idx.scores(["transcript"])
    check("term frequency saturates (x50 repeats beat x1, but nowhere near x50)",
          s[3] > s[0] > 0 and s[3] / s[0] < 5.0)

    s = idx.scores(["registrar"])
    check("length normalization: same tf, shorter doc wins",
          s[0] > s[4] > 0)

    check("query terms absent from the corpus score zero everywhere",
          float(np.max(idx.scores(["nonexistentword"]))) == 0.0)

    # ── 2. RRF fusion + pool contract ───────────────────────────────────
    # doc0: rank 1 + rank 3 → must beat doc1: rank 2 + rank 4.
    a = np.array([9.0, 8.0, 0.0, 1.0])
    b = np.array([2.0, 1.0, 0.0, 9.0])
    fused = rrf_fuse((a, b), pool=4)
    check("RRF: consistent top ranks outrank a split vote",
          fused.index(0) < fused.index(1))
    check("RRF: docs no ranker matched never appear", 2 not in fused)

    cos = np.array([0.5, 0.0, 0.2])
    bm = BM25Index([["alpha"], ["beta"], ["gamma"]])
    prev = os.environ.get("LEXICAL_RANKER")
    try:
        os.environ["LEXICAL_RANKER"] = "fused"
        order = candidate_order(cos, bm, ["beta"], cap=3)
        check("calibration guard: zero-cosine docs excluded even on a BM25 hit",
              1 not in order and set(order) <= {0, 2})

        os.environ["LEXICAL_RANKER"] = "tfidf"
        legacy = candidate_order(cos, bm, ["beta"], cap=3)
        check("legacy mode reproduces pure cosine-descending order",
              legacy == [0, 2])

        os.environ["LEXICAL_RANKER"] = "definitely-not-a-mode"
        check("unknown LEXICAL_RANKER values default to fused",
              ranker_mode() == "fused")
    finally:
        if prev is None:
            os.environ.pop("LEXICAL_RANKER", None)
        else:
            os.environ["LEXICAL_RANKER"] = prev

    # ── 3. Real tiers build and serve calibrated scores in both modes ───
    site_corpus = (
        "----- DOC | Registrar Services | https://cvsu.edu.ph/registrar | 2026-01-01 -----\n"
        "The Office of the Registrar processes transcript of records requests, "
        "certificates of enrollment, and student records. Requests are filed at "
        "the registrar window with a valid school ID.\n"
        "----- DOC | Library Hours | https://cvsu.edu.ph/library | 2026-01-01 -----\n"
        "The university library is open on weekdays. Students may borrow books "
        "and access journals and study spaces.\n"
        "----- DOC | Scholarship Programs | https://cvsu.edu.ph/scholarships | 2026-01-01 -----\n"
        "Scholarship grants and financial assistance programs are handled by the "
        "Office of Student Affairs and Services together with partner agencies.\n"
    )
    charter_text = (
        "----- PAGE 12 -----\n"
        "Issuance of Transcript of Records. The registrar receives the request "
        "form, assesses fees, and releases the transcript within the processing "
        "period stated in this charter.\n"
        "----- PAGE 30 -----\n"
        "Library borrowing services for enrolled students, including book "
        "lending and return schedules.\n"
    )

    from api.charter_rag import CharterIndex
    from api.site_rag import SiteIndex

    with tempfile.TemporaryDirectory() as tmp:
        site_path = Path(tmp) / "site_corpus.txt"
        site_path.write_text(site_corpus, encoding="utf-8")
        charter_path = Path(tmp) / "charter.txt"
        charter_path.write_text(charter_text, encoding="utf-8")

        for mode in ("fused", "tfidf"):
            os.environ["LEXICAL_RANKER"] = mode
            try:
                site = SiteIndex(path=str(site_path))
                check(f"[{mode}] site index builds with a BM25 twin",
                      site.available and (site._bm25 is not None))
                hits = site.retrieve("how do I request my transcript of records", k=2)
                check(f"[{mode}] site retrieve finds the registrar doc first",
                      bool(hits) and "registrar" in hits[0].url)
                check(f"[{mode}] site scores stay on the calibrated 0..1 cosine scale",
                      all(0.0 < h.score <= 1.0 for h in hits))

                charter = CharterIndex(path=str(charter_path))
                check(f"[{mode}] charter index builds with a BM25 twin",
                      charter.available and (charter._bm25 is not None))
                hits = charter.retrieve("transcript of records request", k=1)
                check(f"[{mode}] charter retrieve cites the transcript page",
                      bool(hits) and hits[0].page == 12)
                check(f"[{mode}] charter scores stay on the calibrated scale",
                      all(0.0 < h.score <= 1.0 for h in hits))
            finally:
                os.environ.pop("LEXICAL_RANKER", None)

    print()
    if _failures:
        print(f"{len(_failures)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
