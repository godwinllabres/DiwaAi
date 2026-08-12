"""Offline regression for chat-shorthand expansion and the charter quote floor
— run:  python test_shorthand_expansion.py

Both fixes come from the 2026 tester round:
  • "Admission Reqs" fell to the fallback while "Admission Requirements"
    answered correctly — "a lot of students like myself use abbreviations or
    casual wording when chatting with an AI".
  • The verbatim tier quoted "Issuance of School Identification Card
    (Replacement)" at 0.1211, with a page citation, for a question about
    paying an OJT fee.

No models are loaded: this covers the expansion contract and the threshold
itself. The end-to-end routing is exercised by the pipeline probes.
"""
from api.preprocessing import _ABBREVIATIONS, expand_abbreviations
from api import charter_rag, site_rag

failures = 0


def check_true(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# ── expansion contract ───────────────────────────────────────────────────────
print("── shorthand expansion ──")
for short, long in [("reqs", "requirements"), ("req", "requirement"),
                    ("info", "information"), ("sched", "schedule"),
                    ("schol", "scholarship"), ("dept", "department"),
                    ("uni", "university"), ("sem", "semester")]:
    check_true(f"{short!r} -> {long!r}", expand_abbreviations(short) == long)

check_true("expands inside a sentence",
           expand_abbreviations("admission reqs") == "admission requirements")
check_true("expands several tokens",
           expand_abbreviations("schol info") == "scholarship information")
check_true("leaves unknown words alone",
           expand_abbreviations("admission requirements") == "admission requirements")
check_true("empty string is safe", expand_abbreviations("") == "")

# Whole tokens only. A substring pass turns "prerequisite" into
# "prerequirementuisite" and "coreq" into something worse.
check_true("does NOT touch substrings",
           expand_abbreviations("prerequisite reqs") == "prerequisite requirements")
check_true("does NOT split hyphen-joined tokens",
           expand_abbreviations("coreq") == "coreq")

# Words that are also ordinary English, or carry a second campus meaning, must
# never be expanded — a wrong expansion silently rewrites a good question.
for keep in ("admin", "app", "reg", "gen", "ed", "con", "cas"):
    check_true(f"{keep!r} is left alone", expand_abbreviations(keep) == keep)

# Every value must itself be stable, or a second pass would keep rewriting.
for short, long in _ABBREVIATIONS.items():
    check_true(f"idempotent: {short!r}", expand_abbreviations(long) == long)

# ── charter quote floor ──────────────────────────────────────────────────────
print("\n── verbatim quote floors ──")
check_true("charter floor raised to site's 0.15", charter_rag.QUOTE_MIN_SCORE == 0.15)
check_true("charter quote floor >= site quote floor",
           charter_rag.QUOTE_MIN_SCORE >= site_rag.QUOTE_MIN_SCORE)
# Augmentation stays permissive on purpose — the LLM can ignore a weak excerpt,
# a verbatim quote with a page number cannot be ignored by the reader.
check_true("augmentation floor still below the quote floor",
           charter_rag.AUGMENT_MIN_SCORE < charter_rag.QUOTE_MIN_SCORE)

# The measured regression: the OJT passage scored 0.1211, so any floor at or
# below 0.1211 lets a confident citation to an unrelated procedure through.
check_true("floor excludes the 0.1211 OJT false quote",
           charter_rag.QUOTE_MIN_SCORE > 0.1211)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
