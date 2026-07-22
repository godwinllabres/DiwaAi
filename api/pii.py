"""Best-effort PII masking for the audit log (docs/privacy_compliance.md §2).

Students volunteer sensitive personal information into chat — student numbers,
emails, phone numbers — and today it lands verbatim in logs/chat_history.db,
which has no retention schedule yet. This module masks the obvious, regex-able
identifiers BEFORE a message is written to the log and to the moderation feed,
so the stored copy is minimized by design.

Scope + limits (deliberate):
  • Runs only on the LOGGED copy. The live message the brain answers is never
    touched, so ticket / document / subject lookups keep working exactly as
    before — masking happens strictly at the logging boundary.
  • Catches emails, PH phone numbers, and long bare digit runs (student / ID
    numbers). It does NOT catch free-text disclosures ("I have depression") —
    those cannot be pattern-matched and remain a known residual risk that the
    retention window and access controls (not this module) mitigate.
  • Over-masking is acceptable (it's the log copy); under-masking is the real
    risk, so the patterns lean permissive.

Reference numbers that carry letters or dashes — HTKT-07-00001, PR-2026-0042,
COSC 101 — are preserved: the digit-run pattern requires a clean boundary on
both sides, so a run glued to a letter or dash is never masked.
"""
from __future__ import annotations

import os
import re

# Order matters: emails first (their local part can contain digits), then
# phones, then any remaining long digit run.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# PH phone shapes. Each alternative carries a phone-specific signal (a mobile
# trunk, parentheses, +63, or 2-3 separator-joined groups) so a bare digit run
# — e.g. a 9-digit student number — is NOT swallowed here; it falls through to
# the id-run pass and is labelled correctly. The 3-group form comes first so it
# wins over the 2-group form on the common 0XX-XXX-XXXX / 0XX XXX XXXX landline
# (an earlier version leaked the area code of these). Boundaries reject runs
# glued to letters or dashes (ticket/doc refs). Every alternative matches >= 7
# digits by construction, so a match is always phone-length.
_PHONE_RE = re.compile(
    r"(?<![\w-])"
    r"(?:"
    r"(?:\+?63[\s.-]?|0)9\d{2}[\s.-]?\d{3}[\s.-]?\d{4}"    # 09xx / +639xx mobile
    r"|\(\d{2,4}\)[\s.-]?\d{3,4}[\s.-]?\d{3,4}"            # (area) xxx-xxxx
    r"|\+?63[\s.-]?\d{1,2}[\s.-]?\d{3,4}[\s.-]?\d{3,4}"    # +63 area xxx-xxxx
    r"|\d{2,4}[\s.-]\d{3,4}[\s.-]\d{4}"                    # xxx-xxx-xxxx (3 groups)
    r"|\d{3,4}[\s.-]\d{4}"                                 # local xxx-xxxx (needs a sep)
    r")"
    r"(?![\w-])"
)

# Bare digit runs of 6+ (student numbers, reference ids typed alone). 5-digit
# and shorter runs (short ticket forms like #12345, years) are left intact.
_IDNUM_RE = re.compile(r"(?<![\w-])\d{6,}(?![\w-])")

_ENABLED = os.environ.get("LOG_MASK_PII", "1") == "1"


def mask_pii(text: str) -> str:
    """Return `text` with emails, phone numbers, and long id/number runs masked.

    Idempotent-ish: the replacement tokens contain no maskable content, so
    re-running is a no-op. Honors LOG_MASK_PII (default on); set it to 0 to
    store raw messages (not recommended outside debugging)."""
    if not _ENABLED or not text:
        return text
    out = _EMAIL_RE.sub("[email]", text)
    out = _PHONE_RE.sub("[phone]", out)
    out = _IDNUM_RE.sub("[id]", out)
    return out


def enabled() -> bool:
    return _ENABLED
