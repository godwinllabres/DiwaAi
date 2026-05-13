"""Date-aware topic recommendations for the DIWA homepage.

CvSU Academic Calendar:
  First Semester  : July – December
    Registration  : May 15 – June 30
    Classes start : July 1
    Commencement  : December 15-16

  Second Semester : January – June
    Registration  : November 15 – December 31
    Classes start : January 2
    Commencement  : May 25-26

We surface the topics most likely to be on the visitor's mind right now,
before they type. Falls back to a balanced default when nothing strongly
matches the current month.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional


# ---------------------------------------------------------------------------
# Seasonal program — keep this in sync with the academic calendar intent and
# any official announcements. Each season carries:
#   - a human label for the UI heading
#   - a short reason string ("Enrollment is open this week")
#   - ranked tags (highest first)
# ---------------------------------------------------------------------------

class Season:
    __slots__ = ("key", "label", "reason", "tags")

    def __init__(self, key: str, label: str, reason: str, tags: List[str]):
        self.key = key
        self.label = label
        self.reason = reason
        self.tags = tags


SEASONS: List[Season] = [
    # May–June: 1st sem registration open (May 15 – June 30)
    Season(
        key="enrollment_first_sem",
        label="1st-semester enrollment is open",
        reason="Registration for the 1st semester runs until June 30.",
        tags=[
            "enrollment_procedure",
            "enrollment_schedule",
            "tuition_fees",
            "admissions_requirements",
            "scholarship",
            "registrar",
        ],
    ),
    # July–August: 1st sem just started (classes begin July 1)
    Season(
        key="first_sem_start",
        label="First semester has started",
        reason="Classes began July 1 — orientation and schedule questions are common.",
        tags=[
            "academic_calendar",
            "courses_offered",
            "campus_facilities",
            "campus_location",
            "registrar",
            "scholarship",
        ],
    ),
    # September–October: 1st sem midterms
    Season(
        key="first_sem_midterms",
        label="First semester — midterm period",
        reason="Midterms are underway — academic policy questions peak now.",
        tags=[
            "academic_policies",
            "academic_calendar",
            "registrar",
            "campus_facilities",
            "scholarship",
        ],
    ),
    # November–December: 1st sem ending + 2nd sem registration opens (Nov 15–Dec 31)
    # + 1st sem commencement (Dec 15-16)
    Season(
        key="enrollment_second_sem",
        label="2nd-semester enrollment is open",
        reason="Registration for the 2nd semester runs November 15 – December 31.",
        tags=[
            "enrollment_procedure",
            "enrollment_schedule",
            "tuition_fees",
            "registrar",
            "academic_calendar",
            "scholarship",
        ],
    ),
    # January–April: 2nd sem ongoing (classes start Jan 2, midterms ~Mar)
    Season(
        key="second_sem_ongoing",
        label="Second semester is ongoing",
        reason="2nd semester is in session — common questions about courses and schedules.",
        tags=[
            "academic_calendar",
            "courses_offered",
            "academic_policies",
            "registrar",
            "campus_facilities",
            "scholarship",
        ],
    ),
]


def _season_for(today: date) -> Season:
    """Map a calendar month to a Season based on the CvSU academic calendar."""
    m = today.month
    if m in (5, 6):
        return SEASONS[0]  # 1st sem registration (May 15 – June 30)
    if m in (7, 8):
        return SEASONS[1]  # 1st sem just started (July 1)
    if m in (9, 10):
        return SEASONS[2]  # 1st sem midterms
    if m in (11, 12):
        return SEASONS[3]  # 2nd sem registration (Nov 15 – Dec 31) + 1st sem commencement
    # January – April: 2nd sem ongoing (starts Jan 2, commencement May 25-26)
    return SEASONS[4]


def recommend(
    today: Optional[date] = None,
    available_tags: Optional[Iterable[str]] = None,
    max_cards: int = 6,
) -> dict:
    """Return the recommended topic ordering for today.

    `available_tags`, if given, filters out tags that the current intents DB
    doesn't actually serve — so we never recommend a card that would 404.
    """
    today = today or date.today()
    season = _season_for(today)

    tags = list(season.tags)
    if available_tags is not None:
        avail = set(available_tags)
        tags = [t for t in tags if t in avail]

    # Always pad with a stable fallback so the homepage doesn't go empty when
    # the season list and the intents DB drift apart.
    fallback = [
        "admissions_requirements",
        "tuition_fees",
        "courses_offered",
        "scholarship",
        "campus_facilities",
        "contact_info",
    ]
    for t in fallback:
        if t in tags:
            continue
        if available_tags is not None and t not in set(available_tags):
            continue
        tags.append(t)

    return {
        "today": today.isoformat(),
        "season": season.key,
        "label": season.label,
        "reason": season.reason,
        "tags": tags[:max_cards],
    }
