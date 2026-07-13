"""
One-shot cleanup of cavsu_intents.json.

Removes three categories of noise introduced by the feedback ingest loop:
  1. Duplicate-prefix patterns  ("Tell me about Tell me about X")
  2. Wrapper-bloated patterns   ("Please explain Does CvSU have X for 2026?")
  3. Extreme-length patterns    (< 4 chars or > 80 chars after stripping)

Run:
  py -3.11 clean_patterns.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INTENTS_PATH = ROOT / "data" / "cavsu_intents.json"

# ---------------------------------------------------------------------------
# Wrappers injected by the edge-case batch script and feedback pipeline
# ---------------------------------------------------------------------------
STRIP_PREFIXES = [
    "sana matulungan mo ako sa ",
    "gusto ko pong malaman ang ",
    "hi, i just want to ask - ",
    "quick question - ",
    "i have a question about ",
    "can i ask about ",
    "please help me with ",
    "i want to know ",
    "can you tell me ",
    "i need information about ",
    "tell me about ",
    "please explain ",
    "can you help me with ",
    "can you explain ",
    "give me details on ",
    "i'm asking about ",
    "share information on ",
    "i need to know ",
]

STRIP_SUFFIXES = [
    " please answer asap.",
    " asap po",
    " it's urgent.",
    " for new students?",
    " for freshmen?",
    " for 2026?",
    " this year?",
    " for cvsu indang?",
    " for cvsu imus?",
    " for cvsu bacoor?",
    " for the main campus?",
    " by email?",
    " by phone?",
    " today?",
    " right now?",
    " as a transfer student?",
    " for international students?",
    " thanks!",
    " please.",
    " po?",
    " please?",
]


def strip_wrappers(text: str) -> str:
    """Iteratively strip known prefixes and suffixes until stable."""
    t = text.strip()
    changed = True
    while changed:
        changed = False
        low = t.lower()
        for p in STRIP_PREFIXES:
            if low.startswith(p):
                t = t[len(p):].strip()
                low = t.lower()
                changed = True
        for s in STRIP_SUFFIXES:
            if low.endswith(s):
                t = t[: -len(s)].strip()
                low = t.lower()
                changed = True
    return t


def has_duplicate_prefix(text: str) -> bool:
    """Detect patterns where first N words repeat immediately (2 ≤ N ≤ 4)."""
    words = text.split()
    for n in range(2, 5):
        if len(words) >= 2 * n and words[:n] == words[n: 2 * n]:
            return True
    return False


def clean_pattern(raw: str) -> str | None:
    """
    Return a cleaned version of the pattern, or None to drop it entirely.
    """
    # Fix duplicate prefixes first (strip one full copy)
    if has_duplicate_prefix(raw):
        words = raw.split()
        for n in range(2, 5):
            if len(words) >= 2 * n and words[:n] == words[n: 2 * n]:
                raw = " ".join(words[n:]).strip()
                break

    cleaned = strip_wrappers(raw)

    # Drop if too short or too long after cleaning
    if len(cleaned) < 4 or len(cleaned) > 80:
        return None

    return cleaned


def main() -> None:
    with open(INTENTS_PATH, "r", encoding="utf-8") as f:
        doc = json.load(f)

    total_before = sum(len(i["patterns"]) for i in doc["intents"])
    total_fixed = total_dropped = 0

    for intent in doc["intents"]:
        seen: set[str] = set()
        new_patterns: list[str] = []

        for raw in intent["patterns"]:
            cleaned = clean_pattern(raw)
            if cleaned is None:
                total_dropped += 1
                continue
            if cleaned != raw:
                total_fixed += 1
            if cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                new_patterns.append(cleaned)
            else:
                total_dropped += 1  # post-clean duplicate

        intent["patterns"] = new_patterns

    total_after = sum(len(i["patterns"]) for i in doc["intents"])

    output = json.dumps(doc, indent=2, ensure_ascii=False)
    with open(INTENTS_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    print("=== Pattern Cleanup Complete ===")
    print(f"Patterns before : {total_before}")
    print(f"Patterns after  : {total_after}")
    print(f"Fixed (stripped): {total_fixed}")
    print(f"Dropped (noise) : {total_dropped}")
    print(f"Net removed     : {total_before - total_after}")
    print()
    print("Per-intent breakdown:")
    for intent in sorted(doc["intents"], key=lambda x: x["tag"]):
        print(f"  {intent['tag']:<30} {len(intent['patterns'])} patterns")


if __name__ == "__main__":
    main()
