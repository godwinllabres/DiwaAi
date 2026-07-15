"""
PROTOTYPE — READ-only multi-step agentic tool-use loop for the AIS bridge.

Keystone #3 of the internal-copilot design (docs/sevi-internal-copilot-design.md).
Turns the single-shot AIS router (one {tool,args} -> one call_tool -> deterministic
format) into a *bounded* loop where the model can plan, chain several READ tools,
feed each result back to itself, and synthesize one grounded answer — so
"resolve the UACS code, then check its balance" works in a single turn.

Design guarantees (match the governance guardrails):
  * OPT-IN — only runs when AIS_AGENTIC_LOOP=1 and the AIS bridge is available.
  * READ-ONLY — the loop may call only the advertised READ tools; any
    write/create tool is refused by name (see _WRITE_DENYLIST), so there is NO
    natural-language path to a write. Writes stay behind the /ais/write confirm
    flow (see docs/create-dv-contract.md).
  * BOUNDED — hard caps on steps, total tool calls, wall-clock, and the size of
    tool output fed back to the model.
  * GROUNDED — the synthesis prompt forbids inventing DV names/amounts/dates and
    tells the model to say so when the tools don't contain the answer.
  * AUDITABLE — every tool call is metered + logged with redacted args, reusing
    ais_mcp._metrics_record / ais_mcp._redact_args.

Wiring (add near the top of ais_mcp.try_handle, right after the
`if not (_MCP_AVAILABLE and _ENABLED): return None` guard):

    if intent_hint is None and os.getenv("AIS_AGENTIC_LOOP") == "1":
        from . import agentic_loop
        handled = await agentic_loop.run_agentic_read(message, session_id=session_id)
        if handled is not None:
            return handled
        # else fall through to the deterministic single-shot router below

Returns the same dict shape as try_handle so the caller is unchanged:
    {"text", "dv_card", "table", "suggestions", "context_set", "source", "trace"}
or None when the query wasn't AIS-shaped (no tool was called) — so the normal
NLU pipeline still runs.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Optional

from . import ais_mcp

_logger = logging.getLogger("ais_mcp.agentic")

# ── bounds (all overridable by env) ───────────────────────────────────────────
MAX_STEPS = int(os.getenv("AIS_AGENTIC_MAX_STEPS", "4"))          # model<->tool rounds
MAX_TOOL_CALLS = int(os.getenv("AIS_AGENTIC_MAX_TOOL_CALLS", "6"))  # hard call ceiling
WALL_BUDGET_S = float(os.getenv("AIS_AGENTIC_WALL_BUDGET_S", "20"))
SYNTH_MAX_TOKENS = int(os.getenv("AIS_AGENTIC_MAX_TOKENS", "600"))
RESULT_CHARS_CAP = int(os.getenv("AIS_AGENTIC_RESULT_CAP", "6000"))  # per tool result fed back

# Every advertised tool is a READ today; deny writes explicitly so a future
# write tool accidentally added to _LLM_TOOLS can NEVER be invoked in-loop.
_WRITE_DENYLIST = {
    # AIS writes
    "create_dv", "approve_dv", "post_dv", "cancel_dv", "set_dv_status",
    "draft_bir_2307", "issue_bir_2307", "submit_dv", "amend_dv",
    # HR / DTR — render_dtr produces an official CSC form (DPA-sensitive) and
    # draft_dtr writes a record; both go through the preview/confirm flow, never
    # the loop. get_attendance stays a READ (allowed) once the cvsu-hr MCP ships.
    "render_dtr", "draft_dtr",
    # HR / COE + Service Record — official signed documents, rendered behind the
    # confirm flow. Profile reads (get_employee_profile, salary permlevel-gated)
    # stay READ.
    "render_coe", "render_service_record",
}

_AGENT_SYSTEM = (
    "You are Sevi, an internal CvSU accounting copilot for staff. Answer the user's "
    "question using ONLY the AIS read tools provided. Plan the minimal set of calls, "
    "chaining them when one result feeds the next (e.g. resolve a UACS/PAP code with "
    "lookup_uacs, then check budget_balance for it). Ground EVERY figure, DV name, and "
    "date in tool output — never invent them. If the tools do not contain the answer, "
    "say so plainly. You can only READ: you cannot create, approve, post, or cancel "
    "anything — if asked to, explain that the user must do that in the ERPNext Desk "
    "under their own sign-off. Keep the final answer concise and factual."
)


def _is_read_tool(name: str) -> bool:
    return name in ais_mcp._KNOWN_TOOL_NAMES and name not in _WRITE_DENYLIST


def _pick_provider() -> Optional[str]:
    """Mirror ais_mcp's router provider selection."""
    prov = os.getenv("LLM_PROVIDER", "").strip().lower()
    if prov in ("ollama", "local"):
        return "ollama"
    if prov in ("claude", "anthropic"):
        return "anthropic"
    # auto: prefer local, then Anthropic if a key is present
    if os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_MODEL"):
        return "ollama"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


class _State:
    """Carries loop progress across tool calls for cards + auditing."""
    def __init__(self) -> None:
        self.used_tool = False
        self.calls = 0
        self.last_tool: Optional[str] = None
        self.last_data: Any = None
        self.trace: list[dict] = []
        self.t_start = time.monotonic()

    def over_budget(self) -> bool:
        return (
            self.calls >= MAX_TOOL_CALLS
            or (time.monotonic() - self.t_start) > WALL_BUDGET_S
        )


async def _exec_tool(name: str, args: dict, st: _State, call_tool_fn: Callable) -> dict:
    """Run one tool call under the read-only + audit rules. Returns
    {"content": str, "is_error": bool} to feed back to the model."""
    st.used_tool = True
    st.calls += 1

    if not _is_read_tool(name):
        _logger.warning("agentic refused non-read tool=%s args=%s", name, ais_mcp._redact_args(args))
        return {
            "content": json.dumps({
                "error": "tool_not_allowed",
                "message": (
                    f"'{name}' is not a read tool. Writes (create/approve/post/cancel) "
                    "must be done by the user in the Desk under their own sign-off."
                ),
            }),
            "is_error": True,
        }

    t0 = time.monotonic()
    try:
        data = await call_tool_fn(name, args)
        ok = True
    except ais_mcp.ToolCallError as exc:  # app-level "no" (validation/not-found)
        data, ok = {"error": "tool_error", "message": str(exc)}, False
    ms = int((time.monotonic() - t0) * 1000)

    ais_mcp._metrics_record(name, ms, ok=ok)
    st.trace.append({"tool": name, "args": ais_mcp._redact_args(args), "ok": ok, "ms": ms})
    _logger.info("agentic tool=%s args=%s ok=%s ms=%d", name, ais_mcp._redact_args(args), ok, ms)

    if ok:
        st.last_tool, st.last_data = name, data

    text = json.dumps(data, default=str)
    if len(text) > RESULT_CHARS_CAP:
        text = text[:RESULT_CHARS_CAP] + '..."(truncated)"'
    return {"content": text, "is_error": not ok}


# ── provider loops ────────────────────────────────────────────────────────────
async def _loop_anthropic(message: str, st: _State, call_tool_fn: Callable) -> str:
    import anthropic  # local import: never hard-depend on the SDK

    client = anthropic.AsyncAnthropic()
    model = ais_mcp._router_anthropic_model()
    msgs: list[dict] = [{"role": "user", "content": message}]
    final = ""

    for _ in range(MAX_STEPS):
        force_final = st.over_budget()
        resp = await client.messages.create(
            model=model,
            max_tokens=SYNTH_MAX_TOKENS,
            system=_AGENT_SYSTEM,
            tools=[] if force_final else ais_mcp._LLM_TOOLS,
            messages=msgs,
        )
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

        if not tool_uses:
            final = text
            break

        msgs.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            r = await _exec_tool(tu.name, dict(tu.input or {}), st, call_tool_fn)
            results.append({
                "type": "tool_result", "tool_use_id": tu.id,
                "content": r["content"], "is_error": r["is_error"],
            })
        msgs.append({"role": "user", "content": results})
    else:
        # Steps exhausted without a text answer — one last no-tools synthesis.
        resp = await client.messages.create(
            model=model, max_tokens=SYNTH_MAX_TOKENS, system=_AGENT_SYSTEM,
            messages=msgs + [{"role": "user", "content": "Answer now from the results above; do not call more tools."}],
        )
        final = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    return final


async def _loop_ollama(message: str, st: _State, call_tool_fn: Callable) -> str:
    import httpx

    url = f"{ais_mcp._OLLAMA_BASE_URL}/v1/chat/completions"
    model = ais_mcp._router_ollama_model()
    msgs: list[dict] = [
        {"role": "system", "content": _AGENT_SYSTEM},
        {"role": "user", "content": message},
    ]
    final = ""

    async with httpx.AsyncClient(timeout=30.0) as http:
        for _ in range(MAX_STEPS):
            force_final = st.over_budget()
            body = {"model": model, "messages": msgs, "max_tokens": SYNTH_MAX_TOKENS}
            if not force_final:
                body["tools"] = ais_mcp._LLM_TOOLS_OPENAI
                body["tool_choice"] = "auto"
            resp = await http.post(url, json=body)
            resp.raise_for_status()
            msg = ((resp.json().get("choices") or [{}])[0].get("message")) or {}
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final = (msg.get("content") or "").strip()
                break

            # Echo the assistant turn (with its tool_calls) then answer each.
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw = fn.get("arguments")
                args = raw if isinstance(raw, dict) else _safe_json(raw)
                r = await _exec_tool(name, args, st, call_tool_fn)
                msgs.append({"role": "tool", "tool_call_id": call.get("id", name), "content": r["content"]})
        else:
            msgs.append({"role": "user", "content": "Answer now from the results above; do not call more tools."})
            resp = await http.post(url, json={"model": model, "messages": msgs, "max_tokens": SYNTH_MAX_TOKENS})
            resp.raise_for_status()
            final = (((resp.json().get("choices") or [{}])[0].get("message")) or {}).get("content", "").strip()

    return final


def _safe_json(raw: Optional[str]) -> dict:
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


# ── entrypoint ────────────────────────────────────────────────────────────────
async def run_agentic_read(
    message: str,
    session_id: Optional[str] = None,
    *,
    call_tool_fn: Optional[Callable] = None,
) -> Optional[dict]:
    """Run the bounded READ-only loop. Returns a try_handle-shaped dict, or None
    when no tool was used (fall through to NLU) or the bridge/provider is down."""
    if not (ais_mcp._MCP_AVAILABLE and ais_mcp._ENABLED):
        return None
    provider = _pick_provider()
    if provider is None:
        return None
    call_tool_fn = call_tool_fn or ais_mcp.call_tool

    st = _State()
    try:
        if provider == "anthropic":
            final = await _loop_anthropic(message, st, call_tool_fn)
        else:
            final = await _loop_ollama(message, st, call_tool_fn)
    except ais_mcp.CircuitOpenError:
        return None  # MCP down — let the single-shot path do its degraded reply
    except Exception:  # noqa: BLE001 — never block chat on the loop; fall through
        _logger.exception("agentic loop failed message=%r", message[:200])
        return None

    if not st.used_tool:
        # Model answered without calling any tool -> not an AIS query; let the
        # regular NLU pipeline handle it (mirrors the single-shot router's None).
        return None

    return {
        "text": final or ais_mcp._format_reply(st.last_tool, st.last_data),
        "dv_card": ais_mcp._build_dv_card(st.last_tool, st.last_data) if st.last_tool else None,
        "table": ais_mcp._build_table(st.last_tool, st.last_data) if st.last_tool else None,
        "suggestions": ais_mcp._build_suggestions(st.last_tool, {}, st.last_data) if st.last_tool else [],
        "context_set": ais_mcp._public_context(session_id),
        "source": "ais_mcp_agentic",
        "trace": st.trace,
    }
