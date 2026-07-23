"""Tests for the structured reply DTO (api/response_blocks.py) and for the
display formatter that must never shred an already-structured answer."""

import json

import pytest

from api.response_blocks import (
    BulletListBlock,
    HeadingBlock,
    NoteBlock,
    OrderedListBlock,
    ParagraphBlock,
    parse_blocks,
)


ADMISSIONS = (
    "ADMISSION TO CvSU (per the official Office of Student Affairs and Services "
    "and the admission portal):\n"
    "\n"
    "1. FILE THE ONLINE APPLICATION at https://admission.cvsu.edu.ph/ — encode "
    "your information in the registration/application form.\n"
    "2. PRINT the accomplished application form on A4-size bond paper.\n"
    "3. PREPARE THESE DOCUMENTS (typical for SHS-graduate applicants):\n"
    "   - Certified true copy of Grade 12 Report Card (Form 138)\n"
    "   - Certificate of Good Moral Character\n"
    "4. SUBMIT TO OSAS at your target campus for exam scheduling.\n"
    "\n"
    "For the current cycle's exact requirements, see https://cvsu.edu.ph.\n"
    "\n"
    "\U0001F4D6 Source: CvSU Citizens' Charter, FY 2026 edition, p. 1694"
)


def test_parses_the_admissions_reply_into_typed_blocks():
    blocks = parse_blocks(ADMISSIONS)
    kinds = [b.kind for b in blocks]
    assert kinds == ["paragraph", "ordered_list", "paragraph", "note"]

    steps = blocks[1]
    assert isinstance(steps, OrderedListBlock)
    assert steps.start == 1
    assert len(steps.items) == 4
    # The step marker never leaks into the item text.
    assert steps.items[0].text.startswith("FILE THE ONLINE APPLICATION")
    # Bullets indented under step 3 nest inside it rather than detaching.
    assert steps.items[2].subs == [
        "Certified true copy of Grade 12 Report Card (Form 138)",
        "Certificate of Good Moral Character",
    ]
    assert all(not s.subs for i, s in enumerate(steps.items) if i != 2)

    note = blocks[3]
    assert isinstance(note, NoteBlock)
    assert note.icon == "\U0001F4D6"
    assert note.text.startswith("Source: CvSU Citizens' Charter")


def test_recognises_a_short_all_caps_line_as_a_heading():
    blocks = parse_blocks("GENERAL ELIGIBILITY:\n\nAnyone may apply.")
    assert isinstance(blocks[0], HeadingBlock)
    assert blocks[0].text == "GENERAL ELIGIBILITY"  # colon stripped
    assert isinstance(blocks[1], ParagraphBlock)


def test_a_sentence_is_never_mistaken_for_a_heading():
    blocks = parse_blocks("CvSU offers many programs.")
    assert isinstance(blocks[0], ParagraphBlock)


def test_standalone_bullets_stay_a_flat_list():
    blocks = parse_blocks("Requirements:\n- Form 138\n- Good moral")
    bullets = [b for b in blocks if isinstance(b, BulletListBlock)]
    assert bullets and bullets[0].items == ["Form 138", "Good moral"]


def test_a_list_starting_past_one_keeps_its_numbering():
    blocks = parse_blocks("3. Third step\n4. Fourth step")
    assert isinstance(blocks[0], OrderedListBlock)
    assert blocks[0].start == 3


def test_empty_text_yields_no_blocks():
    assert parse_blocks("") == []
    assert parse_blocks("   \n\n  ") == []


# ── the regression these blocks exist to make impossible ─────────────────────


def test_display_formatter_leaves_an_authored_numbered_list_intact():
    """`_format_display_text` used to split on the "1. " marker itself, leaving
    the number stranded on its own line ("1.\\n\\nFILE THE ONLINE …")."""
    from api.app import _format_display_text

    assert _format_display_text(ADMISSIONS) == ADMISSIONS


def test_display_formatter_still_bulletizes_a_dense_paragraph():
    from api.app import _format_display_text

    dense = (
        "The University Registrar handles these transactions: issuance of the "
        "Transcript of Records for graduating students, issuance of the "
        "Certificate of Enrollment for scholarship applicants, evaluation of "
        "credentials for transferees from other institutions, and processing of "
        "the diploma request for alumni of the Indang campus."
    )
    out = _format_display_text(dense)
    assert out.count("\n- ") >= 3


def test_chat_response_derives_blocks_from_text():
    from api.app import ChatResponse, ResponseSource

    r = ChatResponse(
        text=ADMISSIONS, intent="admissions_requirements",
        source=ResponseSource.NEURAL_NETWORK,
    )
    assert [b.kind for b in r.blocks] == ["paragraph", "ordered_list", "paragraph", "note"]
    # And the DTO round-trips through JSON with its discriminators intact.
    payload = json.loads(r.model_dump_json())
    assert payload["blocks"][1]["kind"] == "ordered_list"
    assert payload["blocks"][1]["items"][2]["subs"]


def test_explicit_blocks_are_not_overwritten():
    from api.app import ChatResponse, ResponseSource

    r = ChatResponse(
        text="anything", intent="x", source=ResponseSource.FALLBACK,
        blocks=[ParagraphBlock(text="hand-built")],
    )
    assert len(r.blocks) == 1 and r.blocks[0].text == "hand-built"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
