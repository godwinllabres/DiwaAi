"""Sanitation checks for onboarding a new intent.

Adding a new intent is the riskiest change you can make to the chatbot —
overlapping patterns silently cannibalize existing intents, and bad
responses become part of the canon. This module runs every check we'd
otherwise discover the hard way at QA time:

  * tag format: snake_case, alphanumeric, not already in use
  * pattern hygiene: length, encoding, duplicates within and across intents
  * cross-intent collisions: if the current classifier confidently maps a
    new pattern to an existing intent, that's a warning — we may be
    splitting one topic into two by accident
  * response hygiene: length, duplicates, basic profanity
  * encoding: every string must round-trip through UTF-8 cleanly

The output is a structured report. Callers (the admin UI or a CLI) decide
what to do with errors vs warnings. We never apply the intent here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


# Conservative banned-token list. Extend per policy.
_PROFANITY = frozenset({
    "fuck", "shit", "bitch", "asshole", "cunt", "fag", "nigger",
    "putang", "puta", "gago", "tangina", "bobo",
})

_TAG_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

# Tags emitted by CODE tiers, not by the intents store. Creating a DB intent
# under one of these names would teach the classifier to PREDICT a system
# state from user text (e.g. serve a curated reply labelled "llm_unavailable"
# while the real degrade reply is shadowed) — so onboarding refuses them
# outright, and the /admin/intents hard gate does NOT honour ?force=true for
# them. Keep in sync with the intent constants in api/hybrid_chatbot.py and
# the short-circuit sources in api/app.py.
RESERVED_TAGS = frozenset({
    # cascade tier outputs (api/hybrid_chatbot.py)
    "llm_unavailable", "nlu_fallback", "smalltalk", "conversation_recap",
    "find_place", "college_programs",
    # endpoint short-circuit sources (api/app.py)
    "ais_mcp", "connectors_mcp", "hr_mcp",
    "campus_disambiguation", "action_book_advising",
})
RESERVED_PREFIXES = ("safety_",)


def is_reserved_tag(tag: str) -> bool:
    """True when `tag` names a code-owned tier or system state."""
    tag = (tag or "").strip().lower()
    return tag in RESERVED_TAGS or tag.startswith(RESERVED_PREFIXES)

_PATTERN_MIN = 3
_PATTERN_MAX = 200
_RESPONSE_MIN = 10
_RESPONSE_MAX = 2000

_COLLISION_HIGH_CONF = 0.65   # >= this on existing intent => hard collision
_COLLISION_MED_CONF = 0.45    # warn between these two


@dataclass
class SanitizationFinding:
    severity: str       # "error" | "warning" | "info"
    code: str           # machine-readable
    message: str        # human-readable
    detail: Optional[Dict[str, Any]] = None


@dataclass
class SanitizationReport:
    candidate_tag: str
    pattern_count: int
    response_count: int
    findings: List[SanitizationFinding] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_tag": self.candidate_tag,
            "pattern_count": self.pattern_count,
            "response_count": self.response_count,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "findings": [
                {
                    "severity": f.severity,
                    "code": f.code,
                    "message": f.message,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip()).lower()


def _has_profanity(s: str) -> List[str]:
    tokens = re.findall(r"[\w']+", _norm(s))
    return [t for t in tokens if t in _PROFANITY]


def _ascii_round_trip(s: str) -> bool:
    """Every string we accept must survive UTF-8 round-trip without loss."""
    try:
        return s.encode("utf-8").decode("utf-8") == s
    except UnicodeError:
        return False


def sanitize_candidate_intent(
    candidate: Dict[str, Any],
    existing_intents: Iterable[Dict[str, Any]],
    classifier_predict_proba: Optional[callable] = None,
) -> SanitizationReport:
    """Run every safety check against `candidate` and return a report.

    Args:
        candidate: { tag, patterns: [str], responses: [str] }.
        existing_intents: iterable of dicts shaped like the rows from
            intents_db.load_intents — used for collision detection.
        classifier_predict_proba: optional callable that takes a pattern
            string and returns a dict { intent_tag: confidence }. If
            present we run every candidate pattern through it and flag
            patterns the current classifier already thinks belong to
            another intent.
    """
    tag = str(candidate.get("tag", "")).strip()
    patterns_raw = candidate.get("patterns") or []
    responses_raw = candidate.get("responses") or []

    patterns: List[str] = [str(p).strip() for p in patterns_raw if str(p).strip()]
    responses: List[str] = [str(r).strip() for r in responses_raw if str(r).strip()]

    report = SanitizationReport(
        candidate_tag=tag,
        pattern_count=len(patterns),
        response_count=len(responses),
    )

    # ---- Tag --------------------------------------------------------------
    if not tag:
        report.findings.append(SanitizationFinding(
            "error", "tag_missing",
            "Intent tag is required.",
        ))
    elif not _TAG_RE.match(tag):
        report.findings.append(SanitizationFinding(
            "error", "tag_format",
            "Tag must be lowercase snake_case, 3–40 chars, starting with a letter.",
            {"received": tag},
        ))
    elif is_reserved_tag(tag):
        report.findings.append(SanitizationFinding(
            "error", "tag_reserved",
            f"'{tag}' is reserved for a code-owned tier or system state "
            "(safety gate, LLM-unavailable degrade, smalltalk, MCP bridges, …). "
            "The classifier must never be trained to predict it. Pick another tag.",
            {"received": tag},
        ))
    else:
        existing_tags = {i.get("tag") for i in existing_intents}
        if tag in existing_tags:
            report.findings.append(SanitizationFinding(
                "error", "tag_exists",
                f"An intent named '{tag}' already exists. Use a new tag or edit the existing one.",
            ))

    # ---- Patterns ---------------------------------------------------------
    if not patterns:
        report.findings.append(SanitizationFinding(
            "error", "no_patterns",
            "At least one training pattern is required.",
        ))
    if len(patterns) < 5:
        report.findings.append(SanitizationFinding(
            "warning", "few_patterns",
            f"Only {len(patterns)} patterns supplied. We recommend 10+ for stable classification.",
        ))

    seen_lower: Dict[str, int] = {}
    for i, p in enumerate(patterns):
        if not _ascii_round_trip(p):
            report.findings.append(SanitizationFinding(
                "error", "encoding",
                f"Pattern #{i + 1} contains characters that don't round-trip through UTF-8.",
                {"pattern": p},
            ))
            continue
        if len(p) < _PATTERN_MIN:
            report.findings.append(SanitizationFinding(
                "error", "pattern_too_short",
                f"Pattern #{i + 1} is shorter than {_PATTERN_MIN} chars.",
                {"pattern": p},
            ))
        if len(p) > _PATTERN_MAX:
            report.findings.append(SanitizationFinding(
                "warning", "pattern_too_long",
                f"Pattern #{i + 1} is {len(p)} chars (limit {_PATTERN_MAX}). Long patterns rarely help.",
                {"pattern": p[:80] + "…"},
            ))
        key = _norm(p)
        if key in seen_lower:
            report.findings.append(SanitizationFinding(
                "warning", "duplicate_pattern_within",
                f"Pattern #{i + 1} duplicates pattern #{seen_lower[key] + 1} (case-insensitive).",
                {"pattern": p},
            ))
        seen_lower[key] = i
        bad = _has_profanity(p)
        if bad:
            report.findings.append(SanitizationFinding(
                "error", "profanity_pattern",
                f"Pattern #{i + 1} contains disallowed words: {', '.join(bad)}.",
                {"pattern": p},
            ))

    # ---- Cross-intent duplicates -----------------------------------------
    existing_patterns: Dict[str, str] = {}
    for intent in existing_intents:
        owner = intent.get("tag", "")
        for p in intent.get("patterns", []) or []:
            existing_patterns[_norm(str(p))] = owner
    for i, p in enumerate(patterns):
        owner = existing_patterns.get(_norm(p))
        if owner and owner != tag:
            report.findings.append(SanitizationFinding(
                "warning", "duplicate_pattern_cross",
                f"Pattern #{i + 1} already exists under intent '{owner}'. "
                "Including it here will pull '{owner}' traffic into '{tag}'.".format(owner=owner, tag=tag),
                {"pattern": p, "owner": owner},
            ))

    # ---- Classifier collisions -------------------------------------------
    if classifier_predict_proba is not None and patterns:
        for i, p in enumerate(patterns):
            try:
                probs = classifier_predict_proba(p) or {}
            except Exception:
                continue
            if not probs:
                continue
            # Find the highest-prob *existing* intent (skip the candidate
            # tag itself, since the candidate isn't trained yet — though
            # in practice the proba comes from the *old* model anyway).
            top_intent, top_conf = max(probs.items(), key=lambda kv: kv[1])
            if top_intent == tag:
                continue
            if top_conf >= _COLLISION_HIGH_CONF:
                report.findings.append(SanitizationFinding(
                    "warning", "model_collision_high",
                    f"Pattern #{i + 1} would be confidently classified as '{top_intent}' "
                    f"({top_conf:.0%}). Including it may cause regressions on that intent.",
                    {"pattern": p, "existing_intent": top_intent, "confidence": top_conf},
                ))
            elif top_conf >= _COLLISION_MED_CONF:
                report.findings.append(SanitizationFinding(
                    "info", "model_collision_medium",
                    f"Pattern #{i + 1} overlaps moderately with '{top_intent}' ({top_conf:.0%}).",
                    {"pattern": p, "existing_intent": top_intent, "confidence": top_conf},
                ))

    # ---- Responses --------------------------------------------------------
    if not responses:
        report.findings.append(SanitizationFinding(
            "error", "no_responses",
            "At least one response is required.",
        ))
    if 0 < len(responses) < 3:
        report.findings.append(SanitizationFinding(
            "warning", "few_responses",
            f"Only {len(responses)} response(s). Add at least 3 for variety.",
        ))

    seen_resp: Dict[str, int] = {}
    for i, r in enumerate(responses):
        if not _ascii_round_trip(r):
            report.findings.append(SanitizationFinding(
                "error", "encoding",
                f"Response #{i + 1} contains characters that don't round-trip through UTF-8.",
                {"response": r[:80] + "…"},
            ))
            continue
        if len(r) < _RESPONSE_MIN:
            report.findings.append(SanitizationFinding(
                "warning", "response_too_short",
                f"Response #{i + 1} is shorter than {_RESPONSE_MIN} chars — looks like a stub.",
                {"response": r},
            ))
        if len(r) > _RESPONSE_MAX:
            report.findings.append(SanitizationFinding(
                "warning", "response_too_long",
                f"Response #{i + 1} is {len(r)} chars (limit {_RESPONSE_MAX}). Consider trimming.",
            ))
        key = _norm(r)
        if key in seen_resp:
            report.findings.append(SanitizationFinding(
                "warning", "duplicate_response",
                f"Response #{i + 1} duplicates response #{seen_resp[key] + 1}.",
            ))
        seen_resp[key] = i
        bad = _has_profanity(r)
        if bad:
            report.findings.append(SanitizationFinding(
                "error", "profanity_response",
                f"Response #{i + 1} contains disallowed words: {', '.join(bad)}.",
                {"response": r[:80] + "…"},
            ))

    return report
