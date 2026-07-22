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
    "Maragondon Campus": ["maragondon"],
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
# A specific place inside a campus — these stay with the map intents, even when
# the sentence also says "cvsu"/"campus" ("where is the CvSU library").
_SPECIFIC_PLACE_RE = re.compile(
    r"\b(library|aklatan|office|opisina|building|gusali|hall|gym|gymnasium|oval|"
    r"canteen|cafeteria|clinic|registrar|cashier|dorm|dormitory|laboratory|lab|"
    r"room|gate|parking|chapel|auditorium|covered court|grandstand|department|"
    r"college|comfort room|cr|store|coop)\b",
    re.IGNORECASE,
)

CLARIFY_TEXT = (
    "CvSU has a main campus in Indang and satellite campuses across Cavite "
    "po — which campus are you asking about?"
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


def is_campus_location_question(text: str) -> bool:
    """A location/address ask about a campus ITSELF ("where is CvSU Imus"),
    not about a place inside one ("where is the CvSU library" → map intent).
    Shared with the campus-directory gate in api/app.py."""
    if _SPECIFIC_PLACE_RE.search(text):
        return False
    return bool(_LOCATION_Q_RE.search(text) and _CAMPUS_WORD_RE.search(text))


def _is_ambiguous_campus_question(text: str) -> bool:
    return is_campus_location_question(text)


# Filler tokens that don't count as "a real question" when deciding whether a
# short reply is just the campus answer.
_FILLER = {
    "campus", "kampus", "po", "yung", "sa", "ang", "the", "please", "is", "in",
    "at", "ba", "kay", "dito", "doon", "ito", "iyan", "na", "ni",
}


def _is_bare_campus_reply(text: str, mentioned: Optional[str]) -> bool:
    """Is this message essentially JUST a campus name (i.e., an answer to the
    clarification), rather than a new question that happens to name a campus?"""
    if not mentioned:
        return False
    stripped = text
    for _, pattern in _ALIAS_RES:
        stripped = pattern.sub(" ", stripped)
    content = [w for w in re.findall(r"[a-zñ]+", stripped.lower()) if w not in _FILLER]
    return len(content) == 0


def resolve(session_key: Optional[str], text: str) -> CampusRouting:
    """Route one message through the campus-context state machine.

    session_key is the caller's session/user id. When it is falsy (a truly
    stateless caller — no session_id AND no user_id), a throwaway state is used
    so anonymous callers never share one global bucket (which would leak one
    visitor's campus/parked question into another's turn).
    """
    state = _session(session_key) if session_key else {"campus": None, "pending": None, "at": 0.0}
    mentioned = extract_campus(text)
    if mentioned:
        state["campus"] = mentioned

    # Pending is single-shot: consume or clear it on the very next turn, so a
    # parked question can never hijack an unrelated short message minutes later.
    pending = state.get("pending")
    state["pending"] = None
    if pending and _is_bare_campus_reply(text, mentioned):
        # The reply is just the campus — resume the parked question.
        return CampusRouting("answer_pending", mentioned, f"{pending} ({mentioned})")

    if _is_ambiguous_campus_question(text) and not mentioned:
        if state["campus"]:
            return CampusRouting(
                "augment", state["campus"], f"{text} ({state['campus']})"
            )
        # Only park a pending question when we can actually resume it (there is
        # a session to key it to). Stateless callers get the clarify prompt but
        # nothing is stored.
        if session_key:
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
