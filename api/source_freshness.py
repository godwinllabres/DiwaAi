"""Freshness follow-ups: "is this the latest?" about the previous reply.

    Reads : data/college_programs.json (through api.college_programs)
    Writes: nothing
    Usage : ask = freshness_ask(text); if ask: freshness_reply(ask, prev_turn)

WHY THIS EXISTS
---------------
"is this the latest?" after a graduate-program answer got the welcome
message (2026-11-25). Follow-up routing recognised the turn, but the
message has no topic words, so the conversational guard read it as
small talk and greeted the student — who had asked whether the list in
front of them was out of date. It was: the answer cited the 2018 GS-OLC
page while CvSU's College of Advanced and Professional Studies (CAPS)
publishes a newer catalog.

The question is about the previous reply's SOURCE, so it is answered
from that source and needs no language model: the previous turn's
citation (or curated topic) is looked up in the program registry, which
records each college's current page and the pages it superseded. With no
previous answer and no named topic, Sevi asks which list is meant.
"""
import re
from typing import Optional

from .college_programs import (
    college_by_key,
    college_for_url,
    find_college,
    format_date,
)

# Curated intents whose answer is about one registry college. Their replies
# cite the college's page, but the curated text and the registry are edited
# separately, so the intent is the more stable key.
_INTENT_COLLEGE = {"graduate_programs": "caps"}

_MAX_WORDS = 16

_YEAR_SPAN_RE = re.compile(
    r"(?:\b(?:for|as\s+of|in|sa|ngayong|this|ng)\s+)?(?:the\s+)?"
    r"(?:\ba\.?\s?y\.?|\bs\.?\s?y\.?|\bschool\s+year|\bacademic\s+year|\btaong\s+panuruan)?\s*"
    r"\b((?:19|20)\d{2})\s*(?:[-–—/]|to)\s*((?:19|20)?\d{2})\b",
    re.IGNORECASE,
)
_THIS_TERM_RE = re.compile(
    r"\b(?:for|as\s+of|in|sa)?\s*(?:this|ngayong)\s+(?:school\s+year|academic\s+year|a\.?y\.?|"
    r"s\.?y\.?|year|semester|sem|taon|semestre)\b",
    re.IGNORECASE,
)

_SUBJ = (r"(?:this|that|it|these|those|ito|iyan|yan|to|yun|iyon)"
         r"(?:\s+(?:list|info|information|answer|data|catalog|one|page|listahan))?"
         r"|the\s+(?:list|info|information|answer|data|catalog|page)")
_STATE = (r"(?:the\s+)?(?:latest|newest|most\s+recent|current|updated|up[\s-]?to[\s-]?date|"
          r"outdated|old|accurate|valid|new)"
          r"(?:\s+(?:one|version|list|info|information|catalog|data))?")
_SUBJ_FIL = (r"(?:ito|iyan|yan|to|yun|iyon|"
             r"ang\s+(?:listahan|info|impormasyon|catalog|sagot)(?:\s+(?:na\s+)?(?:ito|iyan|yan))?)")
_STATE_FIL = r"(?:updated|latest|current|bago|valid|accurate|up[\s-]?to[\s-]?date)"
_TAIL = r"(?:\s+(?:pa|na))?(?:\s+ba)?"
_TOPIC = r"(?:the\s+)?(?P<topic>[\w/&'.-]+(?:\s+[\w/&'.-]+){0,7}?)"

_FRESH_RES = [re.compile(r"^(?:" + p + r")$", re.IGNORECASE) for p in (
    # is this (still) the latest / is that list current / are these updated
    rf"(?:is|are)\s+(?:{_SUBJ})\s+(?:still\s+)?{_STATE}{_TAIL}",
    # is the CAPS list (still) the latest — topic must name a registry college
    rf"(?:is|are)\s+{_TOPIC}\s+(?:still\s+)?{_STATE}{_TAIL}",
    rf"when\s+(?:was|were)\s+(?:{_SUBJ})\s+(?:last\s+)?(?:updated|published|posted)",
    rf"how\s+(?:old|recent|current|updated|up[\s-]?to[\s-]?date)\s+(?:is|are)\s+(?:{_SUBJ})",
    r"(?:is\s+there|do\s+you\s+have)\s+(?:a\s+|an\s+)?(?:newer|more\s+recent|updated|latest|newest)"
    r"(?:\s+(?:one|version|list|catalog))?",
    r"(?:is\s+it\s+)?(?:still\s+)?(?:the\s+)?(?:latest|updated|up[\s-]?to[\s-]?date|current)"
    r"(?:\s+(?:pa|na))?(?:\s+ba)?",
    # updated pa ba ito / latest na ba yan / bago pa ba ang listahan
    rf"{_STATE_FIL}\s+(?:(?:pa|na)\s+)?ba\s+(?:(?:pa|na)\s+)?(?:{_SUBJ_FIL})(?:\s+(?:ng|sa)\s+{_TOPIC})?",
    # ito na ba ang pinakabago / yan ba ang latest
    rf"(?:ito|iyan|yan|to|yun)\s+(?:(?:na|pa)\s+)?ba\s+(?:ang\s+)?"
    r"(?:pinaka-?bago|pinaka-?huli|latest|updated|current)(?:\s+na)?(?:\s+\w+)?",
    rf"(?:{_SUBJ_FIL})\s+ba\s+(?:ay\s+)?(?:updated|latest|luma|bago|current)",
    rf"luma\s+na\s+ba\s+(?:{_SUBJ_FIL})",
    r"kailan\s+(?:huling\s+)?(?:na-?update|in-?update|inupdate|nai-?update)\s+(?:ito|iyan|yan|to|ang\s+\w+)",
)]

_FILLER_RE = re.compile(r"^(?:(?:but|ok|okay|wait|hmm+|so|and|uh+m?|um+|sevi|hi|hey|teka|pero)[\s,]+)+",
                        re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)<>\]\"'“”]+")


def _ay_label(match: re.Match) -> str:
    start, end = match.group(1), match.group(2)
    if len(end) == 2:
        end = start[:2] + end
    return f"AY {start}–{end}"


def freshness_ask(text: str) -> Optional[dict]:
    """{"ay": label|None, "college": dict|None} when `text` asks whether an
    answer is current; None otherwise. "college" is set only when the
    message itself names one ("is the CAPS list the latest?")."""
    if not text:
        return None
    t = text.strip().replace("’", "'")
    if len(re.findall(r"[\w']+", t)) > _MAX_WORDS:
        return None
    ay = None
    year = _YEAR_SPAN_RE.search(t)
    if year:
        ay = _ay_label(year)
        t = t[:year.start()] + " " + t[year.end():]
    else:
        term = _THIS_TERM_RE.search(t)
        if term:
            ay = "this school year"
            t = t[:term.start()] + " " + t[term.end():]
    t = re.sub(r"\bpo\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"[?!.]+", " ", t)
    t = _FILLER_RE.sub("", " ".join(t.split()))
    for rx in _FRESH_RES:
        m = rx.match(t)
        if not m:
            continue
        topic = m.groupdict().get("topic")
        college = None
        if topic:
            college = find_college(topic)
            if college is None:
                # "is the library open now" shape with a topic Sevi has no
                # dated source for: a new question, not a freshness check.
                continue
        return {"ay": ay, "college": college}
    return None


def ay_text(ask: dict, filipino: bool) -> Optional[str]:
    if not ask.get("ay"):
        return None
    if ask["ay"] == "this school year":
        return "ngayong school year" if filipino else "this school year"
    return ask["ay"]


def strip_years(text: str) -> str:
    """The message without its academic-year span — "AY" reads as Filipino
    "ay" to the language detector."""
    return _YEAR_SPAN_RE.sub(" ", text or "")


def _college_for_turn(prev: Optional[dict]) -> tuple[Optional[dict], Optional[bool]]:
    """(college, cited_current) for the previous turn's source."""
    if not prev:
        return None, None
    reply = prev.get("bot_response") or ""
    cited = college_for_url(reply)
    if cited:
        return cited
    key = _INTENT_COLLEGE.get(prev.get("intent") or "")
    if key:
        college = college_by_key(key)
        if college:
            # A curated answer about the college that cites none of its pages.
            return college, None
    return None, None


def _sources_block(college: dict, filipino: bool) -> str:
    parts = [f"{college.get('source_label') or college['abbr'] + ' program page'} — {college['source_url']}"]
    contact = college.get("contact") or {}
    if contact.get("source_url"):
        parts.append(f"{contact.get('source_label', 'CvSU directory')} — {contact['source_url']}")
    return ("Mga pinagmulan: " if filipino else "Sources: ") + " · ".join(parts)


def _college_reply(college: dict, cited_current: Optional[bool], ay: Optional[str],
                   filipino: bool) -> str:
    lang = "fil" if filipino else "en"
    abbr = college["abbr"]
    count = len(college.get("programs", []))
    old = (college.get("superseded_sources") or [None])[0]
    contact = college.get("contact") or {}
    phone = contact.get("phone")
    lines = []

    published = format_date(college.get("source_date") or "")
    if not old:
        # A dated page with nothing newer on record (CEIT's is from 2018).
        if filipino:
            lines.append(f"Galing po ang listahang iyan sa opisyal na pahina ng {abbr} sa CvSU, "
                         f"huling na-publish noong {published}. Iyan po ang pinakabagong bersyon "
                         f"sa records ko, pero hindi na ito na-update mula noon.")
        else:
            lines.append(f"That list is from CvSU's official {abbr} page, last published "
                         f"{published}. It's the newest version in my records, but the page "
                         f"hasn't been updated since then.")
        if ay:
            lines.append(f"Hindi po nito sinasabi kung para ito sa {ay}." if filipino else
                         f"It doesn't say whether it covers {ay}.")
        lines.append(f"Para sa mga programang bukas ngayong semestre, kumpirmahin po sa {abbr} "
                     f"o tingnan ang https://cvsu.edu.ph." if filipino else
                     f"For this semester's offerings, confirm with {abbr} or check "
                     f"https://cvsu.edu.ph.")
        return " ".join(lines) + "\n\n" + (
            f"Pinagmulan: {college['source_url']}" if filipino else f"Source: {college['source_url']}")

    old_name = f"{old['date'][:4]} {old.get('label', 'page')}".strip() if old else ""
    old_count = old.get("program_count") if old else None
    changes = (college.get("changes") or {}).get(lang, "")

    if cited_current is False:
        lines.append("Hindi po — may mas bagong graduate catalog ang CvSU." if filipino else
                     "No — CvSU has a newer public graduate catalog.")
        if old_count:
            lines.append(f"{old_count} programa ang nasa {old_name}, habang {count} naman ang "
                         f"nasa {abbr} catalog na na-publish noong {published}." if filipino else
                         f"The {old_name} lists {old_count} programs, while the {abbr} catalog, "
                         f"published {published}, lists {count}.")
        if changes:
            lines.append(changes)
    else:
        if cited_current:
            lines.append(f"Oo po — galing iyan sa kasalukuyang {abbr} catalog ng CvSU ({count} programa, "
                         f"na-publish noong {published}), ang "
                         f"pinakabagong pinagmulan na na-verify ko." if filipino else
                         f"Yes — that's from CvSU's current {abbr} catalog ({count} programs, published "
                         f"{published}), the newest "
                         f"source I've verified.")
        else:
            lines.append(f"Ang pinakabagong pinagmulan na na-verify ko ay ang {abbr} catalog ng "
                         f"CvSU, na-publish noong {published}, na may {count} programa." if filipino else
                         f"The newest source I've verified is CvSU's {abbr} catalog, published "
                         f"{published}, which lists {count} programs.")
        if old and old_count:
            lines.append(f"Pinalitan nito ang {old_name}, na may {old_count} programa." if filipino
                         else f"It replaces the {old_name}, which listed {old_count}.")
    if ay:
        lines.append(f"Walang nakasaad na academic year sa {abbr} catalog, kaya hindi ko po "
                     f"makukumpirma mula rito kung para ito sa {ay}." if filipino else
                     f"The {abbr} catalog page doesn't say which academic year it covers, so I "
                     f"can't confirm from it that it applies to {ay}.")
    if phone:
        if ay and ay.startswith("AY"):
            when = f"para sa {ay}" if filipino else f"for {ay}"
        else:
            when = ay or ("ngayong semestre" if filipino else "this semester")
        lines.append(f"Para sa mga major at kung aling programa ang tumatanggap ng aplikante "
                     f"{when}, kumpirmahin po sa {abbr} sa {phone}." if filipino else
                     f"For majors and programs accepting applications {when}, you can confirm "
                     f"with {abbr} at {phone}.")
    if cited_current is False:
        lines.append(f"I-type lang po ang “{abbr} programs” para makita ang kasalukuyang listahan."
                     if filipino else
                     f"Ask “{abbr} programs” to see the current list.")
    return " ".join(x for x in lines if x) + "\n\n" + _sources_block(college, filipino)


def _generic_reply(prev: dict, ay: Optional[str], filipino: bool) -> str:
    urls = []
    for url in _URL_RE.findall(prev.get("bot_response") or ""):
        url = url.rstrip(".,;:")
        if url not in urls:
            urls.append(url)
    urls = urls[:3]
    ay_en = f" for {ay}" if ay else ""
    ay_fil = f" para sa {ay}" if ay else ""
    if urls:
        cited = ", ".join(urls)
        if filipino:
            return (f"Batay po ang huling sagot ko sa {cited}. Iyan ang pinakabagong pinagmulan "
                    f"na hawak ko, pero hindi ko nakikita ang mga pagbabagong ginawa pagkatapos "
                    f"nito, kaya hindi ko po makukumpirma na ito na ang pinakabago{ay_fil}. "
                    f"Para makasiguro, tingnan ang https://cvsu.edu.ph o magtanong sa opisinang "
                    f"may hawak nito.")
        return (f"My last answer is based on {cited}. That's the newest source I have for it, "
                f"but I can't see changes made after it, so I can't confirm it's the "
                f"latest{ay_en}. To be sure, check https://cvsu.edu.ph or ask the office in "
                f"charge.")
    if filipino:
        return (f"Galing po ang huling sagot ko sa curated na impormasyon ni Sevi tungkol sa "
                f"CvSU, na walang kasamang petsa ng pahina, kaya hindi ko po makukumpirma na "
                f"ito na ang pinakabago{ay_fil}. Para makasiguro, tingnan ang "
                f"https://cvsu.edu.ph o magtanong sa opisinang may hawak nito.")
    return (f"My last answer comes from Sevi's curated CvSU information, which isn't tied to "
            f"a dated page, so I can't confirm it's the latest{ay_en}. To be sure, check "
            f"https://cvsu.edu.ph or ask the office in charge.")


def which_list_reply(filipino: bool) -> str:
    if filipino:
        return ("Aling sagot o listahan po ang tinutukoy ninyo? Sabihin lang po ang paksa — "
                "halimbawa “graduate programs” o “CEIT programs” — at sasabihin ko kung gaano "
                "kabago ang pinagmulan ko.")
    return ("Which answer or list do you mean? Tell me the topic — for example “graduate "
            "programs” or “CEIT programs” — and I'll tell you how current my source is.")


def freshness_reply(ask: dict, prev: Optional[dict], filipino: bool = False) -> str:
    """The reply to a freshness question. `prev` is the previous content turn
    (a history record) or None when there is none to refer to."""
    ay = ay_text(ask, filipino)
    if ask.get("college"):
        college = ask["college"]
        cited = None
        if prev:
            cited_college, cited = _college_for_turn(prev)
            if not cited_college or cited_college.get("key") != college.get("key"):
                cited = None
        return _college_reply(college, cited, ay, filipino)
    if not prev:
        return which_list_reply(filipino)
    college, cited = _college_for_turn(prev)
    if college:
        return _college_reply(college, cited, ay, filipino)
    return _generic_reply(prev, ay, filipino)
