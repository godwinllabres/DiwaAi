"""MCP server assembly: registers every active group's tools on one Server."""
from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server

from .config import Config
from .registry import ToolGroup, collect_groups

_logger = logging.getLogger("diwa.connectors.server")


def build_server(cfg: Config) -> tuple[Server, list[ToolGroup]]:
    server: Server = Server("diwa-connectors")
    groups = collect_groups(cfg)
    tools = {t.name: t for g in groups for t in g.tools}
    _logger.info(
        "active groups: %s (%d tools)",
        ", ".join(g.name for g in groups) or "none",
        len(tools),
    )

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
            for t in tools.values()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        tool = tools.get(name)
        if tool is None:
            payload: dict[str, Any] = {"ok": False, "error": f"unknown tool: {name}"}
        else:
            try:
                payload = await tool.handler(**(arguments or {}))
            except TypeError as exc:
                payload = {"ok": False, "error": f"bad arguments: {exc}"}
            except Exception as exc:  # a failing upstream must never kill the wire
                _logger.exception("tool %s failed", name)
                payload = {"ok": False, "error": f"tool failure: {exc.__class__.__name__}"}
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))]

    return server, groups
