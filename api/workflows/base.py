"""Workflow engine — Tier 5.5 (POC, see docs/agentic_workflows_poc.md).

The whole tier is a kill-switch away: with AGENTIC_WORKFLOWS_ENABLED unset (the
default), `dispatch` returns None for everything and the pipeline behaves
exactly as before. Turn it on to let a session run a multi-step, tool-executing
workflow.

Extensibility: a workflow subclasses `Workflow`, sets `name` + `START_RE`, and
implements `start()` / `advance()`. Register it in workflows/__init__.py. The
dispatcher handles state, cancellation, TTL, and per-turn logging uniformly, so
a new workflow (drop_subject, request_document, …) is ~30 lines and no pipeline
change.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .state_manager import WorkflowState, clear_state, get_state, set_state

ENABLED = os.environ.get("AGENTIC_WORKFLOWS_ENABLED", "0") == "1"

# A universal escape hatch — an active workflow must never trap the user.
_CANCEL_RE = re.compile(
    r"\b(cancel|nevermind|never mind|stop|forget it|quit|exit|wag na|ayoko na|"
    r"hindi na|huwag na)\b",
    re.IGNORECASE,
)


@dataclass
class Turn:
    """One reply from a workflow. `done` tells the dispatcher to clear state."""
    text: str
    suggestions: List[str] = field(default_factory=list)
    done: bool = False


class Workflow:
    """Base class. A concrete workflow sets these two and implements the two
    methods below. `advance` is called with the live state for steps >= 1."""
    name: str = ""
    START_RE: re.Pattern = re.compile(r"$^")  # matches nothing by default

    def start(self) -> Turn:
        """Reply that opens the workflow (state is created at step 1)."""
        raise NotImplementedError

    async def advance(self, state: WorkflowState, message: str) -> Turn:
        """Handle a follow-up message. Mutate `state` (step/collected); set
        `Turn.done=True` on the terminal turn so state is cleared."""
        raise NotImplementedError


# Populated by workflows/__init__.py to avoid import cycles.
REGISTRY: Dict[str, Workflow] = {}


def register(workflow: Workflow) -> None:
    REGISTRY[workflow.name] = workflow


async def dispatch(key: Optional[str], message: str) -> Optional[Turn]:
    """Route one turn. Returns a Turn when a workflow handled it, else None
    (pipeline continues to the normal tiers). No-op when the tier is disabled
    or there is no conversation key to anchor state to."""
    if not ENABLED or not key or not message:
        return None

    state = get_state(key)

    # An active workflow owns the turn — but 'cancel' always releases it.
    if state is not None:
        if _CANCEL_RE.search(message):
            clear_state(key)
            return Turn(
                "No problem — I've cancelled that. Ask me anything about CvSU po.",
                done=True,
            )
        workflow = REGISTRY.get(state.workflow_name)
        if workflow is None:  # registry changed under a live state — fail safe
            clear_state(key)
            return None
        turn = await workflow.advance(state, message)
        if turn.done:
            clear_state(key)
        else:
            set_state(key, state)
        return turn

    # No active workflow — a trigger phrase starts one. 'cancel' with nothing
    # active is not a workflow turn, so let the normal tiers answer it.
    if _CANCEL_RE.search(message):
        return None
    for workflow in REGISTRY.values():
        if workflow.START_RE.search(message):
            set_state(key, WorkflowState(workflow_name=workflow.name, step=1))
            return workflow.start()
    return None
