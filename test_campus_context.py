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

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILURES'} | campuses={len(CAMPUSES)}")
raise SystemExit(1 if failures else 0)
