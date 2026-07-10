"""One shared way to call an upstream system.

Every tool returns the same envelope so Diwa's answer builder can rely on it:
  {"ok": True,  "data": <json>}
  {"ok": False, "error": "<human-readable reason>", "status": <int, optional>}
Upstream failures never raise — a connector that is down must degrade to a
polite error, not take the tool server with it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

_logger = logging.getLogger("diwa.connectors.http")


async def get_json(
    url: str,
    *,
    timeout: float,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
    except httpx.TimeoutException:
        _logger.warning("timeout after %.1fs: GET %s", timeout, url)
        return {"ok": False, "error": f"upstream timed out after {timeout:.0f}s"}
    except httpx.HTTPError as exc:
        _logger.warning("unreachable: GET %s (%s)", url, exc.__class__.__name__)
        return {"ok": False, "error": "upstream unreachable"}

    if resp.status_code == 429:
        return {"ok": False, "error": "upstream rate limit hit — try again in a minute", "status": 429}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"upstream returned HTTP {resp.status_code}", "status": resp.status_code}
    try:
        return {"ok": True, "data": resp.json()}
    except ValueError:
        return {"ok": False, "error": "upstream returned non-JSON"}


def unwrap_list(data: Any) -> list[Any]:
    """Accept both a bare JSON array and Laravel's {"data": [...]} pagination wrapper."""
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    if isinstance(data, list):
        return data
    return []
