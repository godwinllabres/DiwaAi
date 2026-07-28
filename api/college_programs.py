"""Per-college program registry — the complete list, with its own date.

    Reads : data/college_programs.json
    Writes: nothing
    Usage : from api.college_programs import college_program_reply

WHY THIS EXISTS
---------------
Asking "full course list of CEIT" returned the generic all-colleges blurb.
Two faults met: the three CEIT patterns are owned by the `courses_offered`
intent (whose answer is that blurb) while the CEIT-specific `it_cs_courses`
intent has none, so the classifiers could only ever route CEIT queries to the
generic answer; and no curated response actually held a complete list —
`it_cs_courses` names 2 of CEIT's 10 programs.

Routing alone could not fix that, so the lists live here instead. Every entry
was transcribed verbatim from a CvSU program page mirrored in
docs/site_corpus.txt and re-verified by grepping each program name back
against its source document. Nothing is inferred: a college with no program
page in the corpus is simply absent (the College of Medicine is), which is a
better answer than a plausible invention.

DATED PROVENANCE, NOT A HEDGE
-----------------------------
Each college carries the publication date of the page it came from, and the
reply states it. Several official pages have not been edited upstream in
years — CEIT's is from 2018 — so a nightly corpus sync re-fetches the same old
page: cadence is mirror freshness, not source freshness. Telling a student
"this is CvSU's list as published on 13 January 2018" is both more useful and
more honest than "check the website", which sends them away from the assistant
that already holds the content and tells them nothing about staleness.
"""
import json
import os
import re
from typing import List, Optional

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "college_programs.json")

_COLLEGES: Optional[List[dict]] = None
_ALIAS_RE: Optional[re.Pattern] = None
_ALIAS_TO_KEY: dict = {}

# The reply is only wanted when the student is asking about programmes. A bare
# college mention ("where is CEIT", "CEIT dean") must fall through to the map
# and directory tiers that own those questions.
_PROGRAM_CUE_RE = re.compile(
    r"\b(course|courses|program|programs|programme|programmes|offering|offerings|"
    r"curricul\w*|degree|degrees|major|majors|kurso|kursong|programa|"
    r"what\s+can\s+i\s+(?:take|study|enroll)|anong\s+kurso)\b",
    re.IGNORECASE,
)


def _load() -> List[dict]:
    global _COLLEGES, _ALIAS_RE
    if _COLLEGES is not None:
        return _COLLEGES
    try:
        with open(os.path.abspath(_PATH), "r", encoding="utf-8") as fh:
            _COLLEGES = json.load(fh)
    except (OSError, ValueError):
        _COLLEGES = []
    alts = []
    for college in _COLLEGES:
        for alias in college.get("aliases", []):
            _ALIAS_TO_KEY[alias] = college["key"]
            alts.append(re.escape(alias))
    # Longest first so "college of engineering and information technology" wins
    # over the bare "ceit" nested inside no alias but sharing a prefix elsewhere.
    alts.sort(key=len, reverse=True)
    _ALIAS_RE = re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(alts),
                           re.IGNORECASE) if alts else None
    return _COLLEGES


def find_college(text: str) -> Optional[dict]:
    """The college named in `text`, full or abbreviated, else None."""
    colleges = _load()
    if not text or not _ALIAS_RE:
        return None
    match = _ALIAS_RE.search(text)
    if not match:
        return None
    key = _ALIAS_TO_KEY.get(match.group(0).lower())
    return next((c for c in colleges if c["key"] == key), None)


def _format_date(iso: str) -> str:
    """2018-01-13 -> 13 January 2018; anything unexpected passes through."""
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    try:
        year, month, day = iso.split("-")
        return f"{int(day)} {months[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return iso


def format_college(college: dict, filipino: bool = False) -> str:
    """The college's complete program list, with the source page's own date."""
    lines = []
    for program in college["programs"]:
        lines.append(f"- {program['name']}")
        for major in program.get("majors", []):
            lines.append(f"    - Major in {major}")
    count = len(college["programs"])
    published = _format_date(college.get("source_date", ""))
    if filipino:
        header = (f"Ito ang kompletong listahan ng {count} programa ng "
                  f"{college['full_name']} ({college['abbr']}):")
        footer = (f"Pinagmulan: opisyal na pahina ng CvSU, huling na-update "
                  f"noong {published} — {college.get('source_url', '')}")
    else:
        header = (f"{college['full_name']} ({college['abbr']}) offers these "
                  f"{count} programs:")
        footer = (f"Source: the official CvSU page for this college, last "
                  f"published {published} — {college.get('source_url', '')}")
    return "\n".join([header, "", *lines, "", footer])


def college_program_reply(text: str, filipino: bool = False) -> Optional[str]:
    """Complete program list when `text` names a college AND asks about
    programs; None otherwise so the normal cascade handles it."""
    if not text or not _PROGRAM_CUE_RE.search(text):
        return None
    college = find_college(text)
    if not college or not college.get("programs"):
        return None
    return format_college(college, filipino=filipino)
