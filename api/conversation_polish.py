"""Conversation-quality helpers for the LLM tier.

Three small, deterministic pieces used by api/hybrid_chatbot.py:

  strip_rag_meta(reply)        removes retrieval boilerplate ("Based on the
                               excerpts provided, ...") the model leaks even
                               when the prompt forbids it. The prompt rule is
                               the first line of defence; this is the net.
  is_contextual_followup(text) a short message that only makes sense against
                               the previous turn ("why only trece?", "what
                               about Imus?", "that one"). The intent tiers
                               answer these from the message alone and lose the
                               thread, so the cascade sends them to the LLM
                               with the conversation history instead.
  satellite_campus(label)      the satellite campus a retrieved passage is
                               about (from its title / URL / citation), or None
                               for university-wide and Main Campus material.
                               Used to keep satellite pages out of general
                               answers unless that campus was asked about.
"""
from __future__ import annotations

import re
from typing import Optional

from .campus_context import extract_campus

# ── RAG meta-phrase scrubber ────────────────────────────────────────────────

_SOURCE_NOUN = r"(?:excerpts?|context|sources?|information|documents?|passages?|materials?|data)"
_SOURCE_ADJ = r"(?:provided |given |available |above |supplied |official )?"
_SOURCE_TAIL = r"(?: (?:provided|given|above|supplied|available)(?: to me)?)?"

# Opening clause: "Based on the excerpts provided, ..." / "According to the
# context, ..." / "From the information given, ..."
_LEADING_META_RE = re.compile(
    rf"^(\s*(?:[-*•]|\d+[.)])?\s*\**)"
    rf"(?:based (?:on|solely on|only on)|according to|from|per|looking at)\s+"
    rf"(?:the |my )?{_SOURCE_ADJ}{_SOURCE_NOUN}{_SOURCE_TAIL}\s*[,:]\s*",
    re.IGNORECASE,
)
# Mid-sentence qualifier: "... about X in the excerpts provided." The excerpt
# nouns only — "in the information provided" style phrases are rare mid-
# sentence and "in the information" can be a real phrase.
_INLINE_META_RE = re.compile(
    rf"\s*,?\s*(?:in|from|within) (?:the )?{_SOURCE_ADJ}(?:excerpts?|context|passages?)"
    rf"{_SOURCE_TAIL}(?: (?:I have|I was given))?(?=[\s.,;:!?)]|$)",
    re.IGNORECASE,
)
_KNOWLEDGE_BASE_RE = re.compile(
    r"\s*,?\s*(?:in|from) my (?:knowledge base|training data|database)(?=[\s.,;:!?)]|$)",
    re.IGNORECASE,
)
# "The excerpts don't mention X" → "I don't have details on X"
_EXCERPTS_SUBJECT_RE = re.compile(
    r"\bthe (?:provided |given )?(?:excerpts?|context|passages?)(?: provided| given)?"
    r" (?:do(?:es)? not|don['’]t|doesn['’]t) (?:mention|contain|include|cover|specify|say)\b",
    re.IGNORECASE,
)


def strip_rag_meta(reply: Optional[str]) -> Optional[str]:
    """Remove retrieval boilerplate from an LLM reply; None/empty pass through."""
    if not reply:
        return reply
    out = []
    for line in reply.split("\n"):
        new = _LEADING_META_RE.sub(r"\1", line)
        new = _EXCERPTS_SUBJECT_RE.sub("I don't have details on", new)
        new = _INLINE_META_RE.sub("", new)
        new = _KNOWLEDGE_BASE_RE.sub("", new)
        if new != line:
            # Re-capitalise a sentence whose opening clause was removed,
            # keeping list markers / markdown emphasis in front of it.
            new = re.sub(r"^(\s*(?:[-*•]|\d+[.)])?\s*\**)([a-z])",
                         lambda m: m.group(1) + m.group(2).upper(), new)
        out.append(new)
    return "\n".join(out).strip()


# ── Contextual follow-up detector ───────────────────────────────────────────

# Openers that lean on the previous turn. English + Filipino/Taglish.
_FOLLOWUP_OPENER_RE = re.compile(
    r"^\s*(?:why(?: only| just| not)?|how about|what about|and (?:what|how|the|for|if)|"
    r"but (?:what|why|how|the)|so (?:what|how|is|does|can)|then (?:what|how)|"
    r"what if|is that|does that|can i (?:also|still)|also\b|"
    r"bakit(?: lang| hindi)?|paano naman|eh (?:paano|bakit|yung|ano)|ano naman|"
    r"e di|edi|pano (?:naman|kung)|yung (?:sa|para))\b",
    re.IGNORECASE,
)
# Words that point back at something already said.
_REFERENCE_RE = re.compile(
    r"\b(?:that|this|those|these|it|its|them|they|there|the same|the other|"
    r"instead|only|either|neither|else|more|iyan|yan|yun|iyon|ito|dun|doon|iyong)\b",
    re.IGNORECASE,
)
# Pushback on the previous answer ("just give me a list", "you already said").
_PUSHBACK_RE = re.compile(
    r"^\s*(?:just |can you just |please just |no,? |not that|i (?:said|meant|mean)|"
    r"you (?:already|just) (?:said|told)|that(?:'s| is) not (?:what|it)|"
    r"(?:that )?doesn['’]?t (?:help|answer)|hindi (?:yan|iyan|yun)|sabi ko)",
    re.IGNORECASE,
)
_MAX_FOLLOWUP_WORDS = 9


def is_contextual_followup(text: str) -> bool:
    """True for a short message that depends on the previous turn."""
    words = re.findall(r"[\w'’]+", text or "")
    if not words:
        return False
    if _PUSHBACK_RE.search(text):
        return len(words) <= 16
    if len(words) > _MAX_FOLLOWUP_WORDS:
        return False
    if _FOLLOWUP_OPENER_RE.search(text):
        return True
    # A very short message made mostly of back-references ("that one?",
    # "and those?") with no topic of its own.
    return len(words) <= 4 and bool(_REFERENCE_RE.search(text))


# ── Campus scope of a retrieved passage ─────────────────────────────────────

_MAIN_CAMPUS = "Indang (Main Campus)"


def satellite_campus(label: str) -> Optional[str]:
    """Satellite campus named in a passage title/URL/citation, else None."""
    if not label:
        return None
    # URL slugs use hyphens ("trece-martires-city-campus").
    text = label.replace("-", " ")
    # Only pages ABOUT a campus ("Imus Campus Officials", ".../trece-martires-
    # city-campus/"), not news that merely names the town ("... LGU Maragondon").
    if not re.search(r"\b(?:campus|ccat)\b", text, re.IGNORECASE):
        return None
    campus = extract_campus(text)
    return None if campus in (None, _MAIN_CAMPUS) else campus
