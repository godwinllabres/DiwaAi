"""Offline regression for response selection and the officials data — run:
    python test_response_selection.py

Covers, without loading models or starting a server:
  • language detection used to pick a response variant
  • the acknowledgement intent (bare "okay"/"test" must not get a bot bio)
  • officials accuracy invariants against the 2026 CvSU roster
See the live-log diagnosis: one user asked "What facilities are available on
campus?" in English and got a Taglish answer, then typed "Okay" and got a full
self-introduction.
"""
import json

from api.hybrid_chatbot import _filipino_ratio, _is_filipino

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}" if ok
          else f"FAIL  {name}\n        got={got!r}\n        want={want!r}")


def check_true(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


intents = {i["tag"]: i for i in
           json.load(open("data/cavsu_intents.json", encoding="utf-8"))["intents"]}
rmap = json.load(open("models/responses_map.json", encoding="utf-8"))

# ── language detection ───────────────────────────────────────────────────────
print("── language detection ──")
check_true("plain English question", not _is_filipino("What facilities are available on campus?"))
check_true("English keyword query", not _is_filipino("list of cvsu courses"))
check_true("English dean query", not _is_filipino("who is the dean of ceit"))
check_true("Taglish question", _is_filipino("Ano ang mga kurso sa CvSU?"))
check_true("Filipino question", _is_filipino("Kelan magsisimula ang enrollment?"))
check_true("bare 'sige' reads Filipino", _is_filipino("sige"))
check_true("empty string is not Filipino", not _is_filipino(""))
# Relative ranking is what _select_response uses; a Filipino answer padded with
# English proper nouns still has to out-rank its English twin.
fil_list = ("Ayon sa opisyal na listahan ng CvSU, ito ang mga dean: "
            "CEIT (College of Engineering and Information Technology): Dr. Willie C. Buclatin")
en_list = ("Per the CvSU officials roster, the deans are: "
           "CEIT (College of Engineering and Information Technology): Dr. Willie C. Buclatin")
check_true("Filipino variant out-ranks its English twin",
           _filipino_ratio(fil_list) > _filipino_ratio(en_list))

# ── acknowledgement split ────────────────────────────────────────────────────
print("\n── acknowledgement intent ──")
check_true("acknowledgement intent exists", "acknowledgement" in intents)
ack = intents.get("acknowledgement", {"patterns": [], "responses": []})
for p in ("okay", "ok", "sige", "test", "noted", "got it"):
    check_true(f"'{p}' is an acknowledgement pattern",
               p in [x.lower() for x in ack["patterns"]])
chit = [x.lower() for x in intents["chitchat"]["patterns"]]
for p in ("okay", "ok", "sige", "test"):
    check_true(f"'{p}' no longer in chitchat", p not in chit)
check_true("chitchat keeps bot-identity patterns", "who are you" in chit)
check_true("chitchat keeps help patterns", "can you help me" in chit)
# The whole point: an acknowledgement must not answer with a self-introduction.
for r in ack["responses"]:
    check_true(f"ack response is not a bio ({r[:28]}...)",
               "I'm Sevi" not in r and "I am Sevi" not in r and len(r) < 260)
check_true("acknowledgement is in responses_map", "acknowledgement" in rmap)

# ── officials accuracy (roster effective 1 Jan – 31 Dec 2026) ────────────────
print("\n── officials accuracy ──")
uo = " ".join(intents["university_officials"]["responses"])
check_true("names the President", "Ma. Agnes P. Nuestro" in uo)
for vp in ("Cristina M. Signo", "Melbourne R. Talactac", "John Xavier B. Nepomuceno",
           "Mary Jane D. Tepora", "Almira G. Magcawas"):
    check_true(f"lists VP {vp}", vp in uo)
# Regression: the old copy asserted a Board Secretary. The 2026 roster has no
# such row and lists her as Director, Presidential Management Coordinating Office.
check_true("does NOT assert a Board Secretary", "Board Secretary" not in uo)
check_true("cites the roster source", "contact-us-2" in uo)

deans = " ".join(intents["college_deans"]["responses"])
check_true("CEIT dean is Buclatin", "Willie C. Buclatin" in deans)
check_true("CAS dean is Ferrer", "Ammie P. Ferrer" in deans)
check_true("CON dean is Del Mundo", "Evelyn M. Del Mundo" in deans)
# Both names the LLM previously extracted from stale news posts for CEIT.
check_true("stale 'David L. Cero' is absent", "Cero" not in deans)
check_true("Del Mundo is NOT attributed to CEIT",
           "College of Engineering and Information Technology): Dr. Evelyn" not in deans)
check_true("deans cite the roster", "contact-us-2" in deans)

campus = " ".join(intents["campus_officials"]["responses"])
check_true("Imus administrator present", "Armi Grace B. Desingaño" in campus)
check_true("Maragondon uses 'Principal'", "Principal" in campus)
check_true("campus list cites the roster", "contact-us-2" in campus)

# Every intent the new work touched must be servable.
for tag in ("acknowledgement", "college_deans", "campus_officials", "university_officials"):
    check_true(f"{tag} has responses in responses_map", bool(rmap.get(tag)))

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
