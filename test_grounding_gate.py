"""Regression: the relevance gate for the RAG tiers (api/grounding_gate.py).

Pins the contracts that replaced the absolute cosine floors (2026-09-02):

  1. QUERY PROFILE — a turn that asks for nothing (the UAT utterance "wow, you
     can answer questions like that?", acks, feedback, bot questions) has no
     information need; a statement or a keyword list is underspecified (never
     quoted); a real question is a full information need. Shape flags are read
     from RAW tokens, because sklearn's stop list eats "how"/"who"/"do"/"can".
  2. DECISIONS on the live corpora — the measured failures never come back:
     the GAD-workshop page is not handed to the LLM for the UAT utterance and
     not quoted for "can you answer questions?"; the beta misquotes ("Indang
     (Main Campus)", "Who is the head of cashiers office") stay unquoted; a
     thanks message that names a topic is not quoted; a present-state
     who-question is never quoted from a dated post or the charter; a real
     answerable question still gets its passage.
  3. FLOORS on the two eval sets (scripts/eval_grounding_gate.py): in-sample
     (mirror_qa gold/traps + grounding_probes) and HELD-OUT (the critics' fresh
     utterances, never used for tuning). Floors sit a little under the values
     measured at ship time so a regression fails loudly without flaking on a
     corpus re-sync.

Run: python test_grounding_gate.py   (pure module test: no TensorFlow, no LLM,
no server; ~2 min for the two eval sets)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api import charter_rag, grounding_gate as gg, site_rag  # noqa: E402

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


print("── query profile (corpus-independent) ──")
NEED0 = [
    "wow, you can answer questions like that?", "can you answer questions?", "you can answer that?",
    "nice, very helpful", "thank you so much", "salamat po", "ok", "noted", "hmm", "are you a bot",
    "are you a real person", "do you store my data?", "what is your name", "who made you",
    "show me the map", "the second one", "number 3", "good luck sa board exam", "happy teachers day po",
    "Hi! I'm a first year student. Nice to meet you!", "ikaw ba si sevi?", "may kausap ba akong tao?",
    "that's not what i asked", "is that true?", "galing mo naman", "cool", "tnx po",
]
for t in NEED0:
    prof = gg.analyze_query(t)
    check(f"no information need: {t!r}", prof.need == 0, f"need={prof.need} reason={prof.reason} topical={prof.topical}")

NEED1 = [  # underspecified: excerpts may reach the LLM, never a quote
    "I passed the September 2025 psychometricians licensure exam",
    "nakapasa ako sa board exam!!!", "i am a student of cvsu main campus",
    "congratulations to the new licensed architects", "enrollment schedule", "Indang (Main Campus)",
    "college of nursing", "Registration form", "salamat sa info about sa criminology board exam",
]
for t in NEED1:
    prof = gg.analyze_query(t)
    check(f"underspecified, never quotable: {t!r}", prof.need >= 1 and not prof.quote_ok,
          f"need={prof.need} reason={prof.reason} quote_ok={prof.quote_ok}")

NEED2 = [
    "HOW DO I GET MY TOR", "when does enrollment start", "who is the cvsu president",
    "What passing rate did CvSU get in the August 2025 MedTech licensure exam?",
    "can you tell me the admission requirements?", "So how do I get my TOR?",
    "magkano ang tuition sa BS Psychology", "Sino ang pumalit kay Dr. Robles bilang presidente ng CvSU?",
    "How many CvSU examinees passed the September 2026 civil engineering board exam?",
    "how many campuses does cvsu have?", "Is CvSU better than UP Los Baños?",
]
for t in NEED2:
    prof = gg.analyze_query(t)
    check(f"information need: {t!r}", prof.need >= 1 and prof.predicate,
          f"need={prof.need} reason={prof.reason} predicate={prof.predicate}")
check("a follow-up opener keeps the need but forbids the quote",
      gg.analyze_query("So how do I get my TOR?").information_need
      and not gg.analyze_query("So how do I get my TOR?").quote_ok)
check("'how many examinees passed' is a question, not a statement",
      not gg.analyze_query("How many CvSU examinees passed the exam?").declarative)
check("date terms are collected for the quote check",
      gg.analyze_query("What was CvSU's passing rate in the May 2026 nursing licensure exam?").date_terms == ["2026", "may"]
      or set(gg.analyze_query("What was CvSU's passing rate in the May 2026 nursing licensure exam?").date_terms) == {"2026", "may"})
check("present-state who-question is flagged",
      gg.analyze_query("who is the current dean of CEIT").present_who)
check("Filipino event who-question is not present-state",
      not gg.analyze_query("sino ang nagbigay ng award").present_who)
check("English words never defeat the present-state flag",
      gg.analyze_query("Who is the current head of the Human Resource Development Office?").present_who)
check("empty / whitespace / punctuation profile as no need",
      all(gg.analyze_query(t).need == 0 for t in ("", "   ", "?", "!!!", "😂")))
check("information_need() is the corpus-free switch",
      not gg.information_need("wow, you can answer questions like that?")
      and gg.information_need("when does enrollment start"))

print("\n── decisions on the live corpora ──")
site = site_rag.get_index()
charter = charter_rag.get_index()
check("site index available", site is not None)
check("charter index available", charter is not None)


def decide(text, index):
    return gg.decide(text, index, index.retrieve(text, k=gg.RETRIEVE_K))


if site is not None and charter is not None:
    v = decide("wow, you can answer questions like that?", site)
    check("UAT utterance: nothing handed to the LLM from the site corpus",
          not v.augment and v.quote is None, v.reason)
    v = decide("can you answer questions?", site)
    check("'can you answer questions?': GAD page not quoted, not augmented",
          not v.augment and v.quote is None, v.reason)
    for text in ("Indang (Main Campus)", "Tell me about a specific CvSU campus",
                 "Who is the head of cashiers office", "Carmona Campus", "Office hour"):
        check(f"beta misquote stays unquoted (charter): {text!r}", decide(text, charter).quote is None,
              decide(text, charter).reason)
    check("thanks message naming a topic is not quoted",
          decide("salamat sa info about sa criminology board exam", site).quote is None)
    check("present-state who-question never quotes the charter",
          decide("who is the current dean of CEIT", charter).quote is None)
    check("present-state who-question never quotes a dated post",
          decide("who is the dean of CEIT", site).quote is None)
    check("date-shifted ask is not quoted from another year's article",
          decide("What was CvSU's passing rate in the April 2026 Civil Engineers Licensure Exam?", site).quote is None)
    check("statement is not quoted",
          decide("I passed the September 2025 psychometricians licensure exam", site).quote is None)
    v = decide("seagrass research Maragondon", site)
    check("answerable keyword ask is still grounded", bool(v.augment) and "seagrass" in v.augment[0][0].title.lower(),
          v.reason)
    v = decide("how did CvSU rank in the WURI 2026 list?", site)
    check("answerable question is grounded on the WURI page",
          bool(v.augment) and "wuri" in v.augment[0][0].title.lower(), v.reason)
    check("relevance is a comparable scalar in [0, 1)",
          all(0.0 <= rel < 1.0 for _, rel in v.augment))
    v = decide("when does enrollment start", site)
    check("'when does enrollment start' is no longer starved by the floor",
          bool(v.augment), v.reason)
    check("garbage input returns an empty verdict without raising",
          all(not decide(t, site).augment for t in ("", "   ", "?", "asdkjh qwe", "ang ng sa")))

print("\n── eval-set floors ──")
from scripts import eval_grounding_gate as harness  # noqa: E402

site_gate, charter_gate = harness._load_gate("api.grounding_gate")
r = harness.run(site_gate, charter_gate)
harness.print_report(r)
check("in-sample: no verbatim quote on any conversational probe", r["probes"]["quote_safe_pct"] == 100.0)
check("in-sample: no verbatim quote on any trap", r["traps"]["quote_safe_pct"] == 100.0)
check("in-sample: conversational probes stay out of the LLM's evidence (>= 95%)",
      r["probes"]["augment_safe_pct"] >= 95.0, r["probes"]["augment_safe_pct"])
check("in-sample: gold augment coverage >= 68% (legacy 60.9%, shipped 73%)",
      r["factual"]["augment_coverage_pct"] >= 68.0, r["factual"]["augment_coverage_pct"])
h = harness.run_heldout(site_gate, charter_gate)
harness.print_heldout(h)
check("held-out: conversational quote-safe >= 99% (legacy 86.7%)", h["never"]["quote_safe_pct"] >= 99.0,
      h["never"]["quote_safe_pct"])
check("held-out: conversational augment-safe >= 65% (legacy 31.5%)", h["never"]["augment_safe_pct"] >= 65.0,
      h["never"]["augment_safe_pct"])
check("held-out: quote-forbidden asks quote-safe >= 97% (legacy 65.3%)", h["noquote"]["quote_safe_pct"] >= 97.0,
      h["noquote"]["quote_safe_pct"])
check("held-out: answerable admitted >= 85% of retrievable (legacy 82%)",
      h["gold"]["admitted_pct_of_retrievable"] >= 85.0, h["gold"]["admitted_pct_of_retrievable"])
check("held-out: wrong-document quotes <= 15% of quotes issued (legacy 28%)",
      h["gold"]["wrong_doc_quotes"] <= 0.15 * max(1, h["gold"]["quotes_issued"]),
      f"{h['gold']['wrong_doc_quotes']}/{h['gold']['quotes_issued']}")
check("held-out: first questions with a follow-up opener still grounded (>= 10/12)",
      h["must_augment"]["admitted"] >= 10, h["must_augment"]["admitted"])

print(f"\n{'ALL PASS' if not FAILED else f'{FAILED} FAILURE(S)'}  ({PASSED} passed)")
sys.exit(1 if FAILED else 0)
