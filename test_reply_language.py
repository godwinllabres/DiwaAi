"""Offline regression for the two UAT-driven reply controls — run:
    python test_reply_language.py

Covers, without loading models or starting a server:
  • the reply-language preference (ChatRequest.language -> _reply_in_filipino)
  • the "which college?" clarification and its chip round-trip

Both come from the July/August 2026 tester round:
  - "Allow users to select their preferred language (English or Filipino)
     before initiating the conversation." (staff, PDO) — detection alone
     cannot serve a reader who is not the person typing.
  - "the programs offered by our college" (faculty, CAS) — programs was the
     weakest-rated task of the round; the ask names no college, so the tier
     that needs one returned None and the generic all-colleges blurb won.
"""
from api.hybrid_chatbot import REPLY_LANGUAGES, _is_filipino, _reply_in_filipino
from api.college_programs import (
    college_program_clarification,
    college_program_reply,
    find_college,
)

failures = 0


def check_true(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# ── reply-language preference ────────────────────────────────────────────────
print("── reply-language preference ──")
check_true("allowlist is exactly en/fil/auto", REPLY_LANGUAGES == {"en", "fil", "auto"})

EN_Q = "what are the enrollment requirements"
FIL_Q = "kelan ang enrollment"

# Baseline: no preference must behave exactly as detection did before.
check_true("no preference -> detection (English)", _reply_in_filipino(EN_Q, None) is False)
check_true("no preference -> detection (Filipino)", _reply_in_filipino(FIL_Q, None) is True)
check_true("no preference matches _is_filipino exactly",
           all(_reply_in_filipino(q, None) == _is_filipino(q)
               for q in (EN_Q, FIL_Q, "", "sige", "who is the dean of ceit")))

# The point of a selector: it has to beat detection in BOTH directions, or the
# reader who set it still gets the language the question happened to be in.
check_true("'fil' overrides an English question", _reply_in_filipino(EN_Q, "fil") is True)
check_true("'en' overrides a Filipino question", _reply_in_filipino(FIL_Q, "en") is False)
check_true("'auto' falls back to detection (English)", _reply_in_filipino(EN_Q, "auto") is False)
check_true("'auto' falls back to detection (Filipino)", _reply_in_filipino(FIL_Q, "auto") is True)

# A preference that slipped past validation must cost the turn its preference,
# never its answer — same posture as the device_class validator.
for bad in ("", "tagalog", "EN", "en-US", "xx", None):
    check_true(f"unrecognised {bad!r} degrades to detection",
               _reply_in_filipino(FIL_Q, bad) == _is_filipino(FIL_Q))

# ── "which college?" clarification ───────────────────────────────────────────
print("\n── which-college clarification ──")

FIRES = [
    "the programs offered by our college",   # verbatim from the feedback form
    "what programs does my college offer",
    "courses in our college",
    "anong kurso sa aming kolehiyo",
]
for q in FIRES:
    check_true(f"asks which college: {q!r}", college_program_clarification(q) is not None)

# Must stay off everything the existing tiers already answer correctly.
QUIET = [
    ("what programs does CEIT offer", "names a college -> full list wins"),
    ("courses offered by the College of Nursing", "names a college in full"),
    ("what programs does CvSU offer", "general ask -> courses_offered owns it"),
    ("where is our college", "no program cue"),
    ("who is the dean of our college", "no program cue"),
    ("", "empty input"),
]
for q, why in QUIET:
    check_true(f"stays quiet ({why})", college_program_clarification(q) is None)

# The chips are sent back as the next message verbatim, so each one must route
# into the complete-list reply. A bare "CEIT" does NOT (it carries no program
# cue and lands on college_deans) — which is why the label includes it.
res = college_program_clarification("the programs offered by our college")
check_true("clarification returns (text, chips)", res is not None and len(res) == 2)
text, chips = res
check_true("asks a question", text.strip().endswith("?"))
check_true("offers every college with programs", len(chips) >= 10)
for chip in chips:
    reply = college_program_reply(chip)
    check_true(f"chip round-trips: {chip!r}", bool(reply) and "offers" in reply)
    check_true(f"chip names a real college: {chip!r}", find_college(chip) is not None)
    check_true(f"chip does not re-ask: {chip!r}", college_program_clarification(chip) is None)

# Filipino wording is served when the reply language says so.
fil = college_program_clarification("the programs offered by our college", filipino=True)
check_true("Filipino clarification is Filipino", fil is not None and "kolehiyo" in fil[0])
check_true("Filipino clarification keeps the same chips", fil is not None and fil[1] == chips)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
