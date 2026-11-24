"""Per-session campus context — disambiguation + follow-up grounding.

"Where is the campus located?" is ambiguous: CvSU has a main campus (Indang)
and ten satellites. Instead of silently assuming Indang, this module:

  1. remembers which campus a session is talking about (any mention sticks,
     with a TTL, except an objection like "why only trece?") —
     "I'm from CvSU Imus" → campus=Imus;
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

# Retrieval scope keys — the same keys data/charter_structure.json's
# "campuses" ranges use. "all" (university-wide) and "toc" are not campuses.
CAMPUS_KEYS: dict[str, str] = {
    "Indang (Main Campus)": "main",
    "Bacoor City Campus": "bacoor",
    "Carmona Campus": "carmona",
    "Cavite City Campus": "cavite_city",
    "CvSU-CCAT Campus (Rosario)": "ccat",
    "General Trias City Campus": "general_trias",
    "Imus City Campus": "imus",
    "Maragondon Campus": "maragondon",
    "Naic Campus": "naic",
    "Silang Campus": "silang",
    "Tanza Campus": "tanza",
    "Trece Martires City Campus": "trece",
}
# Dasmariñas City Learning Center has its own charter section but is
# deliberately NOT in CAMPUSES (no clarify chip, no campus_directory entry):
# this alias only unlocks its chunks when a question names it.
_DASMA_RE = re.compile(r"\bdasma(?:ri[nñ]as)?\b", re.IGNORECASE)

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


# News/recognition asks that mention "where" incidentally ("Where does CvSU
# rank in WURI?", "When and where was the conference held?") are NOT campus-
# location asks — without this guard they hijack the clarify flow away from
# the events/rankings/officials intents.
_NEWS_TOPIC_RE = re.compile(
    r"\b(rank(?:s|ed|ing|ings)?|wuri|qs star(?:s)?|award(?:s|ed)?|recognition|"
    r"conference|forum|symposium|ceremony|celebration|anniversary|held|"
    r"officials?|topnotcher(?:s)?|licensure|board exam|when and where)\b",
    re.IGNORECASE,
)


def is_campus_location_question(text: str) -> bool:
    """A location/address ask about a campus ITSELF ("where is CvSU Imus"),
    not about a place inside one ("where is the CvSU library" → map intent)
    or a news/recognition ask that merely contains a where-word.
    Shared with the campus-directory gate in api/app.py."""
    if _SPECIFIC_PLACE_RE.search(text) or _NEWS_TOPIC_RE.search(text):
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
    # "why only trece?" objects to a campus; it must not become the session's
    # campus (that re-admitted the Trece chunk for the next 30 minutes).
    if mentioned and not is_contrastive_campus_mention(text):
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


# "why only trece?" / "bakit trece lang?" complains that a campus was singled
# out; it does not ask about that campus, so it must not scope retrieval to it.
_EXCLUSIVE_COMPLAINT_RE = re.compile(
    r"^\W*(why|bakit)\b.*\b(only|just|lang)\b", re.IGNORECASE
)


def is_exclusive_complaint(text: str) -> bool:
    return bool(_EXCLUSIVE_COMPLAINT_RE.search(text or ""))


_ANY_ALIAS = "|".join(re.escape(a) for aliases in CAMPUSES.values() for a in aliases)
# "not trece", "hindi sa Imus", "other than the main campus".
_NEGATED_CAMPUS_RE = re.compile(
    r"\b(?:not|hindi|instead of|other than|maliban sa)\s+(?:sa\s+|the\s+)?(?:" + _ANY_ALIAS + r")\b",
    re.IGNORECASE,
)


# Words a "why only <campus>?" complaint is made of. Anything else ("why does
# only Imus offer psychology") is a real question about that campus.
_COMPLAINT_WORDS = frozenset("""
why bakit only just lang is it that this the a an in at sa for from campus campuses
city cvsu ba po naman you your did does do was were answer answers said say mention
mentioned show showed shown give gave list listed about there ang yung mo nyo ninyo
""".split())


def is_contrastive_campus_mention(text: str) -> bool:
    """A short message that names a campus only to object to it ("why only
    trece?", "bakit trece lang?", "not Imus"), not to ask about it."""
    if not text or len(re.findall(r"[\w'’]+", text)) > 8:
        return False
    if _NEGATED_CAMPUS_RE.search(text):
        return True
    if not is_exclusive_complaint(text):
        return False
    rest = re.findall(r"[\w'’]+", strip_campus_mentions(text).lower())
    return all(w in _COMPLAINT_WORDS for w in rest)


def strip_campus_mentions(text: str) -> str:
    """The message with every campus alias removed, whitespace collapsed."""
    for _, pattern in _ALIAS_RES:
        text = pattern.sub(" ", text)
    return " ".join(text.split())


# Tokens of campus names. A query bigram made only of these ("trece campus",
# "martires city") says where, not what, so it must not pass the verbatim
# tier's phrase-match gate on its own.
_CAMPUS_TOKENS = frozenset(
    w for aliases in CAMPUSES.values() for a in aliases for w in a.split()
) | {"campus", "campuses", "city", "cvsu", "dasma", "dasmarinas", "dasmariñas"}


def is_campus_term(term: str) -> bool:
    """True when every word of an analyzer term is part of a campus name."""
    words = term.lower().split()
    return bool(words) and all(w in _CAMPUS_TOKENS for w in words)


def campus_key(canonical: Optional[str]) -> Optional[str]:
    """Scope key for a canonical CAMPUSES name (None passes through)."""
    return CAMPUS_KEYS.get(canonical) if canonical else None


def query_campus_key(text: str) -> Optional[str]:
    """Scope key of the campus a message names, if any."""
    if not text or is_contrastive_campus_mention(text):
        return None
    key = campus_key(extract_campus(text))
    if key is None and _DASMA_RE.search(text):
        key = "dasmarinas"
    return key


def retrieval_scope(text: str, session_campus: Optional[str] = None) -> frozenset:
    """Chunk campus tags a question may be grounded on.

    A satellite named by the message (or, failing that, the session's campus)
    → that campus + university-wide chunks. Otherwise main campus +
    university-wide: a generic question must not be answered from a
    satellite's procedure. "toc" (the charter's list of services) is never in
    scope — it names services without describing them.
    """
    key = query_campus_key(text) or campus_key(session_campus)
    if key and key != "main":
        return frozenset({key, "all"})
    return frozenset({"main", "all"})


# Site-corpus tagging aliases: CAMPUSES' plus the "CvSU Main" spelling news
# titles use. Built per key so a doc naming two campuses is detectable.
_DOC_ALIASES: dict[str, list[str]] = {}
for _canonical, _aliases in CAMPUSES.items():
    _DOC_ALIASES.setdefault(CAMPUS_KEYS[_canonical], []).extend(_aliases)
_DOC_ALIASES["main"] = _DOC_ALIASES["main"] + ["cvsu main", "main campuses"]
_DOC_ALIASES["dasmarinas"] = ["dasma", "dasmarinas", "dasmariñas"]
_DOC_ALIAS_RES = {
    key: re.compile(r"\b(" + "|".join(re.escape(a) for a in aliases) + r")\b", re.IGNORECASE)
    for key, aliases in _DOC_ALIASES.items()
}
_ALIAS_ALT = "|".join(re.escape(a) for aliases in _DOC_ALIASES.values() for a in aliases)
# "CvSU Silang posts ...", "CvSU-Imus ..." — the alias directly after CvSU.
# A bare "^CvSU " prefix over-tags ("CvSU turns over ... LGU Maragondon").
_CVSU_PREFIX_RE = re.compile(r"^\s*cvsu\s*[-–]?\s*(main|" + _ALIAS_ALT + r")\b", re.IGNORECASE)
# 2018 program pages titled with just the town ("Silang", "Trece Martires City").
_TOWN_ONLY_RE = re.compile(r"^\s*(" + _ALIAS_ALT + r")(?:\s+city)?\s*$", re.IGNORECASE)
_CAMPUS_WORD_DOC_RE = re.compile(r"\b(campus(?:es)?|ccat)\b", re.IGNORECASE)


def doc_campus_key(title: str, url: str) -> str:
    """Campus tag for an official-site document, from its title and slug.

    The corpus rewrites every URL to cvsu.edu.ph, so the campus can only be
    read off the title/slug. A satellite tag needs exactly one campus named
    AND evidence the doc is about that campus (a "campus" word, the alias
    right after "CvSU", or a town-only title); news that merely mentions a
    town, and posts listing several campuses, stay "all".
    """
    title = (title or "").replace("–", " ").replace("-", " ")
    slug = (url or "").rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    text = f"{title} {slug}"
    hits = {key for key, rx in _DOC_ALIAS_RES.items() if rx.search(text)}
    if len(hits) != 1:
        return "all"
    key = next(iter(hits))
    # Main needs the same evidence: "Indang LGU" / "Don Severino" history
    # news is university-wide and must stay visible to satellite sessions.
    if _CAMPUS_WORD_DOC_RE.search(text) or _CVSU_PREFIX_RE.search(title) or _TOWN_ONLY_RE.search(title):
        return key
    return "all"


def snapshot() -> dict:
    """For admin/debug: active sessions with a campus or pending question."""
    now = time.monotonic()
    return {
        "active_sessions": sum(1 for s in _sessions.values() if now - s["at"] <= _TTL_SECONDS),
        "with_campus": sum(
            1 for s in _sessions.values() if s["campus"] and now - s["at"] <= _TTL_SECONDS
        ),
    }
