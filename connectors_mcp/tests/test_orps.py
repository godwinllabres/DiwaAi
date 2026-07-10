import httpx
import respx

from diwa_connectors.config import load_config
from diwa_connectors.groups import orps

BASE = "http://orps.test"
METHOD_URL = f"{BASE}/api/method/ticketing_platform.ticketing_platform.api.get_ticket_tracking"


def _handler():
    cfg = load_config(env={"ORPS_BASE_URL": BASE})
    group = orps.build_group(cfg)
    return {t.name: t.handler for t in group.tools}["orps_track_ticket"]


@respx.mock
async def test_track_ticket_found():
    respx.get(METHOD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "ticket_number": "TT-0001",
                    "ticket_status": "In Progress",
                    "logs": [{"ticket_status": "Open", "timestamp": "2026-07-01 08:00:00"}],
                }
            },
        )
    )
    out = await _handler()(ticket_number="TT-0001")
    assert out["ok"] is True
    assert out["data"]["found"] is True
    assert out["data"]["ticket_status"] == "In Progress"
    assert len(out["data"]["logs"]) == 1


@respx.mock
async def test_track_ticket_not_found():
    respx.get(METHOD_URL).mock(return_value=httpx.Response(200, json={"message": None}))
    out = await _handler()(ticket_number="NOPE")
    assert out["ok"] is True
    assert out["data"] == {"found": False, "ticket_number": "NOPE"}


@respx.mock
async def test_track_ticket_rate_limited():
    respx.get(METHOD_URL).mock(return_value=httpx.Response(429))
    out = await _handler()(ticket_number="TT-0001")
    assert out["ok"] is False
    assert out["status"] == 429


@respx.mock
async def test_track_ticket_upstream_down():
    respx.get(METHOD_URL).mock(side_effect=httpx.ConnectError("boom"))
    out = await _handler()(ticket_number="TT-0001")
    assert out["ok"] is False
    assert "unreachable" in out["error"]
