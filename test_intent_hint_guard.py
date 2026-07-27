"""Regression tests — the intent_hint bypass is READ-ONLY.

`try_handle(intent_hint=...)` names an MCP tool directly, skipping the router.
Without a guard, a caller holding X-Internal-Key could invoke approve_dv /
post_dv / cancel_dv through it and skip /ais/write entirely — no UI confirm and
no per-user OAuth token. These tests pin that hatch shut.

Run:  python test_intent_hint_guard.py
"""
import asyncio
import sys

from api import ais_mcp, agentic_loop

_failures = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _failures.append(name)


def main() -> int:
    # One canonical write list, shared by the loop and the hint bypass.
    check("agentic_loop reuses ais_mcp.WRITE_TOOLS (single source of truth)",
          agentic_loop._WRITE_DENYLIST is ais_mcp.WRITE_TOOLS)
    check("every /ais/write action is in WRITE_TOOLS",
          {"approve_dv", "post_dv", "cancel_dv", "set_dv_status"} <= set(ais_mcp.WRITE_TOOLS))

    # Force past the availability guards so try_handle reaches the hint branch,
    # and stub the transport so nothing leaves the process.
    prev = (ais_mcp._MCP_AVAILABLE, ais_mcp._ENABLED, ais_mcp.call_tool)
    ais_mcp._MCP_AVAILABLE = ais_mcp._ENABLED = True
    invoked = []

    async def fake_call_tool(name, args):
        invoked.append((name, args))
        return {"ok": True}

    ais_mcp.call_tool = fake_call_tool
    try:
        # Every write tool must be refused, and must not reach the transport.
        for tool in sorted(ais_mcp.WRITE_TOOLS):
            invoked.clear()
            result = asyncio.run(
                ais_mcp.try_handle("x", intent_hint=tool,
                                   intent_args={"name": "DV-1", "confirm": True})
            )
            check(f"write tool refused via intent_hint: {tool}",
                  result is None and not invoked)

        # A read tool still works, but caller-supplied auth/confirm are stripped:
        # the hatch must not be able to self-authorize a mutation.
        invoked.clear()
        asyncio.run(ais_mcp.try_handle(
            "x", intent_hint="get_dv",
            intent_args={"name": "DV-1", "__auth_token": "STOLEN", "confirm": True},
        ))
        check("read tool via intent_hint still dispatches", len(invoked) == 1)
        args = invoked[0][1] if invoked else {}
        check("__auth_token stripped from caller args", "__auth_token" not in args)
        check("confirm stripped from caller args", "confirm" not in args)
        check("legitimate args preserved", args.get("name") == "DV-1")
    finally:
        ais_mcp._MCP_AVAILABLE, ais_mcp._ENABLED, ais_mcp.call_tool = prev

    print("\n" + ("ALL PASS" if not _failures else f"FAILURES: {_failures}"))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
