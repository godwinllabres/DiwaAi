"""Response linter (P2-7, HANDOFF-QUALITY 2026-08-03).

Lints every curated response in data/cavsu_intents.json against the response
style contract, and every contact detail against the verified sources:

  vague_phrase       hedging language with no concrete answer behind it
                     ("it depends", "may vary", "the office concerned", ...)
  missing_date       a fee / schedule / requirement answer with no effective
                     date ("As of AY ...", "per the Citizens' Charter", ...)
  unverified_email   an email that is not in the verified campus directory
  unverified_url     a URL that is neither a verified source binding
                     (data/intent_sources.json) nor the official portal root
  buried_answer      a long response whose first sentence is a question or a
                     hedge instead of a direct answer

The email/URL rules are the regression fence for the class of bug fixed in
P0-2: all 4 wrong directory emails would have been caught here.

Allowlist workflow (the handoff's "exit non-zero on NEW violations"):
  python scripts/lint_responses.py                    # gate: fails on NEW only
  python scripts/lint_responses.py --update-allowlist # accept current findings
  python scripts/lint_responses.py --strict           # fail on ANY violation

A violation key includes a hash of the response text, so *editing* a response
re-lints it from scratch — the allowlist only shields text that has not
changed since it was accepted. The end state (P2-9 migration) is an empty
allowlist with --strict in CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INTENTS_PATH = ROOT / "data" / "cavsu_intents.json"
SOURCES_PATH = ROOT / "data" / "intent_sources.json"
ALLOWLIST_PATH = ROOT / "scripts" / "lint_responses_allowlist.json"

# ── Rule definitions ─────────────────────────────────────────────────────────

VAGUE_PHRASES = [
    "it depends",
    "may vary",
    "the office concerned",
    "office concerned",
    "you may inquire",
    "depende sa",
    "maaaring magbago",
]
# "usually" only as a standalone hedge opening a clause — mid-sentence
# "usually the first 1-2 weeks" is a concrete qualifier, not a dodge.
VAGUE_OPENER_RE = re.compile(r"(?:^|\.\s+)(?:usually|it varies|it depends)\b", re.IGNORECASE)

# Responses that talk about money / dates / requirements need an effective date.
DATED_TOPIC_TAG_RE = re.compile(
    r"tuition|fee|schedule|calendar|deadline|requirement", re.IGNORECASE
)
PESO_RE = re.compile(r"(?:₱|\bPHP?\s?\d)")
DATE_MARKER_RE = re.compile(
    r"\bas of\b|\bAY\s*20\d\d|\bA\.?Y\.?\s*20\d\d|Citizens'? Charter|\bFY\s*20\d\d"
    r"|\beffective\b|\b20\d\d[-–]20\d\d\b|\bRA\s?\d{4,5}\b",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>\)\]\"',]+")
# The bare official portal is trivially correct and appears as the generic
# verification pointer throughout the corpus; subdomains are still checked.
PORTAL_ROOTS = ("https://cvsu.edu.ph", "http://cvsu.edu.ph", "https://www.cvsu.edu.ph")

BURIED_MIN_CHARS = 300
HEDGE_OPENER_RE = re.compile(
    r"^(?:it depends|well[, ]|there are (?:several|many)|usually|it varies"
    r"|maraming|depende|that is a good question)",
    re.IGNORECASE,
)


def verified_emails() -> set[str]:
    """Every email in the verified campus directory (api/campus_places.py)."""
    from api import campus_places

    emails: set[str] = set()
    for directory in campus_places._INTENT_TO_DIRECTORY.values():
        if directory.email:
            emails.add(directory.email.lower())
    for value in vars(campus_places).values():
        if isinstance(value, str) and EMAIL_RE.fullmatch(value):
            emails.add(value.lower())
    return emails


def verified_urls() -> set[str]:
    """Every site-source URL bound in data/intent_sources.json."""
    urls: set[str] = set()
    try:
        bindings = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))["bindings"]
    except (OSError, ValueError, KeyError):
        return urls
    for refs in bindings.values():
        for ref in refs:
            locator = str(ref.get("locator", ""))
            if locator.startswith("http"):
                urls.add(locator.rstrip("/").lower())
    return urls


def first_sentence(text: str) -> str:
    stripped = text.strip()
    for match in re.finditer(r"[.!?](?:\s|$)", stripped):
        return stripped[: match.end()].strip()
    return stripped.split("\n", 1)[0]


def lint_response(tag: str, response: str, emails: set[str], urls: set[str]):
    """Yield (rule, detail) violations for one response."""
    lowered = response.lower()

    for phrase in VAGUE_PHRASES:
        if phrase in lowered:
            yield "vague_phrase", phrase
    if VAGUE_OPENER_RE.search(response):
        yield "vague_phrase", "hedge opener"

    if (DATED_TOPIC_TAG_RE.search(tag) or PESO_RE.search(response)) and not DATE_MARKER_RE.search(response):
        yield "missing_date", ""

    for email in set(EMAIL_RE.findall(response)):
        if email.lower() not in emails:
            yield "unverified_email", email
    for url in set(URL_RE.findall(response)):
        trimmed = url.rstrip(".,;:!?/").lower()
        if trimmed in (root.lower() for root in PORTAL_ROOTS):
            continue
        if trimmed not in urls:
            yield "unverified_url", trimmed

    if len(response) >= BURIED_MIN_CHARS:
        head = first_sentence(response)
        if head.endswith("?") or HEDGE_OPENER_RE.search(head):
            yield "buried_answer", head[:60]


def violation_key(tag: str, rule: str, detail: str, response: str) -> str:
    digest = hashlib.sha1(response.encode("utf-8")).hexdigest()[:10]
    return f"{tag}|{rule}|{detail}|{digest}"


def collect_violations():
    emails = verified_emails()
    urls = verified_urls()
    doc = json.loads(INTENTS_PATH.read_text(encoding="utf-8"))
    found = []
    for intent in doc["intents"]:
        tag = intent["tag"]
        for response in intent.get("responses", []):
            for rule, detail in lint_response(tag, response, emails, urls):
                found.append({
                    "key": violation_key(tag, rule, detail, response),
                    "intent": tag, "rule": rule, "detail": detail,
                    "snippet": response.strip().replace("\n", " ")[:90],
                })
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update-allowlist", action="store_true",
                        help="accept every current violation into the allowlist")
    parser.add_argument("--strict", action="store_true",
                        help="ignore the allowlist; fail on any violation")
    args = parser.parse_args()

    found = collect_violations()

    if args.update_allowlist:
        ALLOWLIST_PATH.write_text(json.dumps({
            "_comment": ("Accepted pre-existing lint violations. The P2-9 response "
                         "migration drives this to empty; editing a response drops "
                         "its entries automatically (keys hash the text)."),
            "accepted": sorted(v["key"] for v in found),
        }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"allowlist updated: {len(found)} accepted violations")
        return 0

    accepted: set[str] = set()
    if not args.strict and ALLOWLIST_PATH.exists():
        accepted = set(json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8")).get("accepted", []))

    new = [v for v in found if v["key"] not in accepted]
    stale = accepted - {v["key"] for v in found}

    by_rule: dict[str, int] = {}
    for v in found:
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
    print(f"lint_responses: {len(found)} total "
          f"({', '.join(f'{r}={n}' for r, n in sorted(by_rule.items()))}); "
          f"{len(found) - len(new)} allowlisted, {len(new)} NEW"
          + (f", {len(stale)} stale allowlist entries" if stale else ""))

    if new:
        print("\nNEW violations (fix the response, or --update-allowlist to accept):")
        for v in new:
            detail = f" [{v['detail']}]" if v["detail"] else ""
            print(f"  {v['intent']} · {v['rule']}{detail}\n      {v['snippet']}")
        return 1
    print("OK: no new violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
