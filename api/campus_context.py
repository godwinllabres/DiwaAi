"""Per-session campus context — disambiguation + follow-up grounding.

"Where is the campus located?" is ambiguous: CvSU has a main campus (Indang)
and ten satellites. Instead of silently assuming Indang, this module:

  1. remembers which campus a session is talking about (any mention sticks,
     with a TTL) — "I'm from CvSU Imus" → campus=Imus;
  2. when a campus-dependent question arrives WITHOUT a campus and the
     session has one → rewrites the message to carry it ("Where is the
     campus located? (Imus Campus)") so the intent tiers, the charter RAG
     retrieval, and the LLM all receive the campus token — the charter's
     per-campus sections then rank correctly;
  3. when there's no campus at all → asks which one, using the envelope's
     `suggestions` as clickable campus chips, and parks the question so the
     user's one-word answer ("Bacoor") resumes it automatically.

State is in-memory per session_id (same lifetime model as the AIS bridge's
pronoun context).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

_TTL_SECONDS = 1800.0  # 30 min — same conversation, not a profile

# Canonical campus names → alias patterns (word-boundary, lowercase).
CAMPUSES: dict[str, list[str]] = {
    "Indang (Main Campus)": ["indang", "main campus", "don severino"],
    "Bacoor City Campus": ["bacoor"],
    "Carmona Campus": ["carmona"],
    "Cavite City Campus": ["cavite city"],
    "CvSU-CCAT Campus (Rosario)": ["ccat", "rosario"],
    "General Trias City Campus": ["general trias", "gen trias", "gentri"],
    "Imus City Campus": ["imus"],
    "Naic Campus": ["naic"],
    "Silang Campus": ["silang"],
    "Tanza Campus": ["tanza"],
    "Trece Martires City Campus": ["trece martires", "trece"],
}

_ALIAS_RES = [
    (canonical, re.compile(r"\b(" + "|".join(re.escape(a) for a in aliases) + r")\b", re.IGNORECASE))
    for canonical, aliases in CAMPUSES.items()
]

# Campus-dependent question: a location/where ask about "the campus/school/
# CvSU" in general. Specific-place asks ("where is the library") stay with
# the map intents.
_LOCATION_Q_RE = re.compile(
    r"\b(where|saan|nasaan|asaan|located?|location|address|papunta|pupunta|how (?:do i|to) get)\b",
    re.IGNORECASE,
)
_CAMPUS_WORD_RE = re.compile(r"\b(campus(?:es)?|university|unibersidad|school|cvsu)\b", re.IGNORECASE)

CLARIFY_TEXT = (
    "CvSU has a main campus in Indang and ten satellite campuses po — "
    "which campus are you asking about?"
)
CLARIFY_SUGGESTIONS = list(CAMPUSES.keys())


@dataclass
class CampusRouting:
    action: str                    # "none" | "clarify" | "augment" | "answer_pending"
    campus: Optional[str] = None
    message: Optional[str] = None  # rewritten message for augment/answer_pending


_sessions: dict[str, dict] = {}


def _session(key: str) -> dict:
    now = time.monotonic()
    state = _sessions.get(key)
    if state is None or now - state["at"] > _TTL_SECONDS:
        state = {"campus": None, "pending": None, "at": now}
        _sessions[key] = state
    state["at"] = now
    if len(_sessions) > 5000:  # bound memory on long-running processes
        stale = [k for k, s in _sessions.items() if now - s["at"] > _TTL_SECONDS]
        for k in stale:
            _sessions.pop(k, None)
    return state


def extract_campus(text: str) -> Optional[str]:
    for canonical, pattern in _ALIAS_RES:
        if pattern.search(text):
            return canonical
    return None


def _is_ambiguous_campus_question(text: str) -> bool:
    return bool(_LOCATION_Q_RE.search(text) and _CAMPUS_WORD_RE.search(text))


def resolve(session_key: Optional[str], text: str) -> CampusRouting:
    """Route one message through the campus-context state machine."""
    state = _session(session_key or "anon")
    mentioned = extract_campus(text)
    if mentioned:
        state["campus"] = mentioned

    # A parked question + a message that is (mostly) just a campus name:
    # the user answered the clarification — resume the original question.
    if state["pending"] and mentioned and len(text.split()) <= 4:
        question = state["pending"]
        state["pending"] = None
        return CampusRouting(
            "answer_pending", mentioned, f"{question} ({mentioned})"
        )

    if _is_ambiguous_campus_question(text) and not mentioned:
        if state["campus"]:
            return CampusRouting(
                "augment", state["campus"], f"{text} ({state['campus']})"
            )
        state["pending"] = text
        return CampusRouting("clarify")

    return CampusRouting("none", state["campus"])


def snapshot() -> dict:
    """For admin/debug: active sessions with a campus or pending question."""
    now = time.monotonic()
    return {
        "active_sessions": sum(1 for s in _sessions.values() if now - s["at"] <= _TTL_SECONDS),
        "with_campus": sum(
            1 for s in _sessions.values() if s["campus"] and now - s["at"] <= _TTL_SECONDS
        ),
    }
