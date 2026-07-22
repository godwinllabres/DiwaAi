"""Offline regression for the campus directory gate — run:
    python test_campus_directory.py

Locks the fix for the screenshot failure where "Where is the campus
located?" (session campus: General Trias) was answered with a Citizens'
Charter COVER PAGE quote plus the Indang map card:
  • every campus_context campus has a charter-grounded directory entry;
  • satellite location asks are claimed by the directory gate BEFORE any
    retrieval tier;
  • the main campus keeps its intent-tier answer (richer text + real map);
  • the Indang map is never attached to a satellite conversation.
"""
import sys

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api import campus_context, campus_directory  # noqa: E402

checks = 0
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks, failures
    checks += 1
    if condition:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}  {detail}")


# ─── data coverage: context keys <-> directory keys are the same set ─────────
print("[data] coverage")
ctx = set(campus_context.CAMPUSES)
dirs = set(campus_directory.DIRECTORY)
check("every campus has a directory entry", ctx == dirs,
      f"missing={ctx - dirs} extra={dirs - ctx}")
for campus, info in campus_directory.DIRECTORY.items():
    check(f"address present: {campus}", bool(info.address and "Cavite" in info.address))

# ─── location-question detector ──────────────────────────────────────────────
print("\n[detector] is_campus_location_question")
for text in [
    "Where is the campus located?",
    "Where is CvSU Imus campus?",
    "saan ang cvsu maragondon campus",
    "What is the address of CvSU Naic?",
    "How do I get to the CvSU General Trias campus?",
]:
    check(f"location ask: {text}", campus_context.is_campus_location_question(text))
for text in [
    "where is the CvSU library",             # place inside a campus → map intent
    "where is the registrar office",         # same
    "What courses are offered at CvSU Imus?",
    "thank you po",
]:
    check(f"not a campus ask: {text}", not campus_context.is_campus_location_question(text))

# ─── gate: who claims the turn ───────────────────────────────────────────────
print("\n[gate] is_directory_turn")
MAIN = campus_directory.MAIN_CAMPUS

routing = campus_context.resolve(None, "Where is CvSU Imus campus?")
check("inline satellite → directory",
      campus_directory.is_directory_turn("Where is CvSU Imus campus?", routing.campus))

routing = campus_context.resolve(None, "Where is the main campus located?")
check("main campus → cascade (richer intent answer + map)",
      not campus_directory.is_directory_turn("Where is the main campus located?", routing.campus))

check("no campus → cascade/clarify",
      not campus_directory.is_directory_turn("Where is the campus located?", None))
check("satellite but non-location ask → cascade",
      not campus_directory.is_directory_turn(
          "What courses are offered po?", "Imus City Campus"))

# the screenshot scenario: campus remembered from earlier in the session
session = "t-gentri"
campus_context.resolve(session, "how about sa CvSU General Trias?")
routing = campus_context.resolve(session, "Where is the campus located?")
check("screenshot repro: augmented follow-up → directory",
      routing.action == "augment"
      and campus_directory.is_directory_turn(routing.message, routing.campus),
      f"got action={routing.action} campus={routing.campus}")

# ─── answers ─────────────────────────────────────────────────────────────────
print("\n[answer] build_answer")
text, info = campus_directory.build_answer("General Trias City Campus")
check("address in answer", "Brgy. Vibora, General Trias City, Cavite" in text)
check("phone in answer", "(046) 509-4148" in text)
check("citation in answer", "Citizens' Charter" in text and "2024" in text)
check("display name", info.display_name == "CvSU General Trias City Campus")

text, _ = campus_directory.build_answer("Naic Campus")
check("no-email campus still answers", "Naic, Cavite" in text)

# ─── satellite map guard ─────────────────────────────────────────────────────
print("\n[cards] is_satellite")
check("satellite true", campus_directory.is_satellite("Bacoor City Campus"))
check("main is not satellite", not campus_directory.is_satellite(MAIN))
check("None is not satellite", not campus_directory.is_satellite(None))
check("unknown name is not satellite", not campus_directory.is_satellite("Mars Campus"))

print(f"\n{checks - failures}/{checks} passed")
raise SystemExit(1 if failures else 0)
