"""
Stub cvsu-ais MCP server — DEV/DEMO ONLY, serves FAKE data.

Stands in for the real (external) CvSU AIS MCP server so the whole Sevi -> AIS
plumbing can be exercised end-to-end locally: it implements the READ tools the
bridge (api/ais_mcp.py) advertises, returning shapes that api/ais_mcp.py's
formatters (_format_reply / _build_dv_card / _build_table) render as cards and
tables. Swap this for the real AIS server later by pointing AIS_MCP_URL at it —
no code change in Sevi.

  * FAKE data — never wire this to production.
  * READ-only — no write tools (approve/post/create). Writes go through
    /ais/write against the real server, not here.
  * No auth on the wire — bind to loopback / host-only; see the note in main().

Run:
    pip install "mcp[sse]" starlette uvicorn
    python ais_stub_server.py --host 0.0.0.0 --port 8765
Then set (sevi.env):  AIS_MCP_URL=http://host.docker.internal:8765/sse

Endpoints: GET /sse (MCP), POST /messages/, GET /health.
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("ais-stub")

# ── fake data ─────────────────────────────────────────────────────────────────
_DVS: list[dict[str, Any]] = [
    {"name": "DV-2026-00042", "control_number": "2026-06-0042", "payee_name": "Manila Electric Company (MERALCO)",
     "gross_amount": 45320.50, "workflow_status": "Approved", "posting_date": "2026-06-28",
     "fund_cluster": "01 Regular Agency Fund", "ors_burs_reference": "ORS-2026-0123", "dv_type": "Regular",
     "modified": "2026-06-28 14:05:11"},
    {"name": "DV-2026-00043", "control_number": "2026-06-0043", "payee_name": "PLDT Inc.",
     "gross_amount": 12800.00, "workflow_status": "Submitted", "posting_date": "2026-06-29",
     "fund_cluster": "01 Regular Agency Fund", "ors_burs_reference": "ORS-2026-0131", "dv_type": "Regular",
     "modified": "2026-06-29 09:20:00"},
    {"name": "DV-2026-00051", "control_number": "2026-07-0051", "payee_name": "Grainger Supplies Trading",
     "gross_amount": 88000.00, "workflow_status": "IA Audit Required", "posting_date": "2026-07-03",
     "fund_cluster": "01 Regular Agency Fund", "ors_burs_reference": "ORS-2026-0140", "dv_type": "Regular",
     "modified": "2026-07-03 16:44:02"},
    {"name": "TRUST-2026-00007", "control_number": "2026-07-0007", "payee_name": "EduBooks Publishing House",
     "gross_amount": 250000.00, "workflow_status": "Posted", "posting_date": "2026-07-01",
     "fund_cluster": "07 Trust Fund (STF)", "ors_burs_reference": "BURS-2026-0009", "dv_type": "Trust",
     "modified": "2026-07-01 11:00:00"},
    {"name": "DV-2026-00055", "control_number": "2026-07-0055", "payee_name": "Juan D. Dela Cruz (reimbursement)",
     "gross_amount": 3450.00, "workflow_status": "Submitted", "posting_date": "2026-07-10",
     "fund_cluster": "01 Regular Agency Fund", "ors_burs_reference": "ORS-2026-0155", "dv_type": "Regular",
     "modified": "2026-07-10 08:15:00"},
]

_PENDING = ("Submitted", "IA Audit Required")  # what "waiting on me" surfaces

_UACS = {
    "funding_source": [
        {"code": "01 1010101", "description": "Specific Budgets of National Government Agencies — Regular Agency Fund"},
        {"code": "07 4020101", "description": "Special Trust Fund (STF) — Internally Generated Income"},
    ],
    "pap": [
        {"code": "310100100001000", "description": "Higher Education Program — Instruction/Delivery of Higher Education"},
        {"code": "310200100001000", "description": "Advanced Education Program — Graduate Programs"},
        {"code": "200000100001000", "description": "General Administration and Support — General Management and Supervision"},
    ],
    "location": [
        {"code": "042100000", "description": "Region IV-A (CALABARZON) — Cavite — Indang"},
    ],
}

_OBJECT_CODES = [  # for lookup_uacs when kind not matched to the above buckets
    {"code": "5020402000", "description": "Electricity Expenses (MOOE)"},
    {"code": "5020502000", "description": "Telephone Expenses (MOOE)"},
    {"code": "5020310000", "description": "Office Supplies Expenses (MOOE)"},
]

_BALANCES = {  # keyed loosely by lowercased name substring
    "mooe": {"allotment": 5_000_000.00, "obligated": 4_130_000.00, "disbursed": 2_960_000.00},
    "stf":  {"allotment": 3_200_000.00, "obligated": 1_050_000.00, "disbursed": 720_000.00},
    "ps":   {"allotment": 42_000_000.00, "obligated": 38_500_000.00, "disbursed": 37_900_000.00},
}

_BIR_2307 = [
    {"name": "AIS-2307-2026-00012", "payee_name": "Manila Electric Company (MERALCO)", "payee_tin_redacted": "•••• 5321",
     "approval_status": "Approved", "gross_amount": 45320.50, "ewt_amount": 906.41, "net_amount": 44414.09,
     "period_from": "2026-06-01", "period_to": "2026-06-30"},
    {"name": "AIS-2307-2026-00013", "payee_name": "PLDT Inc.", "payee_tin_redacted": "•••• 8890",
     "approval_status": "Draft", "gross_amount": 12800.00, "ewt_amount": 256.00, "net_amount": 12544.00,
     "period_from": "2026-06-01", "period_to": "2026-06-30"},
]


# ── tool handlers (return shapes the ais_mcp formatters understand) ───────────
def _dv_row(d: dict) -> dict:
    return {"name": d["name"], "payee_name": d["payee_name"],
            "gross_amount": d["gross_amount"], "workflow_status": d["workflow_status"]}


async def get_dv(name: str) -> dict:
    for d in _DVS:
        if d["name"].upper() == str(name).upper():
            return d
    return {"found": False, "name": name}


async def list_pending_dvs() -> dict:
    rows = [_dv_row(d) for d in _DVS if d["workflow_status"] in _PENDING]
    return {"rows": rows, "total_count": len(rows)}


async def find_dv(query: str) -> dict:
    q = (query or "").lower()
    hits = [d for d in _DVS if q in d["name"].lower() or q in d["payee_name"].lower()
            or q in (d.get("control_number") or "").lower() or q in (d.get("ors_burs_reference") or "").lower()]
    rows = [_dv_row(d) for d in hits]
    return {"rows": rows, "total_count": len(rows)}


async def budget_balance(kind: str, name: str) -> dict:
    key = next((k for k in _BALANCES if k in (name or "").lower()), "mooe")
    b = _BALANCES[key]
    balance = round(b["allotment"] - b["obligated"], 2)
    unpaid = round(b["obligated"] - b["disbursed"], 2)
    return {"found": True, "kind": kind, "name": name,
            "allotment": b["allotment"], "obligated": b["obligated"],
            "disbursed": b["disbursed"], "unpaid_obligations": unpaid, "balance": balance}


async def lookup_uacs(kind: str, query: str = "") -> dict:
    pool = _UACS.get(kind, _OBJECT_CODES)
    q = (query or "").lower()
    matches = [m for m in pool if not q or q in m["description"].lower() or q in m["code"].lower()]
    return {"matches": matches or pool[:3]}


async def dv_totals(group_by: str = "", **filters) -> dict:
    live = [d for d in _DVS if d["workflow_status"] != "Cancelled"]
    total = round(sum(d["gross_amount"] for d in live), 2)
    count = len(live)
    if not group_by:
        return {"total_amount": total, "count": count}
    buckets: dict[str, dict] = {}
    for d in live:
        if group_by == "month":
            key = (d.get("posting_date") or "")[:7]
        elif group_by == "fiscal_year":
            key = (d.get("posting_date") or "")[:4]
        else:
            key = str(d.get(group_by) or "(unset)")
        b = buckets.setdefault(key, {"key": key, "amount": 0.0, "count": 0})
        b["amount"] = round(b["amount"] + d["gross_amount"], 2)
        b["count"] += 1
    rows = sorted(buckets.values(), key=lambda r: r["amount"], reverse=True)
    return {"group_by": group_by, "total_amount": total, "count": count, "rows": rows}


async def run_report(report_name: str) -> dict:
    return {
        "report_name": report_name,
        "columns": [
            {"fieldname": "pap", "label": "P/A/P"},
            {"fieldname": "allotment", "label": "Allotment"},
            {"fieldname": "obligations", "label": "Obligations"},
            {"fieldname": "disbursements", "label": "Disbursements"},
            {"fieldname": "balance", "label": "Unobligated Balance"},
        ],
        "rows": [
            {"pap": "Higher Education — Instruction", "allotment": "5,000,000.00",
             "obligations": "4,130,000.00", "disbursements": "2,960,000.00", "balance": "870,000.00"},
            {"pap": "General Administration & Support", "allotment": "2,000,000.00",
             "obligations": "1,540,000.00", "disbursements": "1,205,000.00", "balance": "460,000.00"},
        ],
    }


async def list_bir_2307() -> dict:
    return {"rows": list(_BIR_2307), "total_count": len(_BIR_2307)}


async def get_bir_2307(name: str) -> dict:
    for c in _BIR_2307:
        if c["name"].upper() == str(name).upper():
            return c
    return {"found": False, "name": name}


async def find_bir_2307(query: str = "", **filters) -> dict:
    q = (query or "").lower()
    hits = [c for c in _BIR_2307 if not q or q in c["name"].lower() or q in c["payee_name"].lower()]
    return {"rows": hits, "total_count": len(hits)}


# Tool schemas mirror api/ais_mcp.py _LLM_TOOLS (names + input schemas must match
# so the bridge's router and call_tool line up). READ tools only.
_HANDLERS = {
    "get_dv": get_dv, "list_pending_dvs": list_pending_dvs, "find_dv": find_dv,
    "budget_balance": budget_balance, "lookup_uacs": lookup_uacs, "dv_totals": dv_totals,
    "run_report": run_report, "list_bir_2307": list_bir_2307, "get_bir_2307": get_bir_2307,
    "find_bir_2307": find_bir_2307,
}

_SCHEMAS: dict[str, dict] = {
    "get_dv": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    "list_pending_dvs": {"type": "object", "properties": {}},
    "find_dv": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "budget_balance": {"type": "object", "properties": {"kind": {"type": "string"}, "name": {"type": "string"}}, "required": ["kind", "name"]},
    "lookup_uacs": {"type": "object", "properties": {"kind": {"type": "string"}, "query": {"type": "string"}}, "required": ["kind"]},
    "dv_totals": {"type": "object", "properties": {"group_by": {"type": "string"}}},
    "run_report": {"type": "object", "properties": {"report_name": {"type": "string"}}, "required": ["report_name"]},
    "list_bir_2307": {"type": "object", "properties": {}},
    "get_bir_2307": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    "find_bir_2307": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def build_server() -> Server:
    server: Server = Server("cvsu-ais-stub")

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [types.Tool(name=n, description=f"[STUB] {n}", inputSchema=_SCHEMAS[n]) for n in _HANDLERS]

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        fn = _HANDLERS.get(name)
        if fn is None:
            payload: Any = {"ok": False, "error": f"unknown tool: {name}"}
        else:
            try:
                payload = await fn(**(arguments or {}))
            except TypeError as exc:
                payload = {"ok": False, "error": f"bad arguments: {exc}"}
            except Exception as exc:  # noqa: BLE001 — never kill the wire
                _log.exception("tool %s failed", name)
                payload = {"ok": False, "error": f"tool failure: {exc.__class__.__name__}"}
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
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 so a dockerized Sevi can reach it via host.docker.internal")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    server = build_server()
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await server.run(r, w, server.create_initialization_options())
        return Response()

    async def health(_request):
        return JSONResponse({"service": "cvsu-ais-stub", "data": "FAKE", "tools": list(_HANDLERS)})

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/health", endpoint=health),
    ])
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        _log.warning("binding %s — MCP wire has NO auth and serves FAKE data; dev use only", args.host)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
