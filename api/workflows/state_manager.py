"""In-memory, per-conversation workflow state (POC — see docs/agentic_workflows_poc.md).

A stateful workflow (e.g. booking an advising appointment) needs to remember
where a user is across turns. This is a lightweight store keyed by the
conversation key (session_id, falling back to user_id — the same keying the
campus context and AIS follow-up context use).

POC scope + limits (deliberate):
  • In-memory + single-worker, like the /chat rate limiter and campus context.
    A multi-worker deploy must move this to Redis/Postgres — an active booking
    would otherwise be invisible to the worker that fields the next turn.
  • Entries expire after _TTL_SECONDS so an abandoned half-finished workflow
    doesn't trap the user forever; the next message starts fresh.
  • collected_data can hold volunteered PII (a student number) for the life of
    the transaction. It lives only here, in memory, and is cleared the moment
    the workflow finishes or the user cancels. The chat LOG of those turns is
    PII-masked by api/pii.py, so the student number never lands in the DB.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# 10-minute transaction window — matches the AIS follow-up context TTL.
_TTL_SECONDS = float(600)
_PRUNE_CAP = 2000


@dataclass
class WorkflowState:
    workflow_name: str
    step: int = 0
    collected: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)


_active: Dict[str, WorkflowState] = {}


def _prune(now: float) -> None:
    for key in [k for k, s in _active.items() if now - s.updated_at > _TTL_SECONDS]:
        _active.pop(key, None)


def get_state(key: Optional[str]) -> Optional[WorkflowState]:
    """Return the live workflow for this conversation, or None if there is
    none or it has expired (expired entries are dropped on read)."""
    if not key:
        return None
    state = _active.get(key)
    if state is None:
        return None
    if time.time() - state.updated_at > _TTL_SECONDS:
        _active.pop(key, None)
        return None
    return state


def set_state(key: Optional[str], state: WorkflowState) -> None:
    if not key:
        return
    now = time.time()
    if len(_active) > _PRUNE_CAP:
        _prune(now)
    state.updated_at = now
    _active[key] = state


def clear_state(key: Optional[str]) -> None:
    if key:
        _active.pop(key, None)


def active_count() -> int:
    """Live (non-expired) workflow count — for /admin observability."""
    now = time.time()
    return sum(1 for s in _active.values() if now - s.updated_at <= _TTL_SECONDS)
