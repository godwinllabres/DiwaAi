"""DTS (Document Tracking System) tool group — public document tracking.

Wraps the guest, rate-limited (20/min) Frappe whitelisted method
`dts.api.search_document_movement` from the cvsu-dts repo. The upstream
return is a heavy nested payload (full movement trail across six document
types); this tool condenses it to what a chat answer needs: current status,
document type, and the recent movement history.

Upstream sanitizes the reference to `^[A-Za-z0-9-]+$` (max 64) and returns
`{}` for not-found/invalid — mirrored client-side so bad input never burns a
rate-limited call.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import Config
from ..http import get_json
from ..registry import ToolDef, ToolGroup

_METHOD = "dts.api.search_document_movement"
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_MAX_MOVEMENTS = 8


def _condense(reference_number: str, payload: dict[str, Any]) -> dict[str, Any]:
    movements = payload.get("movements") or []
    recent = [
        {
            "status": m.get("card_header_text") or m.get("transaction_type", ""),
            "when": m.get("transaction_date"),
            "remarks": m.get("remarks"),
        }
        for m in movements[:_MAX_MOVEMENTS]
        if isinstance(m, dict)
    ]
    route = payload.get("route") or []
    return {
        "found": True,
        "reference_number": reference_number,
        "document_type": payload.get("document_type"),
        "current_status": recent[0]["status"] if recent else None,
        "last_update": recent[0]["when"] if recent else None,
        "movements": recent,
        "total_movements": len(movements),
        "route": route[:10] if isinstance(route, list) else route,
    }


def build_group(cfg: Config) -> ToolGroup:
    base = cfg.dts.base_url
    timeout = cfg.timeout_seconds

    async def track_document(reference_number: str) -> dict[str, Any]:
        reference_number = (reference_number or "").strip()
        if not _REFERENCE_RE.match(reference_number):
            return {
                "ok": True,
                "data": {
                    "found": False,
                    "reference_number": reference_number,
                    "note": "invalid reference format (letters, digits, and dashes only)",
                },
            }
        payload = await get_json(
            f"{base}/api/method/{_METHOD}",
            params={"reference_number": reference_number},
            timeout=timeout,
        )
        if not payload.get("ok"):
            return payload
        message = payload["data"].get("message") if isinstance(payload["data"], dict) else None
        if not message:
            return {"ok": True, "data": {"found": False, "reference_number": reference_number}}
        return {"ok": True, "data": _condense(reference_number, message)}

    tools = (
        ToolDef(
            name="dts_track_document",
            description=(
                "Track a document in the CvSU Document Tracking System by its reference "
                "number (communication, purchase request/order, job order, disbursement "
                "voucher, or financial document). Public lookup; returns current status "
                "and the movement history."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reference_number": {
                        "type": "string",
                        "description": "The document reference number (letters, digits, dashes)",
                    }
                },
                "required": ["reference_number"],
            },
            handler=track_document,
        ),
    )
    return ToolGroup(name="dts", tools=tools)
