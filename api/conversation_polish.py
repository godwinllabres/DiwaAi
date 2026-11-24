"""Conversation-quality helpers for the LLM tier.

Small, deterministic pieces used by api/hybrid_chatbot.py:

  strip_rag_meta(reply)        removes retrieval boilerplate ("Based on the
                               excerpts provided, ...") the model leaks even
                               when the prompt forbids it. The prompt rule is
                               the first line of defence; this is the net.
  dedupe_reply(reply, prev)    drops blocks repeated inside one reply and flags
                               a reply that repeats the previous turn (the
                               same 3-bullet fallback served twice).
  is_pushback / wants_repeat / pivot_reply
                               "just create a list available online": answer
                               the limitation once instead of regenerating the
                               same fallback.
  is_contextual_followup(text) a short message that only makes sense against
                               the previous turn ("why only trece?", "what
                               about Imus?", "that one"). The intent tiers
                               answer these from the message alone and lose the
                               thread, so the cascade sends them to the LLM
                               with the conversation history instead.
                               followup_kind(text) says which kind:
                               "pushback" or "contrast".
  satellite_campus(label)      the satellite campus a retrieved passage is
                               about (from its title / URL / citation), or None
                               for university-wide and Main Campus material.
                               Used to keep satellite pages out of general
                               answers unless that campus was asked about.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional, Tuple

from .campus_context import CAMPUSES, extract_campus

# ── RAG meta-phrase scrubber ────────────────────────────────────────────────

_SOURCE_NOUN = r"(?:excerpts?|context|sources?|information|documents?|passages?|materials?|data|knowledge base)"
_INLINE_ADJ = r"(?:provided |given |available |above |supplied |official )?"
# Lead-ins also carry "official CvSU" ("Based on the official CvSU excerpts
# provided, ...") and a coordinated noun ("the excerpts and conversation
# context").
_SOURCE_ADJ = r"(?:provided |given |available |above |supplied )?(?:official )?(?:CvSU )?"
_SOURCE_COORD = r"(?: and (?:the )?(?:conversation )?(?:context|history|excerpts?))?"
_SOURCE_TAIL = r"(?: (?:provided|given|above|supplied|available)(?: to me)?)?"
_LEADIN_TAIL = rf"{_SOURCE_TAIL}(?: I (?:have|was given))?"
_LIST_PREFIX = r"(\s*(?:[-*•]|\d+[.)])?\s*\**)"
# Kept in the output: "I'm sorry, but based on the excerpts, X" -> "I'm sorry, but X".
_HEDGE = r"((?:(?:I['’]m|I am) sorry,? (?:but )?|Unfortunately,? |However,? )?)"
_LEADIN_VERB = r"(?:based (?:on|solely on|only on)|according to|from|per|looking at)"

# Opening clause: "Based on the excerpts provided, ..." / "According to the
# context, ..." / "From the information given, ..." The trailing [,:] is
# required, so "Based on the information provided by the Registrar, ..." stays.
_LEADING_META_RE = re.compile(
    rf"^{_LIST_PREFIX}{_HEDGE}{_LEADIN_VERB}\s+"
    rf"(?:the |my )?{_SOURCE_ADJ}{_SOURCE_NOUN}{_SOURCE_COORD}{_LEADIN_TAIL}\s*[,:]\s*",
    re.IGNORECASE,
)
# The same lead-in opening a later sentence on the line ("Enrollment is in
# June. Based on the excerpts, ..."). Excerpt-type nouns only: "From the data,
# enrollment rose" mid-paragraph is real prose.
_SENTENCE_META_RE = re.compile(
    rf"([.!?]\s+){_HEDGE}(?:based (?:on|solely on|only on)|according to)\s+"
    rf"(?:the |my )?{_SOURCE_ADJ}(?:excerpts?|context|passages?){_SOURCE_COORD}{_LEADIN_TAIL}"
    rf"\s*[,:]\s*([a-z]?)",
    re.IGNORECASE,
)
# "**Based on the excerpts provided**, X"
_BOLD_META_RE = re.compile(
    r"^(\s*(?:[-*•]|\d+[.)])?\s*)\*\*((?:based on|according to)[^*]{0,60})\*\*\s*[,:]\s*",
    re.IGNORECASE,
)
# "Based on the excerpts provided [Admission — ...], X" -> "According to [Admission — ...], X".
# The bracket moves byte-for-byte: the output guard matches it against the
# grounding citations.
_CITED_META_RE = re.compile(
    r"\b(?:based on|according to|from) the (?:official )?(?:CvSU )?excerpts?(?: provided)?\s+"
    r"(\[[^\[\]]{4,200}\])\s*,\s*",
    re.IGNORECASE,
)
# What is left after the output guard strips an invented citation:
# "According to [Invented], X" -> "According to, X".
_ORPHAN_LEADING_RE = re.compile(
    rf"^{_LIST_PREFIX}(?:(?:based on|according to|per)\s*[,:]|from\s*,)\s*", re.IGNORECASE)
_ORPHAN_SENTENCE_RE = re.compile(
    r"([.!?]\s+)(?:based on|according to)\s*[,:]\s*([a-z]?)", re.IGNORECASE)
# Filipino lead-ins. "impormasyon"/"datos"/"sipi" only with ibinigay/binigay,
# so "Ayon sa Registrar, ..." and ordinary "ayon sa impormasyon ng ..." survive.
# "sipi" is also the plain word for a document copy ("sa mga sipi ng Form 138").
_FIL_SRC = r"(?:excerpts?|konteksto|context)"
_FIL_GIVEN = r"(?:ibinigay|binigay)"
_FIL_SIPI = rf"(?:{_FIL_GIVEN}\s+na\s+sipi|sipi\s+na\s+{_FIL_GIVEN})"
_FIL_LEADING_META_RE = re.compile(
    rf"^{_LIST_PREFIX}(?:"
    rf"(?:batay|base|ayon)\s+sa\s+(?:mga\s+)?(?:{_FIL_GIVEN}\s+na\s+)?{_FIL_SRC}"
    rf"(?:\s+na\s+{_FIL_GIVEN}(?:\s+sa\s+akin)?)?"
    rf"|(?:batay|base|ayon)\s+sa\s+(?:mga\s+)?(?:{_FIL_GIVEN}\s+na\s+(?:impormasyon|datos)"
    rf"|(?:impormasyon|datos)\s+na\s+{_FIL_GIVEN}(?:\s+sa\s+akin)?"
    rf"|{_FIL_SIPI}(?:\s+sa\s+akin)?)"
    rf"|sa\s+mga\s+(?:(?:ibinigay\s+na\s+)?excerpts?(?:\s+na\s+ibinigay)?|{_FIL_SIPI})"
    rf")\s*[,:]\s*",
    re.IGNORECASE,
)
# "Hindi nabanggit sa mga excerpt ang X" -> "Wala po akong detalye tungkol sa X"
_FIL_SUBJECT_RE = re.compile(
    r"\bhindi\s+(?:po\s+)?(?:nabanggit|binanggit|nakasaad|makikita)\s+sa\s+(?:mga\s+)?"
    rf"(?:(?:{_FIL_GIVEN}\s+na\s+)?(?:excerpts?|konteksto)(?:\s+na\s+ibinigay)?|{_FIL_SIPI})\s+ang\b",
    re.IGNORECASE,
)
_FIL_INLINE_RE = re.compile(
    rf"\s*,?\s*sa\s+mga\s+(?:(?:ibinigay\s+na\s+)?excerpts?(?:\s+na\s+ibinigay)?|{_FIL_SIPI})"
    r"(?=[\s.,;:!?)]|$)",
    re.IGNORECASE,
)
# "The excerpts mention only Trece Martires." -> "I only have details on Trece Martires."
_EXCERPTS_ONLY_RE = re.compile(
    r"\bthe (?:provided )?(?:excerpts?|context|passages?)(?: provided| I have(?: access to)?)? "
    r"(?:only (?:mention|cover|include)s?|only talks? about|(?:mentions?|covers?|includes?) only)\b",
    re.IGNORECASE,
)
# Mid-sentence qualifier: "... about X in the excerpts provided." The excerpt
# nouns only — "in the information provided" style phrases are rare mid-
# sentence and "in the information" can be a real phrase. Not before "of":
# "within the context of RA 10931" is real prose.
_INLINE_META_RE = re.compile(
    rf"\s*,?\s*(?:in|from|within) (?:the )?{_INLINE_ADJ}(?:excerpts?|context|passages?)"
    rf"{_SOURCE_TAIL}(?: (?:I have|I was given))?(?=[\s.,;:!?)]|$)(?!\s+of\b)",
    re.IGNORECASE,
)
_KNOWLEDGE_BASE_RE = re.compile(
    r"\s*,?\s*(?:(?:in|from) my (?:current )?(?:knowledge base|training data|database)"
    r"|(?:in|from) what (?:was|I was|I've been|I have been|you) (?:provided|given|gave)(?: (?:to )?me)?"
    r"(?: right now)?(?=[\s.,;:!?)]|$)"
    # Clause end only: "Submit them in the documents I have listed above."
    r"|(?:in|from) the (?:documents?|materials?|sources?) (?:in front of me|I (?:have|was given)(?: access to)?)"
    r"(?: right now)?(?=\s*[.,;:!?)]|\s*$)"
    # Seen live (Claude Haiku, 2026-11-23): "... about Imus in my current set
    # of excerpts." Clause end only, like the documents form above.
    r"|(?:in|from) (?:my|the) current (?:set of )?(?:excerpts?|passages?|information|sources?)"
    r"(?=\s*[.,;:!?)]|\s*$)"
    r"|(?:in|from) my (?:set of )?(?:excerpts?|passages?)(?=\s*[.,;:!?)]|\s*$))",
    re.IGNORECASE,
)
# "The excerpt I have mentions that X" → "I only know that X" (seen live,
# 2026-11-23). Without "that": "The excerpt I have mentions X" → "I only
# have details on X".
_EXCERPTS_I_HAVE_RE = re.compile(
    r"\bthe (?:excerpts?|passages?|context) I (?:have(?: access to)?|was given) "
    r"(?:mentions?|says?|notes?|states?|shows?)( that)?\b",
    re.IGNORECASE,
)
# Same phrases opening the line: "In my knowledge base, X" -> "X". The
# inline forms above only eat the comma before the phrase, leaving ", X".
_LEADING_IN_META_RE = re.compile(
    rf"^{_LIST_PREFIX}(?:in|from|within) (?:my (?:knowledge base|training data|database)"
    rf"|(?:the )?{_INLINE_ADJ}(?:excerpts?|context|passages?))"
    rf"{_SOURCE_TAIL}(?: (?:I have|I was given))?\s*[,:]\s*",
    re.IGNORECASE,
)
# "The excerpts don't mention X" → "I don't have details on X"
_EXCERPTS_SUBJECT_RE = re.compile(
    r"\bthe (?:provided |given )?(?:excerpts?|context|passages?)(?: provided| given| I have)?"
    r" (?:do(?:es)? not|don['’]t|doesn['’]t) (?:mention|contain|include|cover|specify|say)\b",
    re.IGNORECASE,
)


# OSAS is Student Affairs, not the Registrar. Claude Haiku wrote "Office of
# the University Registrar (OSAS)" in 3 of 12 LLM turns across two live runs
# (2026-11-23/24); the glossary now says so too, this is the net.
_REGISTRAR_OSAS_RE = re.compile(
    r"\b((?:the )?(?:Office of the )?(?:University )?Registrar(?:['’]s Office)?)\s*\(OSAS\)",
    re.IGNORECASE,
)


# Robotic clarification opener seen live: "I don't have enough context to
# answer that. Could you clarify…" → "Could you clarify…". Only a GENERIC
# lead-in ("…context.", "…context to answer that.") and only when the next
# sentence is the clarification question: a lead-in that names its subject
# ("…context to confirm this applies to Imus.") is a material caveat and
# stays (review 2026-11-24).
_NO_CONTEXT_LEAD_RE = re.compile(
    r"^\s*I (?:don['’]t|do not) have (?:enough |much )?context"
    r"(?: (?:to (?:answer|respond to|go on|help with)|for|on) (?:that|this|it|your question))?"
    r"\s*(?:[.!]|\s+—)\s*",
    re.IGNORECASE,
)
# The remainder must reach a question within its first two sentences.
_CLARIFY_QUESTION_RE = re.compile(r"^(?:[^.!?\n]*[.!]\s+)?[^.!?\n]*\?")


# Catch-all for "the excerpt" as a sentence subject with any singular verb
# ("The excerpt I have lists…", "The excerpt mentions that…", live 2026-11-24)
# after the specific rewrites above: → "My information lists…". Singular verbs
# only, so the swap stays grammatical; plural forms are left alone.
_EXCERPT_SUBJECT_ANY_RE = re.compile(
    r"\b([Tt])he (?:provided |given )?(?:excerpt|passage)(?: provided| given| I have(?: access to)?)?"
    r"(?= (?:[a-z]+s|does|doesn['’]t|has|is|was)\b)",
)


def strip_rag_meta(reply: Optional[str]) -> Optional[str]:
    """Remove retrieval boilerplate from an LLM reply; None/empty pass through.

    Also corrects the one office mix-up seen live ("Registrar (OSAS)")."""
    if not reply:
        return reply
    reply = _REGISTRAR_OSAS_RE.sub(r"\1", reply)
    trimmed = _NO_CONTEXT_LEAD_RE.sub("", reply)
    if trimmed != reply and trimmed and _CLARIFY_QUESTION_RE.match(trimmed):
        reply = trimmed[0].upper() + trimmed[1:]
    out = []
    for line in reply.split("\n"):
        new = _CITED_META_RE.sub(
            lambda m: ("According to " if m.group(0)[0].isupper() else "according to ")
            + m.group(1) + ", ", line)
        new = _BOLD_META_RE.sub(
            lambda m: m.group(1) if re.search(_SOURCE_NOUN, m.group(2), re.IGNORECASE)
            else m.group(0), new)
        new = _LEADING_META_RE.sub(r"\1\2", new)
        new = _LEADING_IN_META_RE.sub(r"\1", new)
        new = _FIL_LEADING_META_RE.sub(r"\1", new)
        new = _ORPHAN_LEADING_RE.sub(r"\1", new)
        new = _SENTENCE_META_RE.sub(
            lambda m: m.group(1) + m.group(2) + (m.group(3) if m.group(2) else m.group(3).upper()),
            new)
        new = _ORPHAN_SENTENCE_RE.sub(lambda m: m.group(1) + m.group(2).upper(), new)
        new = _EXCERPTS_SUBJECT_RE.sub("I don't have details on", new)
        new = _EXCERPTS_ONLY_RE.sub("I only have details on", new)
        new = _EXCERPTS_I_HAVE_RE.sub(
            lambda m: "I only know that" if m.group(1) else "I only have details on", new)
        new = _EXCERPT_SUBJECT_ANY_RE.sub(
            lambda m: "My information" if m.group(1) == "T" else "my information", new)
        new = _FIL_SUBJECT_RE.sub(
            lambda m: ("W" if m.group(0)[0].isupper() else "w") + "ala po akong detalye tungkol sa",
            new)
        new = _INLINE_META_RE.sub("", new)
        new = _FIL_INLINE_RE.sub("", new)
        new = _KNOWLEDGE_BASE_RE.sub("", new)
        if new != line:
            # A removed phrase can still leave the line opening on ", X".
            new = re.sub(r"^(\s*(?:[-*•]|\d+[.)])?\s*\**)[,;:]\s*", r"\1", new)
            # Re-capitalise a sentence whose opening clause was removed,
            # keeping list markers / markdown emphasis in front of it.
            new = re.sub(r"^(\s*(?:[-*•]|\d+[.)])?\s*\**)([a-z])",
                         lambda m: m.group(1) + m.group(2).upper(), new)
            new = re.sub(r"(?<=\S)[ \t]{2,}", " ", new)
            new = re.sub(r"(?<=\S)[ \t]+([,.;:!?])", r"\1", new)
        out.append(new)
    return "\n".join(out).strip()


# ── Contextual follow-up detector ───────────────────────────────────────────

# Openers that lean on the previous turn. English + Filipino/Taglish.
# A strong opener counts only when what follows it has no topic of its own
# (_is_topic_free): "what about Imus?" is a follow-up, "how about the dorm
# fees" and "and where is the clinic" are new questions the intent tiers
# answer. Routing those to the LLM dropped their curated answers.
_STRONG_OPENER_RE = re.compile(
    r"^\s*(?:what about|how about|and (?:what|how|the|for|if|where|when)|"
    r"but (?:what|why|how)|then (?:what|how)|is (?:that|it)|does (?:that|it)|"
    r"are (?:those|they)|why (?:only|just|not)|bakit (?:lang|hindi)|paano naman|"
    r"pano (?:naman|kung)|ano naman|eh?\s+(?:paano|pano|bakit|yung|ano|sa)|"
    r"e di|edi|yung (?:sa|para))\b",
    re.IGNORECASE,
)
# Openers that also start full questions ("is it possible to shift courses",
# "bakit hindi pa lumalabas ang grades ko"): a follow-up only when very short
# ("is that free?") or topic-free.
_SHORT_ONLY_OPENER_RE = re.compile(
    r"^\s*(?:is (?:that|it)|does (?:that|it)|are (?:those|they)|bakit hindi)\b", re.IGNORECASE)
_SHORT_ONLY_MAX_WORDS = 4
# Weak openers also start standalone questions ("why is tuition free at
# CvSU?", "so how do I enroll", "bakit mahal ang tuition"), so they count only
# in a very short message or with a back-reference/contrast cue after them
# ("bakit trece lang?", "why is that?").
_WEAK_OPENER_RE = re.compile(
    r"^\s*(?:why|bakit|so|but|also|what if|can i (?:also|still))\b",
    re.IGNORECASE,
)
_CONTRAST_CUE_RE = re.compile(r"\b(?:lang|only|just|naman|instead)\b", re.IGNORECASE)
# Closings and questions back at Sevi are not follow-ups: "no thanks" is
# not pushback, and "how about you?" belongs to small talk.
_CLOSING_RE = re.compile(
    r"^\s*(?:no,?\s*)?(?:thanks?|thank you|salamat|ok(?:ay)?|sige|never ?mind|nvm|"
    r"just checking)\b",
    re.IGNORECASE,
)
_SELF_REF_RE = re.compile(r"^\s*(?:(?:how|what) about you|and you|ikaw)\b", re.IGNORECASE)
# Words that point back at something already said. Not "there"/"more":
# "are there night classes" is a standalone question.
_REFERENCE_RE = re.compile(
    r"\b(?:that|this|those|these|it|its|them|they|the same|the other|"
    r"instead|only|either|neither|else|iyan|yan|yun|iyon|ito|dun|doon|iyong)\b",
    re.IGNORECASE,
)
# Pushback that rejects the previous answer outright ("you already said").
_PUSHBACK_FIXED_RE = re.compile(
    r"^\s*(?:not that|you (?:already|just) (?:said|told)|that(?:'s| is) not (?:what|it)|"
    r"(?:that )?doesn['’]?t (?:help|answer)|hindi (?:yan|iyan|yun))",
    re.IGNORECASE,
)
# Pushback openers that also start fresh asks ("just give me the list of
# deans", "i mean online enrollment"): pushback only when the rest has no
# topic of its own, points back, or asks for a live lookup.
_PUSHBACK_OPENER_RE = re.compile(
    r"^\s*(?:(?:can you |please )?just|no,?|i (?:said|meant|mean)|sabi ko)\b",
    re.IGNORECASE,
)
_MAX_FOLLOWUP_WORDS = 9
_MAX_PUSHBACK_WORDS = 16

# Words that carry no topic of their own: function words, back-references,
# student level / year, generic aspects of whatever was just discussed
# ("and the requirements?"), and generic "give me / list / online" asks.
_LIGHT_WORDS = frozenset("""
a an the and or but so then too also still if for at in on to of about with from by
is are was were be does do did can could will would should may might
i me my mine we us our you your it its that this those these them they one ones
what how why when where which who whom
only just instead other others else same either neither all any some more less
not no yes please pls ok okay
mean meant said say saying asked ask asking
po ba lang naman din rin daw sa ng ang mga yung iyong nang na para kung ako ko
e eh edi di paano pano bakit ano hindi iyan yan yun iyon ito dun doon diyan dito
nga pa mo ninyo nyo kayo tayo natin namin
campus campuses branch branches cvsu university city
graduate grad grads undergraduate undergrad college school masters masteral master's
phd doctorate doctoral freshman freshmen freshie transferee transferees shiftee shifter
returnee returnees senior high shs first second third fourth year years level
next last incoming continuing new old student students foreign international alumni
semester sem summer midyear
requirement requirements fee fees cost costs price prices schedule schedules deadline
deadlines step steps process procedure procedures office offices contact contacts
location address date dates form forms document documents qualification qualifications
rule rules policy policies option options details detail info information
list lists link links answer answers free available
give show tell send create make compile get find search look check browse google
online website site web internet up
""".split())
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
_CAMPUS_ALIAS_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(a) for aliases in CAMPUSES.values() for a in aliases)
    + r"|dasma(?:ri[nñ]as)?)\b",
    re.IGNORECASE,
)


def _is_topic_free(rest: str) -> bool:
    """True when `rest` names nothing beyond a campus, level, year or a
    generic aspect, so it only makes sense against the previous turn."""
    rest = _CAMPUS_ALIAS_RE.sub(" ", rest or "")
    return all(w.lower() in _LIGHT_WORDS or _YEAR_RE.match(w)
               for w in re.findall(r"[\w'’]+", rest))


def _pushback_kind(text: str) -> bool:
    if _PUSHBACK_FIXED_RE.search(text):
        return True
    m = _PUSHBACK_OPENER_RE.match(text)
    if not m:
        return False
    rest = text[m.end():]
    return bool(_is_topic_free(rest) or _REFERENCE_RE.search(rest) or is_live_lookup(text))


def followup_kind(text: str) -> Optional[str]:
    """"pushback", "contrast", or None for a standalone message.

    "contrast" covers any short message that leans on the previous turn
    ("why only trece?", "what about Imus?", "that one?").
    """
    words = re.findall(r"[\w'’]+", text or "")
    if not words:
        return None
    if _CLOSING_RE.search(text) or _SELF_REF_RE.search(text):
        return None
    if _pushback_kind(text):
        return "pushback" if len(words) <= _MAX_PUSHBACK_WORDS else None
    if len(words) > _MAX_FOLLOWUP_WORDS:
        return None
    strong = _STRONG_OPENER_RE.match(text)
    if strong:
        if _SHORT_ONLY_OPENER_RE.match(text) and len(words) <= _SHORT_ONLY_MAX_WORDS:
            return "contrast"
        return "contrast" if _is_topic_free(text[strong.end():]) else None
    weak = _WEAK_OPENER_RE.match(text)
    if weak:
        rest = text[weak.end():]
        # <=3, not 4: "bakit mahal ang tuition" is a full question.
        if len(words) <= 3 or _REFERENCE_RE.search(rest) or _CONTRAST_CUE_RE.search(rest):
            return "contrast"
        return None
    # A very short message made mostly of back-references ("that one?",
    # "and those?") with no topic of its own.
    return "contrast" if len(words) <= 4 and _REFERENCE_RE.search(text) else None


def is_contextual_followup(text: str) -> bool:
    """True for a short message that depends on the previous turn."""
    return followup_kind(text) is not None


# ── Repeats and pushback ────────────────────────────────────────────────────

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# A request to fetch something live, which an offline assistant cannot do.
# Needs a fetch verb aimed at the web, not the bare word: "online programs",
# "online enrollment" and "the CvSU website address" are topics with curated
# answers. The web target must close the clause ("... available online").
_LIVE_FETCH_RE = re.compile(
    r"\b(?:search|look(?: it)? up|browse|google|check|find|hanapin|i-?search|pull|get|"
    r"fetch|create|make|compile|list|give|show)\b.*?"
    r"\b(?:online|(?:on|from|in) (?:the )?(?:cvsu )?(?:web ?site|internet|web|site)|"
    r"the (?:web|internet|web ?site))"
    r"(?:\s+(?:for me|please|pls|po|na lang|lang|instead|now|yourself))*\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_LIVE_VERB_RE = re.compile(
    r"\b(?:google it|search (?:it|for it|online|the (?:web|internet|web ?site))|look it up|"
    r"browse (?:the )?(?:web|internet|web ?site|site)|i-?search mo|hanapin mo)\b",
    re.IGNORECASE,
)
# Filipino "look it up" asks with no "just"-type opener ("hanapin mo na lang
# online"). Kept out of the follow-up openers so routing is unchanged.
_LIVE_ASK_RE = re.compile(r"^\s*(?:hanapin mo|i-?search mo|pa-?search)\b", re.IGNORECASE)
_WANTS_REPEAT_RE = re.compile(r"\b(?:repeat|again|ulit(?:in)?|one more time|isa pa)\b", re.IGNORECASE)


def _norm(text: str) -> str:
    text = _LIST_ITEM_RE.sub("", (text or "").lower())
    text = re.sub(r"[^\w ]+", "", text.replace("\n", " "))
    return " ".join(text.split())


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:])\s+|\n+")


def _key_terms(text: str) -> set:
    """Campus names, numbers, and capitalised words that do not open a
    sentence — the facts that make two similarly-worded replies different."""
    terms = {m.group(0).lower() for m in _CAMPUS_ALIAS_RE.finditer(text or "")}
    terms.update(re.findall(r"\d+(?:[.,:/]\d+)*", text or ""))
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        words = re.findall(r"[\w'’-]+", _LIST_ITEM_RE.sub("", sentence).lstrip("*# "))
        terms.update(w.lower() for w in words[1:] if w[0].isupper())
    return terms


def dedupe_reply(reply: str, previous: Optional[str] = None) -> Tuple[str, bool]:
    """(cleaned_reply, is_repeat_of_previous).

    Drops paragraphs and list items repeated inside the reply (the 3-bullet
    fallback served twice in one answer). Across turns it only FLAGS a repeat
    of the previous reply — never trims, since a partial re-answer can be
    legitimate.
    """
    if not reply:
        return reply, bool(previous)
    seen_paras, seen_items, kept, dropped = set(), set(), [], False
    for para in re.split(r"\n\s*\n", reply):
        key = _norm(para)
        if key and key in seen_paras:
            dropped = True
            continue
        if key:
            seen_paras.add(key)
        lines = []
        for line in para.split("\n"):
            if _LIST_ITEM_RE.match(line):
                item = _norm(line)
                if len(item) >= 12 and item in seen_items:
                    dropped = True
                    continue
                seen_items.add(item)
            lines.append(line)
        kept.append("\n".join(lines))
    if dropped:
        reply = "\n\n".join(p for p in kept if p.strip()).strip()
    if not reply:
        return reply, True
    if not previous:
        return reply, False
    # Same shape, different facts ("At Imus campus ..." then "At Bacoor
    # campus ...") is a new answer, not a repeat.
    if _key_terms(reply) - _key_terms(previous):
        return reply, False
    cur, prev = _norm(reply), _norm(previous)
    if cur and difflib.SequenceMatcher(None, cur, prev).ratio() >= 0.85:
        return reply, True
    prev_lines = {_norm(line) for line in previous.split("\n")}
    lines = [n for n in (_norm(line) for line in reply.split("\n")) if len(n) >= 20]
    if lines and sum(n in prev_lines for n in lines) >= 0.8 * len(lines):
        return reply, True
    return reply, False


def is_pushback(text: str) -> bool:
    """Pushback on the previous answer ("just create a list available online")."""
    return bool(_pushback_kind(text or "") or _LIVE_ASK_RE.search(text or ""))


def is_live_lookup(text: str) -> bool:
    """The message asks Sevi to fetch something live (website, online, search)."""
    return bool(_LIVE_FETCH_RE.search(text or "") or _LIVE_VERB_RE.search(text or ""))


def wants_repeat(text: str) -> bool:
    """The user asked to hear the previous answer again."""
    return bool(_WANTS_REPEAT_RE.search(text or ""))


def pivot_reply(filipino: bool, live_lookup: bool) -> str:
    """Acknowledge the limitation once and offer a narrower ask. No CvSU facts."""
    if filipino:
        if live_lookup:
            return ("Offline assistant po ako, kaya hindi ako makapag-browse ng live na pahina "
                    "o makakuha ng listahan mula sa website. Sabihin lang po kung aling "
                    "kolehiyo, campus, o opisina ang kailangan ninyo at ibibigay ko ang alam "
                    "ko — o tingnan ang https://cvsu.edu.ph.")
        return ("Iyan na po ang lahat ng alam ko tungkol diyan, kaya hindi ko na po uulitin. "
                "Sabihin lang po kung aling kolehiyo, campus, o opisina ang tinatanong ninyo "
                "para mas matulungan ko kayo — o tingnan ang https://cvsu.edu.ph.")
    if live_lookup:
        return ("I'm an offline assistant, so I can't browse live pages or pull a list from "
                "the website for you. Tell me which college, campus, or office you need and "
                "I'll share what I have on it — or check https://cvsu.edu.ph directly.")
    return ("That's everything I have on that one, so I won't repeat myself. If you tell me "
            "which college, campus, or office you're asking about, I can narrow it down — "
            "or check https://cvsu.edu.ph.")


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
