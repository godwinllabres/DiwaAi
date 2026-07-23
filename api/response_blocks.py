"""Structured content blocks for a `/chat` reply.

The reply body used to travel as one markdown-ish string that every consumer
had to re-parse — and any server-side "prettifier" that touched it could (and
did) shred an already-structured answer into `"1.\\n\\n"` fragments. These DTOs
make the structure explicit and authoritative: the server decides what is a
heading, a numbered step, a nested bullet, or a footnote, and the renderer just
draws it.

`ChatResponse.text` is still emitted verbatim for older clients; `blocks` is the
same content, typed. The block kinds mirror the web renderer's own parser
(SeviWeb `MessageBody.parseBlocks`) so the two can never disagree about what a
reply looks like.
"""

from __future__ import annotations

import re
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class HeadingBlock(BaseModel):
    """A short section title — the corpus writes these in ALL CAPS, often with
    a trailing colon. `text` is stored without the colon."""
    kind: Literal["heading"] = "heading"
    text: str


class ParagraphBlock(BaseModel):
    """Ordinary prose. May contain inline markup (URLs, `code`, **bold**)."""
    kind: Literal["paragraph"] = "paragraph"
    text: str


class ListItem(BaseModel):
    """One numbered step, plus any bullets nested directly beneath it."""
    text: str
    subs: List[str] = Field(default_factory=list)


class OrderedListBlock(BaseModel):
    """Numbered steps. `start` is the first marker seen, so a list that begins
    at 3 renders as 3, 4, 5 rather than being silently renumbered."""
    kind: Literal["ordered_list"] = "ordered_list"
    start: int = 1
    items: List[ListItem] = Field(default_factory=list)


class BulletListBlock(BaseModel):
    kind: Literal["bullet_list"] = "bullet_list"
    items: List[str] = Field(default_factory=list)


class NoteBlock(BaseModel):
    """A trailing aside the UI should de-emphasise — currently the appended
    provenance line ("📖 Source: CvSU Citizens' Charter …"). The same
    provenance is available structurally in `ChatResponse.sources`; this block
    exists so a renderer working purely from `blocks` still shows it, and can
    style it as a footnote instead of as body prose."""
    kind: Literal["note"] = "note"
    text: str
    icon: Optional[str] = None


ContentBlock = Annotated[
    Union[HeadingBlock, ParagraphBlock, OrderedListBlock, BulletListBlock, NoteBlock],
    Field(discriminator="kind"),
]


# ── parsing ───────────────────────────────────────────────────────────────────
# Line shapes. Ordered markers accept "1." and "1)"; bullets accept -, * and •.
_OL_LINE_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_UL_LINE_RE = re.compile(r"^\s*[-*•]\s+(.*)$")
# A heading is a short line with no lowercase letters: "ADMISSION TO CvSU" is a
# heading, "CvSU offers …" is not. Digits, punctuation and the acronym-friendly
# mixed case in the corpus ("CvSU") are tolerated via the explicit char class.
_HEADING_RE = re.compile(r"^[A-Z0-9][A-Z0-9 ,()/&'’.:–—-]{2,}$")
_NOTE_PREFIX_RE = re.compile(r"^(📖|ℹ️|⚠️|📌)\s*")

_HEADING_MAX_CHARS = 80


def _is_heading(text: str) -> bool:
    if "\n" in text or len(text) >= _HEADING_MAX_CHARS:
        return False
    return bool(_HEADING_RE.match(text.rstrip(":")))


def _paragraph_block(text: str) -> ContentBlock:
    note = _NOTE_PREFIX_RE.match(text)
    if note:
        return NoteBlock(text=text[note.end():].strip(), icon=note.group(1))
    if _is_heading(text):
        return HeadingBlock(text=text.rstrip(":").strip())
    return ParagraphBlock(text=text)


def parse_blocks(text: str) -> List[ContentBlock]:
    """Split a reply body into typed blocks.

    Purely structural — inline markup, casing and wording are left untouched,
    so `"\\n".join` of the rendered blocks round-trips back to the input.
    """
    if not text or not text.strip():
        return []

    blocks: List[ContentBlock] = []
    para: List[str] = []
    open_list: Optional[Union[OrderedListBlock, BulletListBlock]] = None

    def flush_para() -> None:
        nonlocal para
        joined = "\n".join(para).strip()
        para = []
        if joined:
            blocks.append(_paragraph_block(joined))

    def flush_list() -> None:
        nonlocal open_list
        if open_list is not None:
            blocks.append(open_list)
            open_list = None

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            flush_para()
            flush_list()
            continue

        ol = _OL_LINE_RE.match(line)
        ul = _UL_LINE_RE.match(line) if not ol else None

        if ol:
            flush_para()
            if not isinstance(open_list, OrderedListBlock):
                flush_list()
                open_list = OrderedListBlock(start=int(ol.group(1)))
            open_list.items.append(ListItem(text=ol.group(2).strip()))
        elif ul:
            flush_para()
            if isinstance(open_list, OrderedListBlock) and open_list.items:
                # A bullet indented under a numbered step belongs to that step,
                # not to a new flush-left list.
                open_list.items[-1].subs.append(ul.group(1).strip())
            else:
                if not isinstance(open_list, BulletListBlock):
                    flush_list()
                    open_list = BulletListBlock()
                open_list.items.append(ul.group(1).strip())
        else:
            flush_list()
            para.append(line)

    flush_para()
    flush_list()
    return blocks
