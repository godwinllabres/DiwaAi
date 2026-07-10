import httpx
import respx

from diwa_connectors.config import load_config
from diwa_connectors.groups import dts

BASE = "http://dts.test"
METHOD_URL = f"{BASE}/api/method/dts.api.search_document_movement"


def _handler():
    cfg = load_config(env={"DTS_BASE_URL": BASE})
    group = dts.build_group(cfg)
    return {t.name: t.handler for t in group.tools}["dts_track_document"]


FOUND_PAYLOAD = {
    "message": {
        "document": {"name": "PR-2026-0042"},
        "document_type": "Purchase Request Document",
        "movements": [
            {
                "card_header_text": "RECEIVED BY ACCOUNTING OFFICE",
                "transaction_date": "2026-07-08 10:15:00",
                "remarks": "For processing",
                "transaction_type": "Received",
            },
            {
                "card_header_text": "RELEASED → TO BE FORWARDED TO ACCOUNTING OFFICE",
                "transaction_date": "2026-07-07 16:40:00",
                "remarks": None,
                "transaction_type": "Released",
            },
        ],
        "route": [{"office": "Records"}, {"office": "Accounting"}],
    }
}


@respx.mock
async def test_track_document_found_condensed():
    respx.get(METHOD_URL).mock(return_value=httpx.Response(200, json=FOUND_PAYLOAD))
    out = await _handler()(reference_number="PR-2026-0042")
    assert out["ok"] is True
    data = out["data"]
    assert data["found"] is True
    assert data["document_type"] == "Purchase Request Document"
    assert data["current_status"] == "RECEIVED BY ACCOUNTING OFFICE"
    assert len(data["movements"]) == 2
    assert data["total_movements"] == 2


@respx.mock
async def test_track_document_not_found():
    respx.get(METHOD_URL).mock(return_value=httpx.Response(200, json={"message": {}}))
    out = await _handler()(reference_number="NOPE-123")
    assert out["ok"] is True
    assert out["data"] == {"found": False, "reference_number": "NOPE-123"}


async def test_track_document_invalid_reference_never_calls_upstream():
    out = await _handler()(reference_number="DROP TABLE; --")
    assert out["ok"] is True
    assert out["data"]["found"] is False
    assert "invalid reference format" in out["data"]["note"]


@respx.mock
async def test_track_document_rate_limited():
    respx.get(METHOD_URL).mock(return_value=httpx.Response(429))
    out = await _handler()(reference_number="PR-2026-0042")
    assert out["ok"] is False
    assert out["status"] == 429
