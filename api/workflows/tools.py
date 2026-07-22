"""Workflow tools — the 'agent' actuators (POC, all MOCKS).

A tool is where Sevi would touch the outside world (the SIS / student portal).
Everything here is a MOCK: it logs and returns a fake result, it never writes.

⚠️  SECURITY — READ BEFORE MAKING ANY TOOL REAL  ⚠️
The advising POC collects a student number that the user *types*. A real
booking tool MUST NOT trust a typed id as identity — that lets anyone act as
any student (impersonation / IDOR). Before a tool performs a real write it has
to, at minimum:
  1. Authenticate the student (reuse the per-user OAuth pattern in
     api/auth_ais.py — the write then runs as the student's own identity, not a
     shared bot identity) and DERIVE the id from the session, not the message.
  2. Confirm the action with the user before executing ("Book advising for
     {name} on {date}? yes/no").
  3. Sit behind a kill-switch (AGENTIC_WORKFLOWS_ENABLED) and log every call.
  4. Have DPO sign-off: collecting / processing a student number in chat is new
     PII processing the current consent notice does not cover.
The `authenticated_student_id` parameter below is the seam for (1): when a
deployment wires real auth, pass the authenticated id and the workflow stops
asking for — and trusting — a typed one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_logger = logging.getLogger("diwa.workflows.tools")


async def book_advising_appointment(
    student_id: str,
    date: str,
    authenticated_student_id: Optional[str] = None,
) -> Dict[str, Any]:
    """MOCK: pretend to book an advising slot. Returns a fake confirmation.

    In production this becomes an authenticated POST to the SIS/portal, e.g.:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.post(
                f"{SIS_BASE}/api/advising",
                json={"student_id": authenticated_student_id, "date": date},
                headers={"Authorization": f"Bearer {session_token}"},
            )
            return {"ok": res.status_code == 201, ...}
    Note it would send `authenticated_student_id`, NOT the typed `student_id`."""
    effective_id = authenticated_student_id or student_id
    trusted = authenticated_student_id is not None
    _logger.warning(
        "[MOCK TOOL] book_advising id=%s (trusted=%s) date=%r — NO real write",
        effective_id, trusted, date,
    )
    # Deterministic fake reference (no randomness needed for a mock).
    ref = f"ADV-{effective_id[-4:] if effective_id else '0000'}-{abs(hash(date)) % 10000:04d}"
    return {"ok": True, "confirmation": ref, "mock": True, "authenticated": trusted}
