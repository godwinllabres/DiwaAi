"""Anti-pattern mining over the chat logs (docs/governance_signoff.md §4).

You can only reduce what you count. Every month a share of chats fall into
patterns worth acting on:

  • unanswered_fallback — the brain had no answer (intent nlu_fallback/fallback)
  • off_topic          — out-of-scope / homework / school-comparison refusals
  • low_confidence     — answered, but the classifier was unsure (< threshold)
  • safety_abuse / safety_threat / safety_cooldown — moderation trips
  • safety_self_harm   — counted only; never themed or quoted, for dignity

`build_report()` clusters the messages in each bucket into emerging themes
(top uni/bi-grams after stop-word removal) with a few representative examples.
The themes tell the team what new intents to author, what lexicon gaps to
close, and which anti-patterns are trending — the input to the next iteration.

Pure + DB-agnostic on purpose: it takes plain row dicts, so it unit-tests
without a database and runs the same over SQLite or Postgres rows. Messages
are expected to already be PII-masked (see api/pii.py) at write time.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

FALLBACK_INTENTS = {"nlu_fallback", "fallback"}
OFF_TOPIC_INTENTS = {"out_of_scope", "off_topic_homework", "compare_to_other_school"}

# Buckets that get themed + example messages. self_harm is intentionally NOT
# here: we report its count but never cluster or quote crisis disclosures.
_THEMED_BUCKETS = (
    "unanswered_fallback",
    "off_topic",
    "low_confidence",
    "safety_abuse",
    "safety_threat",
)
_COUNT_ONLY_BUCKETS = ("safety_self_harm", "safety_cooldown")

# English + common Filipino/Taglish function words. Kept deliberately small —
# domain nouns (enrollment, scholarship, campus) must survive as theme signal.
_STOPWORDS = {
    # english
    "the", "a", "an", "is", "are", "am", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "for", "and", "or", "but", "if", "so", "as",
    "i", "you", "we", "they", "he", "she", "it", "me", "my", "your", "our",
    "this", "that", "these", "those", "there", "here", "what", "when", "where",
    "who", "why", "how", "which", "can", "could", "would", "should", "will",
    "do", "does", "did", "have", "has", "had", "get", "got", "with", "from",
    "about", "into", "than", "then", "just", "not", "no", "yes", "please",
    "pls", "hi", "hello", "hey", "po", "im", " im", "u", "ur",
    # filipino / taglish
    "ang", "ng", "sa", "na", "ba", "ko", "mo", "ka", "ako", "ikaw", "siya",
    "kami", "kayo", "sila", "yung", "ung", "may", "meron", "wala", "ito",
    "iyan", "iyon", "dito", "diyan", "doon", "kung", "para", "pero", "at",
    "o", "kasi", "lang", "din", "rin", "naman", "nga", "pa", "ba't", "anong",
    "ano", "saan", "kailan", "sino", "paano", "bakit", "alin", "mga", "ay",
    "kong", "niya", "nila", "namin", "natin", "nyo", "niyo", "opo", "salamat",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
# PII mask placeholders (api/pii.py). Strip the WHOLE token before word
# extraction — otherwise the inner words 'email' / 'phone' / 'id' survive and
# pollute the themes (a bare '[' can never start a token, so the old
# startswith('[') guard was dead).
_MASK_RE = re.compile(r"\[(?:email|phone|id)\]")


def _tokens(text: str) -> List[str]:
    cleaned = _MASK_RE.sub(" ", text or "")
    return [
        t
        for t in (w.lower().strip("'-") for w in _TOKEN_RE.findall(cleaned))
        if len(t) > 1 and t not in _STOPWORDS
    ]


def _terms(text: str) -> List[str]:
    """Unigrams + bigrams, stop-words removed. Bigrams surface real phrases
    ('enrollment schedule', 'good moral') the way single words can't."""
    toks = _tokens(text)
    grams = list(toks)
    grams += [f"{a} {b}" for a, b in zip(toks, toks[1:])]
    return grams


def bucket_for(intent: Optional[str], confidence: Optional[float],
               low_conf_threshold: float) -> Optional[str]:
    """Assign a row to an anti-pattern bucket, or None if it isn't one."""
    intent = (intent or "").strip()
    if intent.startswith("safety_"):
        cat = intent[len("safety_"):]
        return f"safety_{cat}" if f"safety_{cat}" in (
            _THEMED_BUCKETS + _COUNT_ONLY_BUCKETS
        ) else "safety_abuse"
    if intent in FALLBACK_INTENTS:
        return "unanswered_fallback"
    if intent in OFF_TOPIC_INTENTS:
        return "off_topic"
    if confidence is not None and confidence < low_conf_threshold:
        return "low_confidence"
    return None


def build_report(
    rows: List[Dict[str, Any]],
    low_conf_threshold: float = 0.5,
    top_terms: int = 8,
    examples_per_bucket: int = 5,
) -> Dict[str, Any]:
    """Cluster anti-pattern rows into per-bucket themes + cross-bucket trends.

    `rows` need only carry `user_message`, `intent`, `confidence`. Extra keys
    are ignored. Returns a JSON-serializable report."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for name in _THEMED_BUCKETS + _COUNT_ONLY_BUCKETS:
        buckets[name] = {"count": 0, "term_counter": Counter(), "examples": []}

    total = 0
    for row in rows:
        bucket = bucket_for(row.get("intent"), row.get("confidence"), low_conf_threshold)
        if bucket is None:
            continue
        if bucket not in buckets:  # unexpected safety_* category → fold into abuse
            bucket = "safety_abuse"
        total += 1
        b = buckets[bucket]
        b["count"] += 1
        if bucket in _COUNT_ONLY_BUCKETS:
            continue
        msg = (row.get("user_message") or "").strip()
        if msg:
            b["term_counter"].update(_terms(msg))
            example = msg[:160]
            # Dedup on the STORED (truncated) form, not the full message, so
            # identical messages longer than 160 chars aren't listed repeatedly.
            if len(b["examples"]) < examples_per_bucket and example not in b["examples"]:
                b["examples"].append(example)

    # Finalize: turn counters into ranked term lists; accumulate cross-bucket.
    cross: Counter = Counter()
    out_buckets: Dict[str, Any] = {}
    for name, b in buckets.items():
        entry: Dict[str, Any] = {"count": b["count"]}
        if name in _THEMED_BUCKETS:
            counter: Counter = b["term_counter"]
            entry["top_terms"] = [
                {"term": t, "count": c}
                for t, c in counter.most_common(top_terms)
                if c > 1  # a term seen once isn't a theme
            ]
            entry["examples"] = b["examples"]
            # Only fallback/off-topic/low-confidence feed the "what to build
            # next" trend — abuse themes aren't backlog items.
            if name in ("unanswered_fallback", "off_topic", "low_confidence"):
                for t, c in counter.items():
                    if " " in t and c > 1:  # bigrams only, to keep signal high
                        cross[t] += c
        out_buckets[name] = entry

    emerging = [
        {"term": t, "count": c} for t, c in cross.most_common(top_terms)
    ]

    return {
        "total_analyzed": total,
        "low_conf_threshold": low_conf_threshold,
        "buckets": out_buckets,
        "emerging_themes": emerging,
    }
