"""Offline regression for api/campus_context.py — run: python test_campus_context.py"""
from api.campus_context import CAMPUSES, extract_campus, resolve

failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")


# --- extraction ---
check("extract imus", extract_campus("I study at CvSU Imus"), "Imus City Campus")
check("extract gentri alias", extract_campus("taga gentri ako"), "General Trias City Campus")
check("extract none", extract_campus("where is the library?"), None)

# --- flow: ambiguous question with NO known campus -> clarify, then resume ---
s = "flow-1"
r = resolve(s, "Where is the campus located?")
check("no-campus location q clarifies", r.action, "clarify")
r = resolve(s, "Bacoor")
check("campus answer resumes pending", r.action, "answer_pending")
check("resumed message carries campus", "Bacoor City Campus" in (r.message or ""), True)
check("resumed message carries question", "Where is the campus located?" in (r.message or ""), True)

# --- flow: campus learned from an earlier statement -> augment later ---
s = "flow-2"
r = resolve(s, "hello po, taga-Imus ako")
check("statement stores campus", r.campus, "Imus City Campus")
r = resolve(s, "saan po located ang campus?")
check("follow-up augments", r.action, "augment")
check("augmented carries imus", "Imus City Campus" in (r.message or ""), True)

# --- explicit campus in the question itself -> no rewrite needed ---
s = "flow-3"
r = resolve(s, "where is the Naic campus located?")
check("explicit campus passes through", r.action, "none")
check("explicit campus remembered", r.campus, "Naic Campus")

# --- non-campus questions are untouched ---
s = "flow-4"
r = resolve(s, "where is the library?")
check("specific place not intercepted", r.action, "none")
r = resolve(s, "what are the admission requirements?")
check("non-location q untouched", r.action, "none")

# --- a lone campus word without a pending question just updates state ---
s = "flow-5"
r = resolve(s, "Silang")
check("bare campus, no pending -> none", r.action, "none")
check("bare campus stored", r.campus, "Silang Campus")

# --- F7: pending is single-shot; a later short campus-mention question does
#     NOT resume the stale parked question ---
s = "flow-hijack"
resolve(s, "Where is the campus located?")            # parks
r = resolve(s, "Imus tuition fee?")                    # 3 words, mentions campus, but a NEW question
check("short new question does not hijack pending", r.action != "answer_pending", True)
r = resolve(s, "Naic")                                 # a later bare campus must NOT resume the long-gone question
check("pending not resurrected turns later", r.action, "none")

# --- F7: a genuine bare-campus reply on the immediately-next turn DOES resume ---
s = "flow-resume"
resolve(s, "Where is the campus located?")
r = resolve(s, "sa Bacoor po")                          # filler + campus = bare answer
check("bare campus reply (with filler) resumes", r.action, "answer_pending")

# --- F5: two stateless (no session_key) callers must not share state ---
r1 = resolve(None, "Where is the campus located?")      # user A, no session
check("stateless caller still clarifies", r1.action, "clarify")
r2 = resolve(None, "Naic")                              # user B, no session
check("stateless caller B does NOT resume A's question", r2.action, "none")

# --- specific-place questions stay with map intents ---
s = "flow-place"
resolve(s, "taga Imus ako")                             # campus known
r = resolve(s, "where is the CvSU library?")
check("specific-place question not intercepted", r.action, "none")

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILURES'} | campuses={len(CAMPUSES)}")
raise SystemExit(1 if failures else 0)
