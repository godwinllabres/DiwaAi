"""
Sevi bridge to the CvSU HR MCP server — DTR read path (Phase: HR track).

Mirrors api/ais_mcp.py but for HR: routes DTR / attendance queries to the
cvsu-hr server's READ tools and formats a Daily Time Record summary. The
official CSC Form 48 render (render_dtr) and any write go through the
preview/confirm flow, never this bridge.

Config (env):
  HR_MCP_URL      SSE endpoint (default http://127.0.0.1:8766/sse)
  HR_MCP_ENABLED  "1" to enable (default "0" — HR is DPA-sensitive; off unless
                  the surface is fenced behind an authenticated identity)

DPA note: real deployment passes the authenticated user's token so the HR
server self/unit-scopes. This bridge takes an explicit `employee` for the demo;
production must derive it from the session identity, never trust a free-text id.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

_logger = logging.getLogger("hr_mcp")

_MCP_URL = os.environ.get("HR_MCP_URL", "http://127.0.0.1:8766/sse")
_ENABLED = os.environ.get("HR_MCP_ENABLED", "0") == "1"
_CALL_TIMEOUT_SECONDS = float(os.environ.get("HR_MCP_TIMEOUT", "8"))
_DEFAULT_YEAR = int(os.environ.get("HR_MCP_DEFAULT_YEAR", "2026"))

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    _MCP_AVAILABLE = True
except Exception:  # noqa: BLE001 — SDK not installed -> bridge is a no-op
    _MCP_AVAILABLE = False

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"]) if m}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})

# DTR / attendance intent. Widened with common synonyms — this only runs for an
# authenticated INTERNAL user, so generous matching can't affect the student surface.
_DTR_RE = re.compile(
    r"\b(dtr|daily\s*time\s*record|time\s*record|attendance|"
    r"time\s*sheet|timesheet|time\s*logs?|"
    r"csc\s*form\s*(no\.?\s*)?48|form\s*48|"
    r"biometrics?|clock\s*(in|out)|time\s*(in|out)|"
    r"hours\s*worked|my\s*hours|punch(es|ed)?)\b",
    re.IGNORECASE)
# A DTR query that also asks to print/download -> return the official PDF link.
_DTR_PDF_RE = re.compile(r"\b(pdf|render|print|download|official|form\s*48)\b", re.IGNORECASE)
_MONTH_NAMES = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
                7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}
# Public Frappe URL for the download link the USER clicks (their Desk session
# authenticates it) — distinct from any internal URL the bridge itself uses.
_FRAPPE_PUBLIC = os.environ.get("HR_FRAPPE_PUBLIC", "http://accounting.localhost:8002").rstrip("/")
# Real-attendance source: the container reaches Frappe on host.docker.internal
# (Python can't resolve *.localhost) with a Host header for multi-tenant routing.
_FRAPPE_INTERNAL = os.environ.get("HR_FRAPPE_INTERNAL", "http://host.docker.internal:8002").rstrip("/")
_FRAPPE_HOST = os.environ.get("HR_FRAPPE_HOST", "accounting.localhost")
_RELAY_KEY = os.environ.get("HR_RELAY_KEY", "")
_ATT_SOURCE = os.environ.get("HR_ATTENDANCE_SOURCE", "stub")  # "frappe" (real) | "stub"
# HRIS role denials surfaced by the Frappe DTR method (sevi_dtr_attendance).
_DENIAL_MSGS = {
    "not_linked": ("Your account isn't linked to an Employee record yet, so I can't pull "
                   "your DTR. Ask HR to set your User ID on your Employee record."),
    "no_access": "You don't have access to Daily Time Records.",
    "no_dtr": "You don't have a Daily Time Record on file for that period yet.",
    "no_attendance": ("You don't have any check-in records for that period, so there's "
                      "nothing to build a DTR from yet."),
}


async def call_tool(name: str, arguments: dict) -> Any:
    """Open an SSE session, call one HR tool, return parsed JSON (or text)."""
    if not _MCP_AVAILABLE:
        raise RuntimeError("mcp SDK not installed — pip install 'mcp[sse]'")
    import asyncio
    async def _do() -> Any:
        async with sse_client(_MCP_URL) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)
    result = await asyncio.wait_for(_do(), timeout=_CALL_TIMEOUT_SECONDS)
    first = result.content[0] if result.content else None
    text = getattr(first, "text", "") if first is not None else ""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def route(text: str) -> Optional[tuple[str, dict]]:
    """Return (tool, args) if the message is a DTR/attendance query, else None."""
    if not _DTR_RE.search(text):
        return None
    low = text.lower()
    month = next((n for name, n in _MONTHS.items() if re.search(rf"\b{name}\b", low)), None)
    if month is None:
        return None  # need a month to pull a record; ask the user to add one
    ym = re.search(r"\b(20\d\d)\b", text)
    year = int(ym.group(1)) if ym else _DEFAULT_YEAR
    return "get_attendance", {"month": month, "year": year}


def _format_dtr(data: Any) -> Optional[dict]:
    if not isinstance(data, dict) or "days" not in data:
        return None
    t = data.get("totals") or {}
    exceptions = [d for d in data["days"] if d.get("flags")]
    tardy = sum(1 for d in exceptions if "tardy_am" in d["flags"])
    missing = sum(1 for d in exceptions if "missing_punch" in d["flags"])
    lines = [
        f"DTR summary — {data.get('employee_name')} ({data.get('month'):02d}/{data.get('year')})",
        f"  Present: {t.get('days_present', 0)} workday(s) · Leave: {t.get('days_leave', 0)}",
        f"  Tardiness: {tardy} day(s) · Total undertime: {t.get('undertime_h', 0)}h {t.get('undertime_m', 0)}m",
    ]
    if data.get("generated"):
        lines.append("  No saved DTR record for this period yet — this is computed live "
                     "from your check-in logs; the download below generates the official form the same way.")
    if missing:
        lines.append(f"  ⚠ {missing} day(s) with a missing punch — fix in the attendance record before signing.")
    lines.append("  Tap \"Download my DTR\" below for the official CSC Form 48 (PDF).")
    # Chat-friendly: 3 narrow columns fit the ~360px widget panel (no h-scroll).
    # The full per-day AM/PM detail lives in the printable CSS Form 48 (PDF).
    _FLAG_LABEL = {
        "tardy_am": "Late (AM)", "tardy_pm": "Late (PM)",
        "early_out_pm": "Left early", "early_out_am": "Left early (AM)",
        "missing_punch": "Missing punch", "undertime": "Undertime",
    }

    def _note(d: dict) -> str:
        return " · ".join(_FLAG_LABEL.get(f, f) for f in (d.get("flags") or [])) or "—"

    def _ut(d: dict) -> str:
        h, m = d.get("undertime_h", 0), d.get("undertime_m", 0)
        if not (h or m):
            return "—"
        return f"{h}h {m}m" if h else f"{m}m"

    columns = [
        {"key": "day", "label": "Day", "align": "right"},
        {"key": "note", "label": "Exception", "align": "left"},
        {"key": "ut", "label": "Undertime", "align": "right"},
    ]
    rows = [{"day": d["day"], "note": _note(d), "ut": _ut(d)} for d in exceptions]
    table = {"title": "Exception days", "columns": columns, "rows": rows} if rows else None
    return {"text": "\n".join(lines), "table": table, "source": "hr_mcp"}


async def _frappe_attendance(month: int, year: int,
                             acting_user: Optional[str] = None) -> Optional[dict]:
    """Real DTR summary from Frappe (cvsu_web.sevi_web.api.sevi_dtr_attendance).

    `acting_user` (the authenticated Sevi identity) is forwarded so Frappe applies
    that user's HRIS role: an Employee only ever sees their own DTR; HR roles see
    any. Without it, the relay would fall back to the elevated full grant."""
    import httpx
    params = {"month": _MONTH_NAMES.get(month, ""), "year": str(year)}
    if acting_user:
        params["acting_user"] = acting_user
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.get(
            f"{_FRAPPE_INTERNAL}/api/method/cvsu_web.sevi_web.api.sevi_dtr_attendance",
            params=params,
            headers={"X-Sevi-Relay-Key": _RELAY_KEY, "Host": _FRAPPE_HOST},
        )
        r.raise_for_status()
        return r.json().get("message")


def _denial_reply(data) -> Optional[dict]:
    """Map an {ok: False, reason} payload to a user-facing message; None if fine."""
    if isinstance(data, dict) and data.get("ok") is False:
        return {"text": _DENIAL_MSGS.get(data.get("reason"),
                                         "That employee record isn't available."),
                "source": "hr_mcp"}
    return None


async def _dtr_pdf_reply(args: dict, acting_user: Optional[str]) -> dict:
    """Offer the official CSC Form 48 download — but only after confirming a DTR
    exists for this user, so the link never opens onto a 'no DTR' page."""
    mn = _MONTH_NAMES.get(args.get("month"), "")
    yr = args.get("year")
    pre = None
    if _ATT_SOURCE == "frappe":
        try:
            pre = await _frappe_attendance(args.get("month"), yr, acting_user)
        except Exception:  # noqa: BLE001 — never block chat on the HR bridge
            _logger.exception("hr_mcp dtr-pdf precheck failed month=%s", args.get("month"))
            return {"text": "I couldn't reach the DTR service just now — please try again in a moment.",
                    "source": "hr_mcp"}
        if not pre:
            return {"text": "You don't have a DTR on file for that period yet.", "source": "hr_mcp"}
        denied = _denial_reply(pre)
        if denied:
            return denied
    link = (f"{_FRAPPE_PUBLIC}/api/method/cvsu_web.sevi_web.api.sevi_my_dtr_pdf"
            f"?month={mn}&year={yr}")
    generated = bool(isinstance(pre, dict) and pre.get("generated"))
    intro = (f"No saved DTR record for {mn} {yr} yet, so I generated your official DTR — "
             f"CSC Form No. 48 — live from your check-in logs."
             if generated else
             f"Your official DTR — CSC Form No. 48 — for {mn} {yr} is ready.")
    return {"text": f"{intro}\n[Download the DTR (PDF)]({link}) — opens with your Desk login.",
            "source": "hr_mcp",
            "suggestions": [f"My attendance summary for {mn} {yr}"]}


async def _dtr_summary_reply(tool_name: str, args: dict, employee: Optional[str],
                             acting_user: Optional[str]) -> Optional[dict]:
    """Fetch attendance (real Frappe or stub), format the DTR summary, and offer a
    one-tap download of the official form."""
    if _ATT_SOURCE == "frappe":
        try:
            data = await _frappe_attendance(args.get("month"), args.get("year"), acting_user)
        except Exception:  # noqa: BLE001 — never block chat on the HR bridge
            _logger.exception("hr_mcp frappe attendance failed month=%s", args.get("month"))
            return None
    else:
        # DPA: production derives `employee` from the authenticated session, never
        # from free text. The stub/demo accepts an explicit id.
        args["employee"] = employee or os.environ.get("HR_MCP_DEMO_EMPLOYEE", "2018-0087")
        try:
            data = await call_tool(tool_name, args)
        except Exception:  # noqa: BLE001 — never block chat on the HR bridge
            _logger.exception("hr_mcp tool=%s args=%s failed", tool_name,
                              {k: v for k, v in args.items() if k != "employee"})
            return None
    denied = _denial_reply(data)
    if denied:
        return denied
    formatted = _format_dtr(data)
    if formatted and isinstance(data, dict) and data.get("days"):
        mn = _MONTH_NAMES.get(data.get("month"), "")
        formatted["suggestions"] = [f"Download my DTR for {mn} {data.get('year')}"]
    return formatted or {"text": "HR responded but the DTR format wasn't recognized.", "source": "hr_mcp"}


async def try_handle(message: str, employee: Optional[str] = None,
                     session_id: Optional[str] = None,
                     acting_user: Optional[str] = None) -> Optional[dict]:
    """Detect a DTR query and answer it (summary or official-form download). None
    if the message isn't a DTR query.

    `acting_user` is the authenticated Sevi identity (JWT sub); it is forwarded to
    Frappe so the DTR is scoped to that user's HRIS role, not shown broadly."""
    if not (_MCP_AVAILABLE and _ENABLED):
        return None
    routed = route(message)
    if routed is None:
        # A DTR / attendance query missing only the month — ask for it (with quick
        # options) instead of falling through to the student chatbot, which would
        # answer with student content or refuse the internal user as out of scope.
        if _DTR_RE.search(message):
            return {"text": "Which month would you like? For example, \"my DTR for June 2026\".",
                    "source": "hr_mcp",
                    "suggestions": ["My DTR for June 2026", "Download my DTR for June 2026"]}
        return None
    tool_name, args = routed
    if _DTR_PDF_RE.search(message):
        return await _dtr_pdf_reply(args, acting_user)
    return await _dtr_summary_reply(tool_name, args, employee, acting_user)
