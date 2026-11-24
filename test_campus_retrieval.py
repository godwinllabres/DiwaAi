"""Campus-tagged retrieval: satellite procedures stay out of generic answers.

Run:  python test_campus_retrieval.py
Pure module tests over the real charter + site corpora, plus the two
HybridChatbot retrieval helpers on a stub bot. No TensorFlow, no LLM, no
running server required.

Regression: "how can I enroll next year" grounded on the Trece Martires
(trece.cvsu.edu.ph, p.1932) and Dasmariñas pages and presented them as the
university-wide enrollment procedure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")

import numpy as np

from api import charter_pages, charter_rag, lexical_rank, site_rag
from api.campus_context import doc_campus_key, query_campus_key, retrieval_scope, is_campus_term

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


MAIN_ALL = frozenset({"main", "all"})
TRECE_ALL = frozenset({"trece", "all"})


def section(title: str):
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


section("CHARTER PAGE TAGS")
for page, want in [
    (1932, "trece"), (1997, "dasmarinas"), (997, "imus"), (892, "cavite_city"),
    (575, "main"), (566, "main"), (21, "toc"), (52, "all"), (2025, "all"),
    (2018, "maragondon"),
    # range boundaries
    (794, "main"), (795, "bacoor"), (1879, "tanza"), (1880, "trece"),
]:
    got = charter_pages.campus_of(page)
    check(f"p{page} -> {want}", got == want, f"got {got!r}")

section("RETRIEVAL SCOPE")
for args, want in [
    (("how can I enroll next year", None), MAIN_ALL),
    (("how do I enroll at Trece campus", None), TRECE_ALL),
    (("how to enroll", "Imus City Campus"), frozenset({"imus", "all"})),
    # the campus named in the message beats the session's
    (("how to enroll at the main campus", "Imus City Campus"), MAIN_ALL),
    (("requirements sa Dasma", None), frozenset({"dasmarinas", "all"})),
    (("x", "Indang (Main Campus)"), MAIN_ALL),
    # a complaint about a campus being singled out does not ask about it
    (("why only trece?", None), MAIN_ALL),
]:
    got = retrieval_scope(*args)
    check(f"scope{args} == {sorted(want)}", got == want, f"got {sorted(got)}")
check("'bakit trece lang' names no campus", query_campus_key("bakit trece lang?") is None)
check("'trece' alone names trece", query_campus_key("trece") == "trece")

ci = charter_rag.get_index()
check("charter index builds", ci is not None)
if ci is not None:
    section("CHARTER RETRIEVE — generic question stays main + university-wide")
    q = "how can I enroll next year"
    hits = ci.retrieve(q, k=6, campuses=MAIN_ALL)
    check("returns passages", bool(hits))
    check("every passage is main/all",
          all(p.campus in MAIN_ALL for p in hits), str([(p.page, p.campus) for p in hits]))
    check("no Trece page (1880-1978)",
          not any(1880 <= p.page <= 1978 for p in hits), str([p.page for p in hits]))
    check("no trece.cvsu.edu.ph text", not any("trece.cvsu.edu.ph" in p.text for p in hits))
    unscoped = ci.retrieve(q, k=6)
    check("unscoped retrieve still returns satellite pages (filter does the work)",
          any(p.campus not in MAIN_ALL for p in unscoped),
          str([(p.page, p.campus) for p in unscoped]))
    check("passage campus matches charter_pages.campus_of",
          all(p.campus == charter_pages.campus_of(p.page) for p in unscoped))

    section("CHARTER RETRIEVE — satellite named")
    hits = ci.retrieve("how do I enroll at Trece campus", k=6, campuses=TRECE_ALL)
    check("Trece question: top hit is a Trece page",
          bool(hits) and hits[0].campus == "trece",
          str([(p.page, p.campus) for p in hits[:3]]))
    hits = ci.retrieve("admission requirements for incoming first year trece", k=6,
                       campuses=TRECE_ALL)
    check("Trece first-year admission: p1932 on top above the augment floor",
          bool(hits) and hits[0].page == 1932
          and hits[0].score >= charter_rag.AUGMENT_MIN_SCORE,
          str([(p.page, round(p.score, 3)) for p in hits[:3]]))

    section("CHARTER RETRIEVE — table of contents excluded")
    hits = ci.retrieve("registration of continuing students", k=6, campuses=MAIN_ALL)
    check("no TOC page (3-49)", not any(3 <= p.page <= 49 for p in hits),
          str([p.page for p in hits]))
    check("top page is 566", bool(hits) and hits[0].page == 566,
          str([p.page for p in hits[:3]]))

section("SITE DOC TAGS")
for title, url, want in [
    ("Trece Martires City Campus", "https://cvsu.edu.ph/trece-martires-city-campus/", "trece"),
    ("Imus Campus Officials", "https://cvsu.edu.ph/imus-campus-officials/", "imus"),
    ("CvSU Silang posts 100% passing rate in CHRA assessment",
     "https://cvsu.edu.ph/2023/01/01/cvsu-silang-posts-100-passing-rate-in-chra-assessment/", "silang"),
    ("Silang", "https://cvsu.edu.ph/2018/01/silang/", "silang"),
    ("CvSU turns over seagrass research data to DENR, LGU Maragondon",
     "https://cvsu.edu.ph/2023/01/01/cvsu-turns-over-seagrass-research-data/", "all"),
    ("CvSU Main, Bacoor Campuses among top performing schools in licensure exam",
     "https://cvsu.edu.ph/2023/01/01/cvsu-main-bacoor-campuses-among-top/", "all"),
    ("First Semester SY 2024-2025 Application for Admission",
     "https://cvsu.edu.ph/2024/01/01/first-semester-sy-2024-2025-application-for-admission/", "all"),
    ("CvSU Main Campus releases college admission result for 1st Sem, 2021-2022",
     "https://cvsu.edu.ph/2021/01/01/cvsu-main-campus-releases-college-admission-result/", "main"),
    ("Members of Indang Association of PWD participate in GADRC Adult CPR Seminar",
     "https://cvsu.edu.ph/2022/01/01/members-of-indang-association-of-pwd/", "all"),
]:
    got = doc_campus_key(title, url)
    check(f"{title[:50]!r} -> {want}", got == want, f"got {got!r}")

si = site_rag.get_index()
check("site index builds", si is not None)
if si is not None:
    section("SITE RETRIEVE")
    q = "trece martires campus courses offered"
    hits = si.retrieve(q, k=6, campuses=MAIN_ALL)
    check("main scope: no Trece-tagged doc",
          not any(p.campus == "trece" for p in hits), str([(p.title, p.campus) for p in hits]))
    hits = si.retrieve(q, k=6, campuses=TRECE_ALL)
    check("trece scope: the Trece Martires City Campus page is reachable",
          any(p.title == "Trece Martires City Campus" for p in hits),
          str([p.title for p in hits]))

section("LEXICAL_RANK mask=None is a no-op")
rng = np.random.default_rng(7)
cos = np.where(rng.random(200) > 0.4, rng.random(200), 0.0)
docs = [[f"t{int(x)}" for x in rng.integers(0, 30, 12)] for _ in range(200)]
bm = lexical_rank.BM25Index(docs)
query = ["t1", "t2", "t3"]
check("mask=None == no mask",
      lexical_rank.candidate_order(cos, bm, query, cap=20)
      == lexical_rank.candidate_order(cos, bm, query, cap=20, mask=None))
mask = rng.random(200) > 0.5
masked = lexical_rank.candidate_order(cos, bm, query, cap=20, mask=mask)
check("mask keeps only in-mask docs", all(mask[i] for i in masked))

section("HYBRIDCHATBOT RETRIEVAL HELPERS")
from api.hybrid_chatbot import HybridChatbot, NonsenseGate, ScopeGate

bot = HybridChatbot.__new__(HybridChatbot)
bot.model_usage_stats = {}
bot.nonsense_gate = NonsenseGate()
bot.scope_gate = ScopeGate()

grounding, _, _ = bot._gather_grounding("how can I enroll next year")
texts = " ".join(g[2] for g in grounding).upper()
check("generic enroll: no Trece/Dasmariñas grounding",
      "TRECE MARTIRES" not in texts and "DASMARI" not in texts and "TRECE.CVSU" not in texts,
      str([g[1] for g in grounding]))

grounding, _, _ = bot._gather_grounding(
    "admission requirements for incoming first year", campus="Trece Martires City Campus")
check("Trece session: grounding includes the trece.cvsu.edu.ph page",
      any("trece.cvsu.edu.ph" in g[2] for g in grounding), str([g[1] for g in grounding]))

# Follow-up: the concatenated query names Trece, the current message does not.
grounding, _, _ = bot._gather_grounding(
    "admission requirements for incoming first year trece what about main campus",
    scope_text="what about main campus")
check("scope_text decides scope, not the concatenated query",
      not any("trece.cvsu.edu.ph" in g[2] for g in grounding), str([g[1] for g in grounding]))

served = bot._verbatim_document_reply("admission requirements for incoming first year trece")
check("verbatim Trece question quotes a page", served is not None)
if served is not None:
    check("verbatim Trece question quotes the Trece admission page (p.1883)",
          served[0] == "charter_info" and "p. 1883" in served[1], served[1][:200])
for q in ["admission requirements for incoming first year",
          "how can I enroll next year", "registration of continuing students"]:
    served = bot._verbatim_document_reply(q)
    reply = served[1] if served else ""
    check(f"verbatim {q!r} never quotes a satellite page",
          not any(s in reply.upper() for s in ("TRECE", "DASMARI", "IMUS", "CAVITE CITY", "BACOOR")),
          reply[:200])

# A bigram made only of campus words ("trece campus") is not a phrase match:
# inside Trece's pages it quoted the library procedure for an enrollment ask.
served = bot._verbatim_document_reply("how do I enroll at Trece campus")
check("verbatim Trece enroll does not quote on the campus name alone",
      served is None or "Library" not in served[1], (served or ("", ""))[1][:200])
check("campus-only bigram detected",
      is_campus_term("trece campus") and is_campus_term("martires city")
      and not is_campus_term("enroll trece") and not is_campus_term("general education"))

section(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
