"""
AIS relay MCP server — LOCAL dev. Serves the SAME read tools as ais_stub_server
but backed by REAL data from the Frappe AIS (accounting.localhost) via the
secret-gated read method cvsu_web.sevi_web.api.sevi_ais_fetch.

Sevi's ais_mcp bridge is unchanged — it still talks MCP-SSE to this server on
:8765; only the data source changed (stub fake -> real Frappe). Swap back to
ais_stub_server.py to return to fake data.

  * READ-only. Auth to Frappe = the shared relay key (== sevi_jwt_secret),
    a local-dev convenience; production should use per-user OAuth.

Run:
    AIS_FRAPPE_URL=http://accounting.localhost:8002 \
    SEVI_RELAY_KEY=<sevi_jwt_secret> \
    python ais_relay_server.py --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

import httpx
import mcp.types as types
from mcp.server import Server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("ais-relay")

# Connect to a RESOLVABLE host (Python's resolver doesn't handle *.localhost),
# and set the Host header so Frappe's multi-tenant router picks the right site.
_FRAPPE = os.environ.get("AIS_FRAPPE_URL", "http://localhost:8002").rstrip("/")
_HOST = os.environ.get("AIS_FRAPPE_HOST", "accounting.localhost")
_KEY = os.environ.get("SEVI_RELAY_KEY", "")
_DV = "AIS Disbursement Voucher"
_BIR = "AIS BIR 2307"
_PENDING_EXCLUDE = ["Released", "Posted", "Closed", "Cancelled"]
_DV_FIELDS = ["name", "control_number", "payee", "amount", "workflow_status",
              "posting_date", "fund_cluster", "ors_burs_reference", "dv_type", "modified"]


async def _fetch(doctype: str, **params: Any) -> list[dict]:
    """Call the Frappe read relay, return rows."""
    q = {"doctype": doctype}
    for k, v in params.items():
        if v is None:
            continue
        q[k] = v if isinstance(v, str) else json.dumps(v)
    async with httpx.AsyncClient(timeout=12.0) as http:
        r = await http.get(
            f"{_FRAPPE}/api/method/cvsu_web.sevi_web.api.sevi_ais_fetch",
            params=q, headers={"X-Sevi-Relay-Key": _KEY, "Host": _HOST},
        )
        r.raise_for_status()
        return (r.json().get("message") or {}).get("rows") or []


# ── mappers (real Frappe fields -> the shapes ais_mcp formatters expect) ─────
def _dv_full(row: dict) -> dict:
    return {
        "name": row.get("name"), "control_number": row.get("control_number"),
        "payee_name": row.get("payee"), "gross_amount": row.get("amount"),
        "workflow_status": row.get("workflow_status"),
        "posting_date": str(row.get("posting_date")) if row.get("posting_date") else None,
        "fund_cluster": row.get("fund_cluster"),
        "ors_burs_reference": row.get("ors_burs_reference"), "dv_type": row.get("dv_type"),
        "modified": str(row.get("modified")) if row.get("modified") else None,
    }


def _dv_row(row: dict) -> dict:
    return {"name": row.get("name"), "payee_name": row.get("payee"),
            "gross_amount": row.get("amount"), "workflow_status": row.get("workflow_status")}


# ── tools ─────────────────────────────────────────────────────────────────────
async def get_dv(name: str) -> dict:
    rows = await _fetch(_DV, name=name, fields=_DV_FIELDS, limit=1)
    return _dv_full(rows[0]) if rows else {"found": False, "name": name}


async def find_dv(query: str) -> dict:
    like = f"%{query}%"
    rows = await _fetch(_DV, fields=_DV_FIELDS, limit=25, or_filters=[
        ["payee", "like", like], ["name", "like", like],
        ["control_number", "like", like], ["ors_burs_reference", "like", like],
    ])
    return {"rows": [_dv_row(r) for r in rows], "total_count": len(rows)}


async def list_pending_dvs() -> dict:
    rows = await _fetch(_DV, fields=_DV_FIELDS, limit=25,
                        filters=[["workflow_status", "not in", _PENDING_EXCLUDE]],
                        order_by="posting_date desc")
    return {"rows": [_dv_row(r) for r in rows], "total_count": len(rows)}


async def dv_totals(group_by: str = "", **filters) -> dict:
    rows = await _fetch(_DV, limit=0, fields=[
        "name", "amount", "fund_cluster", "workflow_status", "dv_type", "payee", "posting_date"])
    live = [r for r in rows if r.get("workflow_status") != "Cancelled"]
    total = round(sum(float(r.get("amount") or 0) for r in live), 2)
    count = len(live)
    if not group_by:
        return {"total_amount": total, "count": count}
    buckets: dict[str, dict] = {}
    for r in live:
        if group_by == "month":
            key = str(r.get("posting_date") or "")[:7]
        elif group_by == "fiscal_year":
            key = str(r.get("posting_date") or "")[:4]
        else:
            key = str(r.get(group_by) or "(unset)")
        b = buckets.setdefault(key, {"key": key, "amount": 0.0, "count": 0})
        b["amount"] = round(b["amount"] + float(r.get("amount") or 0), 2)
        b["count"] += 1
    ordered = sorted(buckets.values(), key=lambda x: x["amount"], reverse=True)
    return {"group_by": group_by, "total_amount": total, "count": count, "rows": ordered}


def _bir_row(row: dict) -> dict:
    tin = str(row.get("payee_tin") or row.get("tin") or "")
    return {
        "name": row.get("name"), "payee_name": row.get("payee_name") or row.get("payee"),
        "payee_tin_redacted": ("•••• " + tin[-4:]) if len(tin) >= 4 else None,
        "gross_amount": row.get("gross_amount") or row.get("amount") or 0,
        "ewt_amount": row.get("ewt_amount") or row.get("tax_amount") or 0,
        "net_amount": row.get("net_amount") or 0,
        "approval_status": row.get("approval_status") or row.get("workflow_status") or row.get("status"),
        "period_from": str(row.get("period_from") or "") or None,
        "period_to": str(row.get("period_to") or "") or None,
    }


async def list_bir_2307() -> dict:
    rows = await _fetch(_BIR, fields=["*"], limit=25)
    return {"rows": [_bir_row(r) for r in rows], "total_count": len(rows)}


async def get_bir_2307(name: str) -> dict:
    rows = await _fetch(_BIR, name=name, fields=["*"], limit=1)
    return _bir_row(rows[0]) if rows else {"found": False, "name": name}


async def find_bir_2307(query: str = "", **filters) -> dict:
    like = f"%{query}%"
    rows = await _fetch(_BIR, fields=["*"], limit=25, or_filters=[
        ["payee_name", "like", like], ["name", "like", like]]) if query else await _fetch(_BIR, fields=["*"], limit=25)
    return {"rows": [_bir_row(r) for r in rows], "total_count": len(rows)}


async def _not_mapped(_tool: str) -> dict:
    return {"found": False, "note": "reading from real AIS is wired for DVs and BIR 2307; "
            "budget/UACS/reports still need mapping to the live DocTypes."}


async def budget_balance(kind: str = "", name: str = "") -> dict:
    return await _not_mapped("budget_balance")


async def lookup_uacs(kind: str = "", query: str = "") -> dict:
    return {"matches": []}


async def run_report(report_name: str = "") -> dict:
    return {"report_name": report_name, "columns": [], "rows": []}


_HANDLERS = {
    "get_dv": get_dv, "list_pending_dvs": list_pending_dvs, "find_dv": find_dv,
    "dv_totals": dv_totals, "budget_balance": budget_balance, "lookup_uacs": lookup_uacs,
    "run_report": run_report, "list_bir_2307": list_bir_2307, "get_bir_2307": get_bir_2307,
    "find_bir_2307": find_bir_2307,
}
_SCHEMAS = {
    "get_dv": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    "list_pending_dvs": {"type": "object", "properties": {}},
    "find_dv": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "budget_balance": {"type": "object", "properties": {"kind": {"type": "string"}, "name": {"type": "string"}}},
    "lookup_uacs": {"type": "object", "properties": {"kind": {"type": "string"}, "query": {"type": "string"}}},
    "dv_totals": {"type": "object", "properties": {"group_by": {"type": "string"}}},
    "run_report": {"type": "object", "properties": {"report_name": {"type": "string"}}},
    "list_bir_2307": {"type": "object", "properties": {}},
    "get_bir_2307": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    "find_bir_2307": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def build_server() -> Server:
    server: Server = Server("cvsu-ais-relay")

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [types.Tool(name=n, description=f"[REAL] {n}", inputSchema=_SCHEMAS[n]) for n in _HANDLERS]

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        fn = _HANDLERS.get(name)
        try:
            payload = await fn(**(arguments or {})) if fn else {"ok": False, "error": f"unknown tool {name}"}
        except Exception as exc:  # noqa: BLE001
            _log.exception("tool %s failed", name)
            payload = {"ok": False, "error": f"relay failure: {exc.__class__.__name__}"}
        _log.info("tool=%s args=%s", name, arguments)
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))]

    return server


def main() -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not _KEY:
        _log.warning("SEVI_RELAY_KEY is empty — Frappe will reject every read (403).")

    server = build_server()
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await server.run(r, w, server.create_initialization_options())
        return Response()

    async def health(_r):
        return JSONResponse({"service": "cvsu-ais-relay", "data": "REAL", "frappe": _FRAPPE, "tools": list(_HANDLERS)})

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/health", endpoint=health),
    ])
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
