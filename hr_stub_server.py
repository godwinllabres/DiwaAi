"""
Stub cvsu-hr MCP server — DEV/DEMO ONLY, serves FAKE attendance data.

Stands in for the real (external) CvSU HR MCP server so the DTR path can be
exercised end-to-end. Implements the READ tools from
docs/generate-dtr-contract.md (get_attendance, get_employee_shift,
list_unit_employees). Render/write tools (render_dtr, draft_dtr) are NOT here —
those go through the preview/confirm flow against the real server.

  * FAKE data — never wire to production.
  * READ-only. DPA note: real deployment self/unit-scopes by the authenticated
    user's token; the stub ignores identity (demo).

Run:  pip install "mcp[sse]" starlette uvicorn
      python hr_stub_server.py --host 0.0.0.0 --port 8766
Config (sevi.env):  HR_MCP_URL=http://host.docker.internal:8766/sse
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("hr-stub")

_SHIFT = {"am_in": "08:00", "am_out": "12:00", "pm_in": "13:00", "pm_out": "17:00", "grace_min": 0}

_EMPLOYEES = {
    "2018-0087": "Dela Cruz, Juan Santos",
    "2020-0142": "Reyes, Maria Liza",
    "2021-0456": "Santos, Pedro M.",
}
_UNIT = {"Accounting Office": ["2018-0087", "2020-0142"], "ICTO": ["2021-0456"]}

# Fixed sample month so output is deterministic (no clock dependency).
_HOLIDAYS = {12: "Independence Day"}          # June 12
_LEAVE = {15: "Vacation Leave"}               # June 15
_TARDY = {2: 22, 9: 15}                        # day -> minutes late (AM)
_MISSING_PUNCH = {18}                          # day with an incomplete record


def _day_row(day: int, weekday: int) -> dict:
    if weekday >= 5:  # Sat/Sun
        return {"day": day, "type": "weekend", "flags": []}
    if day in _HOLIDAYS:
        return {"day": day, "type": "holiday", "name": _HOLIDAYS[day], "flags": []}
    if day in _LEAVE:
        return {"day": day, "type": "leave", "leave_type": _LEAVE[day], "flags": []}
    if day in _MISSING_PUNCH:
        return {"day": day, "type": "workday", "am_in": "08:01", "am_out": None,
                "pm_in": None, "pm_out": "17:02", "undertime_h": 0, "undertime_m": 0,
                "flags": ["missing_punch"]}
    late = _TARDY.get(day, 0)
    am_in = f"08:{late:02d}" if late else "07:58"
    flags = (["tardy_am"] if late else [])
    ut_m = late
    pm_out = "17:03"
    if day == 9:  # also left early -> extra undertime + flag
        pm_out, flags, ut_m = "16:40", flags + ["early_out_pm"], late + 20
    return {"day": day, "type": "workday", "am_in": am_in, "am_out": "12:02",
            "pm_in": "12:59", "pm_out": pm_out,
            "undertime_h": ut_m // 60, "undertime_m": ut_m % 60, "flags": flags}


async def get_attendance(employee: str, month: int, year: int) -> dict:
    name = _EMPLOYEES.get(employee)
    if name is None:
        return {"ok": False, "code": "employee_not_found", "employee": employee}
    month, year = int(month), int(year)
    days = []
    tot_ut = 0
    present = leave = 0
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        wd = calendar.weekday(year, month, day)
        row = _day_row(day, wd)
        days.append(row)
        if row["type"] == "workday":
            present += 1
            tot_ut += row.get("undertime_h", 0) * 60 + row.get("undertime_m", 0)
        elif row["type"] == "leave":
            leave += 1
    return {
        "employee": employee, "employee_name": name, "month": month, "year": year,
        "shift": _SHIFT, "days": days,
        "totals": {"undertime_h": tot_ut // 60, "undertime_m": tot_ut % 60,
                   "days_present": present, "days_leave": leave},
    }


async def get_employee_shift(employee: str) -> dict:
    return {"employee": employee, "shift": _SHIFT}


async def list_unit_employees(department: str = "") -> dict:
    dept = department or next(iter(_UNIT))
    ids = _UNIT.get(dept, [])
    return {"department": dept,
            "employees": [{"employee": e, "employee_name": _EMPLOYEES[e]} for e in ids]}


_HANDLERS = {
    "get_attendance": get_attendance,
    "get_employee_shift": get_employee_shift,
    "list_unit_employees": list_unit_employees,
}
_SCHEMAS = {
    "get_attendance": {"type": "object", "properties": {
        "employee": {"type": "string"}, "month": {"type": "integer"}, "year": {"type": "integer"}},
        "required": ["employee", "month", "year"]},
    "get_employee_shift": {"type": "object", "properties": {"employee": {"type": "string"}},
                           "required": ["employee"]},
    "list_unit_employees": {"type": "object", "properties": {"department": {"type": "string"}}},
}


def build_server() -> Server:
    server: Server = Server("cvsu-hr-stub")

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
            except Exception as exc:  # noqa: BLE001
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
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()

    server = build_server()
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await server.run(r, w, server.create_initialization_options())
        return Response()

    async def health(_request):
        return JSONResponse({"service": "cvsu-hr-stub", "data": "FAKE", "tools": list(_HANDLERS)})

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
