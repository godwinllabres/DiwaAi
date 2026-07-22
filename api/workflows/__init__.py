"""Agentic workflow tier (Tier 5.5) — stateful, tool-executing conversations.

Public surface used by the pipeline (api/app.py):
    dispatch(key, message) -> Optional[Turn]   # route one turn
    ENABLED                                     # kill-switch (env)
    active_count()                              # /admin observability

Add a workflow: subclass Workflow (see base.py), then register() it below.
"""
from __future__ import annotations

from .advising_workflow import AdvisingWorkflow
from .base import ENABLED, REGISTRY, Turn, Workflow, dispatch, register
from .state_manager import active_count

# Register the POC workflow(s). Add new ones here — e.g. DropSubjectWorkflow.
register(AdvisingWorkflow())

__all__ = [
    "dispatch",
    "ENABLED",
    "REGISTRY",
    "Turn",
    "Workflow",
    "register",
    "active_count",
]
