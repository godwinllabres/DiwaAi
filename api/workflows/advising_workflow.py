"""Advising-appointment booking — the POC workflow (2 steps + a tool call).

Flow:
  start   → "what's your 9-digit student number?"      (state.step = 1)
  step 1  → extract a 9-digit id, else re-ask          (→ step 2)
  step 2  → take the date, call the (mock) tool, finish (state cleared)

See docs/agentic_workflows_poc.md. The student number is trusted here ONLY
because the tool is a mock — a real deployment authenticates the student and
derives the id from the session (see api/workflows/tools.py security note).
"""
from __future__ import annotations

import re

from .base import Turn, Workflow
from .state_manager import WorkflowState
from .tools import book_advising_appointment

_STUDENT_ID_RE = re.compile(r"\b(\d{9})\b")
_CANCEL_HINT = ["Cancel"]


class AdvisingWorkflow(Workflow):
    name = "book_advising"
    # Start on a booking-shaped ask. Kept specific so it doesn't hijack general
    # "advising hours?" questions — those should still hit the FAQ tier.
    START_RE = re.compile(
        r"\b(book|schedule|set\s*up|make|reserve|magpa[- ]?schedule|magbook)\b"
        r"[\w\s]{0,24}?"
        r"\b(advising|adviser|advisor|consultation)\b"
        r"|\badvising\s+(appointment|slot|session|schedule)\b",
        re.IGNORECASE,
    )

    def start(self) -> Turn:
        return Turn(
            "Sure — I can help you book an advising appointment. "
            "What's your **9-digit student number**?",
            suggestions=_CANCEL_HINT,
        )

    async def advance(self, state: WorkflowState, message: str) -> Turn:
        if state.step == 1:
            match = _STUDENT_ID_RE.search(message)
            if not match:
                return Turn(
                    "I didn't catch a valid student number. Please type your "
                    "**9-digit** student number (e.g. 202012345).",
                    suggestions=_CANCEL_HINT,
                )
            state.collected["student_id"] = match.group(1)
            state.step = 2
            return Turn(
                "Thanks. What date would you like? (e.g. “October 12” or “next Monday”)",
                suggestions=["Cancel"],
            )

        if state.step == 2:
            date = message.strip()[:60]
            if not date:
                return Turn(
                    "Which date should I request? (e.g. “October 12”)",
                    suggestions=_CANCEL_HINT,
                )
            state.collected["date"] = date
            result = await book_advising_appointment(
                student_id=state.collected.get("student_id", ""),
                date=date,
            )
            if result.get("ok"):
                return Turn(
                    f"Done — I've logged an advising request for **{date}** "
                    f"(ref `{result['confirmation']}`). You'll get an email "
                    f"confirmation.\n\n_Heads-up: this is a proof-of-concept — "
                    f"no real booking was made yet._",
                    done=True,
                )
            return Turn(
                "Sorry, I couldn't reach the scheduling system right now. "
                "Please try again in a few minutes.",
                done=True,
            )

        # Defensive: unknown step — reset cleanly.
        return Turn("Let's start over — say “book advising” to begin.", done=True)
