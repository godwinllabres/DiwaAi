"""Front-door safety screen for /chat (see docs/moderation_plan.md).

Runs BEFORE the MCP bridges and the NB/NN intent tiers — the existing
Nonsense/Scope gates only guard the LLM tier, which is how "thank you
tangina mo" earned a cheerful "You're welcome!" from the thanks intent.

Graded categories, priority order:
  self_harm  → supportive referral (never a scold)
  threat     → firm boundary, always flagged
  abuse      → profanity/insult DIRECTED at a person or the bot → boundary
  intensifier→ profanity as seasoning around a real ask → sanitize, continue

Profanity matching is powered by the Philippine Profanity Lexicon at
data/profanities/ph_profanity_lexicon.json (207 entries, 328 variants,
10 PH languages) and follows its own matching_guidance:
  • normalization: lowercase, diacritic fold, leetspeak map, collapse 3+
    repeated letters, separator-squeeze for severe multiword phrases
  • boundaries: word boundaries for short/mild terms; substring only for
    severity>=3 terms longer than 5 chars
  • allowlist masked out BEFORE matching (putahe, puto, reputasyon, …)
  • context_dependent entries (identity terms like bakla/bayot — "do not
    auto-block") only count with hostile framing (directed marker nearby)
If the lexicon file is unreadable the gate falls back to a built-in regex —
the front door never goes unguarded.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger("diwa.safety")

_LEXICON_PATH = os.environ.get(
    "SAFETY_LEXICON_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "profanities", "ph_profanity_lexicon.json",
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Normalization — per the lexicon's matching_guidance.
# ─────────────────────────────────────────────────────────────────────────────

# Leet map per the lexicon's matching_guidance. NOTE: '!' is deliberately NOT
# mapped to 'i' — it acts as sentence punctuation far more often than as a
# letter, and mapping it glues onto the next token ("gago ka!" -> "gago kai"),
# defeating the \bka\b directed-marker check.
_LEET = str.maketrans(
    {"@": "a", "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s"}
)


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text).lower().translate(_LEET)
    t = "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    )  # fold diacritics (ñ -> n)
    t = t.replace("*", "")
    t = re.sub(r"(.)\1{2,}", r"\1", t)      # gagooo -> gago
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _squeeze(text: str) -> str:
    """Separator-stripped copy for severe multiword phrases (t a n g i n a)."""
    return re.sub(r"[\s._-]+", "", text)


# ─────────────────────────────────────────────────────────────────────────────
# Self-harm and threats — outside the lexicon's scope, kept as curated rules.
# NOTE: patterns run on _normalize()d text — apostrophes are already spaces.
# ─────────────────────────────────────────────────────────────────────────────

_SELF_HARM_RE = re.compile(
    r"\b(kill(?:ing)? myself|end(?:ing)? my life|take my own life|suicide|suicidal"
    r"|hurt(?:ing)? myself|cut(?:ting)? myself|self ?harm"
    r"|magpapakamatay|magpakamatay|nagpapakamatay"
    r"|gusto ko na?ng? mamatay|ayoko na?ng? mabuhay|wala na?ng? kwenta ang buhay ko)\b",
    re.IGNORECASE,
)

_THREAT_RE = re.compile(
    r"\b(i ?(?:will|ll|m going to|am going to|wanna|want to|gonna)\s+(?:hurt|kill|stab|shoot|attack|beat up)\s+"
    r"(?:you|him|her|them|someone|somebody|my|the|that)"
    r"|papatayin (?:kita|ka|ko si|ko ang|namin)|sasaktan (?:kita|ka|ko si|ko ang)"
    r"|babarilin (?:kita|ka|ko si)|sasabugan|pasabugin"
    r"|bomb (?:the|this|that|a) \w+|magdadala ako ng (?:baril|kutsilyo|bomba)"
    r"|bring(?:ing)? a (?:gun|knife|bomb)|school shooting)\b",
    re.IGNORECASE,
)

# Fallback profanity matcher — used only when the lexicon can't be loaded.
_FALLBACK_PROFANITY_RE = re.compile(
    r"\b(puta(?!he)\w*|putang ?ina\w*|(?:t|k)ang ?ina\w*|kinang ?ina\w*|king ?ina\w*"
    r"|tarantado\w*|gago\w*|gaga\b|tanga(?:ng)?\b|bobo(?:ng)?\b|inutil|ulol|ungas"
    r"|hinayupak|hayop ka\w*|buwisit\w*|bwisit\w*|leche(?! ?flan)\w*|lintik\w*"
    r"|pakyu\w*|pak ?shet|pakshet|punyeta\w*"
    r"|f+u+c*k+\w*|fck\w*|fuk(?:ing|er)?\b|sh[i1]t\w*|bullshit|asshole\w*|bitch\w*"
    r"|motherf\w*|dumbass|jackass|stupid|idiot\w*|moron\w*)\b",
    re.IGNORECASE,
)

_DIRECTED_RE = re.compile(r"\b(ka|kayo|kita|mo|niyo|nyo|you|u|ur|your|bot|diwa)\b", re.IGNORECASE)
_DIRECTED_WINDOW = 12
_DIRECTED_SUFFIXES = ("mo", "ka", "kita", "you", "kayo", "nyo")


# ─────────────────────────────────────────────────────────────────────────────
# Lexicon loading — compiled once, lazily.
# ─────────────────────────────────────────────────────────────────────────────


class _Lexicon:
    def __init__(self, path: str):
        self.loaded = False
        self.version: Optional[str] = None
        self.entry_count = 0
        self.form_count = 0
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001 — fail soft, fallback regex guards
            _logger.warning("profanity lexicon not loaded (%s) — using built-in fallback", exc)
            return

        # (normalized form, severity, is_slur, context_dependent)
        forms: list[tuple[str, int, bool, bool]] = []
        for entry in data.get("entries", []):
            severity = int(entry.get("severity", 2))
            slur = bool(entry.get("is_slur"))
            # False friends ("boto" = vote, "atay" = liver) are innocent
            # without hostile framing — same directed-only treatment as
            # context-dependent identity terms (matching_guidance).
            ctx = bool(entry.get("context_dependent")) or bool(entry.get("false_friends"))
            for raw in [entry.get("term", "")] + list(entry.get("variants") or []):
                form = _normalize(raw)
                if form:
                    forms.append((form, severity, slur, ctx))

        self._meta = {f: (sev, slur, ctx) for f, sev, slur, ctx in forms}

        def _alt(items: list[str]) -> Optional[re.Pattern]:
            if not items:
                return None
            items = sorted(set(items), key=len, reverse=True)
            return re.compile(
                r"\b(" + "|".join(re.escape(i) for i in items) + r")\b", re.IGNORECASE
            )

        # Pools per matching_guidance.boundaries. Context-dependent forms sit
        # in their own pool: they only count with a directed marker nearby.
        # Substring-eligible = severity>=3 AND longer than 5 chars; EVERYTHING
        # else takes word boundaries (the two rules must partition the space —
        # a severity-3, exactly-5-char term like "bilat" belongs in the
        # boundary pool, not a gap between them).
        self._forms = {f for f, _, _, _ in forms}
        ctx_forms = [f for f, _, _, c in forms if c]
        plain = [(f, s) for f, s, _, c in forms if not c]
        self._ctx_re = _alt(ctx_forms)

        def _substring_eligible(form: str, sev: int) -> bool:
            return sev >= 3 and len(form) > 5

        self._bound_re = _alt([f for f, s in plain if not _substring_eligible(f, s)])
        sub_forms = sorted(
            {f for f, s in plain if _substring_eligible(f, s)}, key=len, reverse=True
        )
        self._sub_re = (
            re.compile("(" + "|".join(re.escape(f) for f in sub_forms) + ")", re.IGNORECASE)
            if sub_forms
            else None
        )
        # Severe multiword phrases, matched against separator-squeezed text.
        # The squeezed form is matched WITHOUT boundaries, so it must clear the
        # same >5-char substring floor — otherwise a 2-char variant like "p i"
        # squeezes to "pi" and matches inside "copies"/"capital".
        squeezed = sorted(
            {_squeeze(f) for f, s in plain if s >= 3 and " " in f and len(_squeeze(f)) > 5},
            key=len, reverse=True,
        )
        self._squeezed_re = (
            re.compile("(" + "|".join(re.escape(f) for f in squeezed) + ")", re.IGNORECASE)
            if squeezed
            else None
        )
        # Allowlist phrases are masked out of the text before any matching.
        allow = [_normalize(a.get("phrase", "")) for a in data.get("allowlist", [])]
        self._allow_re = (
            re.compile(
                r"\b(" + "|".join(re.escape(a) for a in sorted(set(allow), key=len, reverse=True) if a) + r")\w*",
                re.IGNORECASE,
            )
            if allow
            else None
        )

        self.version = data.get("version")
        self.entry_count = int(data.get("entry_count") or len(data.get("entries", [])))
        self.form_count = len(forms)
        self.loaded = True
        _logger.info(
            "profanity lexicon v%s loaded: %d entries, %d forms",
            self.version, self.entry_count, self.form_count,
        )

    def mask_allowlist(self, norm: str) -> str:
        return self._allow_re.sub(" ", norm) if self._allow_re else norm

    def find(self, masked: str) -> tuple[list[str], list[str]]:
        """Return (counted_hits, directed_only_hits_pending_evidence)."""
        hits: list[str] = []
        pending: list[str] = []
        for pattern in (self._bound_re, self._sub_re):
            if pattern:
                hits.extend(m.group(0) for m in pattern.finditer(masked))
        if self._squeezed_re:
            hits.extend(m.group(0) for m in self._squeezed_re.finditer(_squeeze(masked)))
        if self._ctx_re:
            pending.extend(m.group(0) for m in self._ctx_re.finditer(masked))
        return hits, pending

    def severity(self, form: str) -> int:
        return self._meta.get(_normalize(form), (2, False, False))[0]

    def is_slur(self, form: str) -> bool:
        return self._meta.get(_normalize(form), (2, False, False))[1]

    def is_form(self, s: str) -> bool:
        """Is the normalized string a known profane form? (glued-suffix check)"""
        return _normalize(s) in self._forms


_lexicon: Optional[_Lexicon] = None


def _get_lexicon() -> _Lexicon:
    global _lexicon
    if _lexicon is None:
        _lexicon = _Lexicon(_LEXICON_PATH)
    return _lexicon


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SafetyResult:
    category: Optional[str]        # self_harm | threat | abuse | intensifier | None
    matches: list = field(default_factory=list)
    sanitized: str = ""            # message with profanity removed (intensifier path)
    max_severity: int = 0


def _directed_near(norm: str, token: str, known_form=None) -> bool:
    """Is this profanity hit aimed at a person/the bot (vs. a stray intensifier)?"""
    token_l = token.lower()
    squeezed_token = token_l.replace(" ", "")
    # Glued form: a profanity STEM with a pronoun suffix ("tanginaMO"). Only
    # counts when the stem (token minus suffix) is itself a known profane form,
    # so benign words that merely end in a pronoun — "damo" (Hiligaynon),
    # "demonyo", "chaka" — do not false-fire as directed abuse.
    for suf in _DIRECTED_SUFFIXES:
        if squeezed_token.endswith(suf) and len(squeezed_token) > len(suf):
            stem = squeezed_token[: -len(suf)]
            if known_form is not None and known_form(stem):
                return True
    # A standalone 2nd-person marker within the window of an occurrence.
    located = False
    for m in re.finditer(re.escape(token_l), norm):
        located = True
        lo = max(0, m.start() - _DIRECTED_WINDOW)
        hi = min(len(norm), m.end() + _DIRECTED_WINDOW)
        if _DIRECTED_RE.search(norm[lo:hi]):
            return True
    # Separator-obfuscated profanity ("t a n g i n a"): the token isn't
    # locatable in the spaced text, so a precise window is impossible. If the
    # squeezed text contains it and any marker appears in the message, treat as
    # directed — the obfuscation itself signals intent.
    if not located and squeezed_token in _squeeze(norm) and _DIRECTED_RE.search(norm):
        return True
    return False


def _sanitize(original: str, tokens: list[str]) -> str:
    out = original
    for token in sorted(set(tokens), key=len, reverse=True):
        # Separator- AND elongation-tolerant: each char may repeat (fuuuck) and
        # be split by a separator (t a n g-i n a), so both forms are removed.
        pattern = r"[\W_]*".join(re.escape(c) + "+" for c in _squeeze(token))
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" ,.!?")


def classify(text: str) -> SafetyResult:
    if not text or not text.strip():
        return SafetyResult(None)
    lex = _get_lexicon()
    norm = _normalize(text)
    masked = lex.mask_allowlist(norm) if lex.loaded else norm

    if m := _SELF_HARM_RE.search(masked):
        return SafetyResult("self_harm", [m.group(0)])
    if m := _THREAT_RE.search(masked):
        return SafetyResult("threat", [m.group(0)])

    known_form = lex.is_form if lex.loaded else None

    # The lexicon is PH-focused; its guidance says to run English lists
    # alongside. The built-in regex stays in the union so generic English
    # insults (stupid bot, asshole) keep tripping.
    hits = [m.group(0) for m in _FALLBACK_PROFANITY_RE.finditer(masked)]
    hard_slur = False
    if lex.loaded:
        lex_hits, pending = lex.find(masked)
        # Context-dependent / false-friend terms count only with hostile framing.
        lex_hits += [p for p in pending if _directed_near(masked, p, known_form)]
        seen = {h.lower() for h in hits}
        hits += [h for h in lex_hits if h.lower() not in seen]
        # Non-context-dependent slurs are never "seasoning".
        hard_slur = any(lex.is_slur(h) for h in lex_hits)
    if not hits:
        return SafetyResult(None)

    # Severity gates the outcome: mild expletives (severity 1 — "damn",
    # "lintik") are not moderation-worthy on their own. Keep them only when
    # directed (an insult), otherwise let the message answer normally. Fallback
    # regex hits (English) aren't in the lexicon → default severity 2, so they
    # keep tripping.
    def _sev(h: str) -> int:
        return lex.severity(h) if lex.loaded else 3

    directed = any(_directed_near(masked, h, known_form) for h in hits)
    gated = [h for h in hits if _sev(h) >= 2 or (directed and _sev(h) >= 1)]
    if not gated:
        return SafetyResult(None)
    hits = gated
    max_sev = max(_sev(h) for h in hits)

    if directed or hard_slur:
        return SafetyResult("abuse", hits, max_severity=max_sev)

    sanitized = _sanitize(text, hits)
    if len(re.findall(r"[A-Za-zñÑ]{3,}", sanitized)) >= 2:
        return SafetyResult("intensifier", hits, sanitized, max_severity=max_sev)
    return SafetyResult("abuse", hits, max_severity=max_sev)


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


def record(category: str, message: str, session_id: Optional[str], max_severity: int = 0) -> None:
    _stats[category] = _stats.get(category, 0) + 1
    _recent.append(
        {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "severity": max_severity or None,
            "message": message[:120],
            "session_id": session_id,
        }
    )


def snapshot() -> dict:
    lex = _get_lexicon()
    return {
        "counts": dict(_stats),
        "recent": list(_recent),
        "lexicon": {
            "loaded": lex.loaded,
            "version": lex.version,
            "entries": lex.entry_count,
            "forms": lex.form_count,
        },
    }
