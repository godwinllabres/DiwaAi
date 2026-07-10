"""ORPS (ICT Helpdesk) tool group — public ticket tracking.

Wraps the guest, rate-limited (20/min) Frappe whitelisted method
`ticketing_platform.ticketing_platform.api.get_ticket_tracking` from the
cvsu-orps repo. Frappe wraps the return value in {"message": ...}.
"""
from __future__ import annotations

from typing import Any

from ..config import Config
from ..http import get_json
from ..registry import ToolDef, ToolGroup

_METHOD = "ticketing_platform.ticketing_platform.api.get_ticket_tracking"


def build_group(cfg: Config) -> ToolGroup:
    base = cfg.orps.base_url
    timeout = cfg.timeout_seconds

    async def track_ticket(ticket_number: str) -> dict[str, Any]:
        payload = await get_json(
            f"{base}/api/method/{_METHOD}",
            params={"ticket_number": ticket_number},
            timeout=timeout,
        )
        if not payload.get("ok"):
            return payload
        message = payload["data"].get("message") if isinstance(payload["data"], dict) else None
        if not message:
            return {"ok": True, "data": {"found": False, "ticket_number": ticket_number}}
        return {"ok": True, "data": {"found": True, **message}}

    tools = (
        ToolDef(
            name="orps_track_ticket",
            description="Track an ICT Helpdesk (Online Request) ticket by its ticket number. Public lookup; returns current status and its history.",
            input_schema={
                "type": "object",
                "properties": {"ticket_number": {"type": "string", "description": "The ticket number, e.g. as printed on the request receipt"}},
                "required": ["ticket_number"],
            },
            handler=track_ticket,
        ),
    )
    return ToolGroup(name="orps", tools=tools)
