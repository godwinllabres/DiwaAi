"""Front-door safety screen for /chat (see docs/moderation_plan.md).

Runs BEFORE the MCP bridges and the NB/NN intent tiers — the existing
Nonsense/Scope gates only guard the LLM tier, which is how "thank you
tangina mo" earned a cheerful "You're welcome!" from the thanks intent.

Graded categories, priority order:
  self_harm  → supportive referral (never a scold)
  threat     → firm boundary, always flagged
  abuse      → profanity/insult DIRECTED at a person or the bot → boundary
  intensifier→ profanity as seasoning around a real ask → sanitize, continue

False-positive guards (the other direction matters just as much):
word-boundary regexes on a leetspeak-normalized copy, no bare high-risk
substrings ("ass", "hayop", "leche" alone are legitimate words/phrases),
explicit lookaheads (puta≠putahe, leche≠leche flan). test_safety_gate.py
carries a benign trap corpus that must never trip.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Normalization — collapse the usual masking so t@ngina / p0ta / f*ck match.
# ─────────────────────────────────────────────────────────────────────────────

_LEET = str.maketrans({"@": "a", "0": "o", "1": "i", "3": "e", "5": "s", "$": "s", "!": "i"})


def _normalize(text: str) -> str:
    t = text.lower().translate(_LEET)
    t = t.replace("*", "")          # f*ck -> fck (patterns cover vowel-dropped forms)
    t = re.sub(r"[^\w\sñ]", " ", t)  # punctuation to spaces, keep ñ
    return re.sub(r"\s+", " ", t).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Lexicons (EN + Filipino/Taglish). Word-boundary anchored; keep entries
# specific — when a bare word is also a legitimate word, require the phrase.
# ─────────────────────────────────────────────────────────────────────────────

_SELF_HARM_RE = re.compile(
    r"\b(kill(?:ing)? myself|end(?:ing)? my life|take my own life|suicide|suicidal"
    r"|hurt(?:ing)? myself|cut(?:ting)? myself|self ?harm"
    r"|magpapakamatay|magpakamatay|nagpapakamatay"
    r"|gusto ko na?ng? mamatay|ayoko na?ng? mabuhay|wala na?ng? kwenta ang buhay ko)\b",
    re.IGNORECASE,
)

# NOTE: patterns run on _normalize()d text — apostrophes are already spaces,
# so "i'm"/"i'll" arrive as "i m"/"i ll".
_THREAT_RE = re.compile(
    r"\b(i ?(?:will|ll|m going to|am going to|wanna|want to|gonna)\s+(?:hurt|kill|stab|shoot|attack|beat up)\s+"
    r"(?:you|him|her|them|someone|somebody|my|the|that)"
    r"|papatayin (?:kita|ka|ko si|ko ang|namin)|sasaktan (?:kita|ka|ko si|ko ang)"
    r"|babarilin (?:kita|ka|ko si)|sasabugan|pasabugin"
    r"|bomb (?:the|this|that|a) \w+|magdadala ako ng (?:baril|kutsilyo|bomba)"
    r"|bring(?:ing)? a (?:gun|knife|bomb)|school shooting)\b",
    re.IGNORECASE,
)

# Profanity/insults. NOTE the guards: puta(?!he) spares "putahe" (a dish),
# leche(?! ?flan) spares the dessert, tanga(?:ng)?\b avoids "tangan",
# "hayop"/"ass" appear only inside directed phrases, never bare.
_PROFANITY_RE = re.compile(
    r"\b(puta(?!he)\w*|putang ?ina\w*|(?:t|k)ang ?ina\w*|kinang ?ina\w*|king ?ina\w*"
    r"|tarantado\w*|gago\w*|gaga\b|tanga(?:ng)?\b|bobo(?:ng)?\b|inutil|ulol|ungas"
    r"|hinayupak|hayop ka\w*|buwisit\w*|bwisit\w*|leche(?! ?flan)\w*|lintik\w*"
    r"|pakyu\w*|pak ?shet|pakshet|punyeta\w*"
    r"|f+u+c*k+\w*|fck\w*|fuk(?:ing|er)?\b|sh[i1]t\w*|bullshit|asshole\w*|bitch\w*"
    r"|motherf\w*|dumbass|jackass|stupid|idiot\w*|moron\w*)\b",
    re.IGNORECASE,
)

# Second-person markers that turn profanity into directed abuse when they sit
# within a few characters of the match ("tanga KA", "tangina MO", "stupid BOT").
_DIRECTED_RE = re.compile(r"\b(ka|kayo|kita|mo|niyo|nyo|you|u|ur|your|bot|diwa)\b", re.IGNORECASE)
_DIRECTED_WINDOW = 12  # chars around the profanity match to scan for a marker


@dataclass
class SafetyResult:
    category: Optional[str]        # self_harm | threat | abuse | intensifier | None
    matches: list = field(default_factory=list)
    sanitized: str = ""            # message with profanity removed (intensifier path)


def classify(text: str) -> SafetyResult:
    if not text or not text.strip():
        return SafetyResult(None)
    norm = _normalize(text)

    if m := _SELF_HARM_RE.search(norm):
        return SafetyResult("self_harm", [m.group(0)])
    if m := _THREAT_RE.search(norm):
        return SafetyResult("threat", [m.group(0)])

    hits = list(_PROFANITY_RE.finditer(norm))
    if not hits:
        return SafetyResult(None)

    for m in hits:
        lo = max(0, m.start() - _DIRECTED_WINDOW)
        hi = min(len(norm), m.end() + _DIRECTED_WINDOW)
        if _DIRECTED_RE.search(norm[lo:hi]) or m.group(0).lower().endswith(("mo", "ka", "kita", "you")):
            return SafetyResult("abuse", [m.group(0) for m in hits])

    # Intensifier: profanity but not directed. Sanitize and, if a real ask
    # remains, let the cascade answer it; otherwise treat as abuse-lite.
    sanitized = _PROFANITY_RE.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" ,.!?")
    if len(re.findall(r"[A-Za-zñÑ]{3,}", sanitized)) >= 2:
        return SafetyResult("intensifier", [m.group(0) for m in hits], sanitized)
    return SafetyResult("abuse", [m.group(0) for m in hits])


# ─────────────────────────────────────────────────────────────────────────────
# Responses. S1 copy is a PLACEHOLDER pending Guidance-office sign-off
# (docs/moderation_plan.md §5.1) — factual contacts, supportive tone.
# ─────────────────────────────────────────────────────────────────────────────

RESPONSES = {
    "self_harm": (
        "I'm really sorry you're going through this — you matter, and you don't "
        "have to face it alone. Please reach out to the **CvSU Guidance and "
        "Counseling Services** on your campus, or call the **NCMH Crisis Hotline "
        "at 1553** (toll-free, 24/7). If you're in immediate danger, contact "
        "campus security or 911. Kung gusto mo, nandito lang ako para sa "
        "impormasyon tungkol sa mga student support services ng CvSU."
    ),
    "threat": (
        "I can't help with anything that could harm someone. If something "
        "serious is going on, please talk to the CvSU Guidance and Counseling "
        "Services or campus security right away. If you meant something else, "
        "rephrase it and I'll gladly help with your CvSU questions."
    ),
    "abuse": (
        "I'm here to help, but let's keep it respectful po. Kung may tanong ka "
        "tungkol sa CvSU — admissions, enrollment, courses, campus services — "
        "sagutin kita agad."
    ),
}

SUGGESTIONS = {
    "self_harm": ["Guidance and Counseling Services", "Student support services"],
    "threat": [],
    "abuse": ["Admission requirements", "Courses offered", "Campus locations"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Observability — counters + a small ring buffer for /admin/moderation.
# ─────────────────────────────────────────────────────────────────────────────

_stats: dict = {"self_harm": 0, "threat": 0, "abuse": 0, "intensifier": 0}
_recent: deque = deque(maxlen=20)


def record(category: str, message: str, session_id: Optional[str]) -> None:
    _stats[category] = _stats.get(category, 0) + 1
    _recent.append(
        {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "message": message[:120],
            "session_id": session_id,
        }
    )


def snapshot() -> dict:
    return {"counts": dict(_stats), "recent": list(_recent)}
