"""diwa-connectors entry point.

Transports:
  http  (default) — Starlette app with /sse, /messages/, /health, served by uvicorn.
                    The MCP wire carries NO auth of its own (same caveat as
                    ais-mcp): bind to loopback unless a reverse proxy fronts it.
  stdio           — for MCP hosts that spawn the server as a subprocess.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .config import load_config
from .server import build_server

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency): KEY=VALUE lines, existing env wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split("#", 1)[0].strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


async def _run_stdio(server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _run_http(server, groups, host: str, port: int) -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())
        return Response()

    async def health(request):
        return JSONResponse(
            {
                "service": "diwa-connectors",
                "groups": [g.name for g in groups],
                "tools": [t.name for g in groups for t in g.tools],
            }
        )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
            Route("/health", endpoint=health),
        ]
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(prog="diwa-connectors", description=__doc__)
    parser.add_argument("--transport", choices=("http", "stdio"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--env-file", default=".env", help="path to a .env file (existing env vars win)")
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("CONNECTORS_LOG_LEVEL", "INFO"))
    _load_dotenv(Path(args.env_file))

    cfg = load_config()
    server, groups = build_server(cfg)
    if not groups:
        logging.warning(
            "no tool groups active — set COURSES_BASE_URL / ORPS_BASE_URL to enable them; serving /health only"
        )

    if args.transport == "stdio":
        asyncio.run(_run_stdio(server))
    else:
        if args.host not in _LOOPBACK_HOSTS:
            logging.warning(
                "binding to %s: the MCP wire is UNAUTHENTICATED — anything that can reach "
                "this port can call every tool. Keep it on loopback or behind a reverse proxy.",
                args.host,
            )
        _run_http(server, groups, args.host, args.port)


if __name__ == "__main__":
    main()
