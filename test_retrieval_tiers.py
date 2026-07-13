"""Two-sided corpus tests for the retrieval tiers (site_rag + intent_retrieval).

Run:  python test_retrieval_tiers.py
Pure module tests — no TensorFlow, no LLM, no running server required.
"""
import sys

from api import intent_retrieval, site_rag

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


print("=" * 64)
print("SITE RAG")
print("=" * 64)
idx = site_rag.get_index()
check("index builds from docs/site_corpus.txt", idx is not None)

if idx is not None:
    # Positives: portal content must be retrievable above the augmentation floor,
    # with the right document on top.
    positives = [
        ("who is the president of CvSU", "ukrainian ambassador"),  # names Dr. Nuestro + VPs
        ("WELA 2026 registration", "wela"),
        ("seagrass research Maragondon", "seagrass"),
        ("CvSU ranking WURI top 500", "wuri"),
        ("AACCUP accreditation of CvSU programs", "aaccup"),
    ]
    for query, expect_in_title in positives:
        ps = idx.retrieve(query, k=1)
        ok = bool(ps) and ps[0].score >= site_rag.AUGMENT_MIN_SCORE and expect_in_title in ps[0].title.lower()
        check(f"retrieves for {query!r}", ok,
              f"got {ps[0].title[:40]!r}@{ps[0].score:.3f}" if ps else "no hits")

    # Citation carries title + URL so the LLM can cite the portal.
    ps = idx.retrieve("WELA 2026 registration", k=1)
    check("citation has title and URL",
          bool(ps) and "WELA" in ps[0].citation() and "http" in ps[0].citation())

    # Negatives: off-topic must never clear the verbatim gate
    # (QUOTE_MIN + >=1 bigram hit) — the same rule predict() enforces.
    for query in ["pizza delivery near me", "how to cook adobo",
                  "what is the meaning of life", "bitcoin price today"]:
        ps = idx.retrieve(query, k=1)
        quotable = bool(ps) and ps[0].score >= site_rag.QUOTE_MIN_SCORE and ps[0].bigram_hits >= 1
        check(f"no verbatim quote for {query!r}", not quotable,
              f"got {ps[0].title[:40]!r}@{ps[0].score:.3f}" if ps else "")

    # WordPress boilerplate must not be in the corpus.
    with open(site_rag._PATH, encoding="utf-8") as fh:
        corpus = fh.read().lower()
    check("Sample Page boilerplate excluded", "| sample page |" not in corpus)
    check("Hello world! boilerplate excluded", "| hello world! |" not in corpus)

print()
print("=" * 64)
print("INTENT RETRIEVAL")
print("=" * 64)
ir = intent_retrieval.get_index()
check("index builds from intents DB", ir is not None)

if ir is not None:
    # Positives: unseen-but-close phrasings must clear HIGH_MATCH_SCORE
    # (served without needing NB agreement).
    for query, intent in [
        ("complete list of courses", "courses_offered"),
        ("who is the cvsu president po", "university_officials"),
        ("full list of courses po", "courses_offered"),
    ]:
        m = ir.retrieve(query)
        ok = m is not None and m.intent == intent and m.score >= intent_retrieval.HIGH_MATCH_SCORE
        check(f"{query!r} -> {intent} (high band)", ok,
              f"got {m.intent}@{m.score:.3f}" if m else "None")

    # Negatives: novel/off-topic queries stay below the serving floor —
    # or resolve to out_of_scope, which is the designed handler for them.
    for query in ["how do I bake a cake", "asdkjhaskdjh",
                  "write me a poem about the moon"]:
        m = ir.retrieve(query)
        ok = m is None or m.score < intent_retrieval.MATCH_MIN_SCORE or m.intent == "out_of_scope"
        check(f"no CvSU intent hijack for {query!r}", ok,
              f"got {m.intent}@{m.score:.3f}" if m else "")

    # Other-school questions must never short-circuit into a CvSU intent.
    for query in ["list of courses at UP Diliman", "tuition at Ateneo",
                  "does DLSU have engineering"]:
        check(f"other-school guard for {query!r}",
              intent_retrieval.mentions_other_school(query))
    check("guard passes plain CvSU query",
          not intent_retrieval.mentions_other_school("complete list of courses at CvSU"))

    # nlu_fallback patterns are excluded from the index.
    m = ir.retrieve("draw me a picture")  # verbatim nlu_fallback pattern
    check("nlu_fallback patterns excluded",
          m is None or m.intent != "nlu_fallback")

print()
print("=" * 64)
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
