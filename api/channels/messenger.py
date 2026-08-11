"""Facebook Messenger channel gateway (POC) — Theme 2 of the 2026-08 research
synthesis: meet students on the channel they already have. The gateway is a
pure adapter: Messenger webhook events in → the existing POST /chat pipeline →
ChatResponse mapped onto Messenger message types out. No tier, threshold, or
safety behavior changes; every turn is logged/screened exactly like a web turn.

Security / privacy posture
--------------------------
* Webhook POSTs are authenticated with X-Hub-Signature-256 (HMAC-SHA256 over
  the raw body with the Meta app secret, constant-time compare). No valid
  signature → 403, body untouched.
* The page-scoped sender id (PSID) never enters the chat pipeline or its logs:
  user_id / session_id are a salted SHA-256 of the PSID ("msgr-…"), so chat
  history joins per sender without storing a Meta identifier (RA 10173 data
  minimization). Replies are addressed to the raw PSID only in the send call.
* First contact per PSID gets the bilingual AI-disclosure message (NPC
  Advisory No. 2024-04: users must know they are talking to an AI and that
  answers can be wrong). Tracking is in-memory (POC): a restart re-sends the
  disclosure — over-disclosing is the safe failure direction.
* Event ids are deduped (Meta redelivers on slow/failed 200s), and each event
  is processed inside its own try/except so one bad event cannot 500 the
  batch and trigger redelivery of its siblings.

Env (all unset by default — the router is only mounted when enabled):
    MESSENGER_ENABLED       "1" mounts the gateway
    MESSENGER_VERIFY_TOKEN  shared string for Meta's GET subscribe handshake
    MESSENGER_APP_SECRET    Meta app secret — signs webhook payloads
    MESSENGER_PAGE_TOKEN    page access token — authorizes replies
    MESSENGER_PSID_SALT     salt for PSID hashing (defaults to the app secret)
    MESSENGER_CHAT_URL      chat endpoint (default http://127.0.0.1:8000/chat)
    MESSENGER_GRAPH_URL     Graph API base (default https://graph.facebook.com/v21.0)
    MESSENGER_WEB_URL       public web app, used in map/handoff links
                            (default https://sevi.cvsu.edu.ph)

Ops walkthrough: docs/MESSENGER_GATEWAY.md.  Tests: test_messenger_gateway.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from collections import deque
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

_logger = logging.getLogger("diwa.channels.messenger")

router = APIRouter(prefix="/channels/messenger", tags=["Channels"])

# Messenger platform limits (Send API): 2000 chars per text message, 13 quick
# replies of ≤20 title chars. We chunk below the text ceiling so a citation
# line appended by the pipeline never straddles a split.
_TEXT_LIMIT = 1900
_QUICK_REPLY_MAX = 13
_QUICK_REPLY_TITLE = 20

# Bilingual, NPC-Advisory-aligned first-contact disclosure. Deliberately plain:
# what this is, what it covers, that it can be wrong, where humans are.
DISCLOSURE = (
    "Hi! Si Sevi po ito — ang official AI assistant ng Cavite State "
    "University, hindi po tao. Sumasagot ako tungkol sa admission, "
    "enrollment, scholarship, at campus. Maaaring magkamali ang sagot ko, "
    "kaya i-verify po sa mga opisyal na sanggunian o sa tanggapang "
    "babanggitin ko.\n\n"
    "Hi! I'm Sevi, CvSU's official AI assistant — not a human. I answer "
    "questions about admissions, enrollment, scholarships, and the campus. "
    "My answers can be wrong, so please verify with the official sources or "
    "offices I cite."
)


def enabled() -> bool:
    """Mount switch — app.py includes the router only when this is true."""
    return os.environ.get("MESSENGER_ENABLED", "0") == "1"


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _chat_url() -> str:
    return _cfg("MESSENGER_CHAT_URL", "http://127.0.0.1:8000/chat")


def _graph_url() -> str:
    return _cfg("MESSENGER_GRAPH_URL", "https://graph.facebook.com/v21.0").rstrip("/")


def _web_url() -> str:
    return _cfg("MESSENGER_WEB_URL", "https://sevi.cvsu.edu.ph").rstrip("/")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly)
# ---------------------------------------------------------------------------

def verify_signature(app_secret: str, raw_body: bytes, header: Optional[str]) -> bool:
    """Constant-time check of Meta's X-Hub-Signature-256 header."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def hash_psid(psid: str) -> str:
    """Stable pseudonymous chat identity for a PSID; the raw PSID stays out
    of the pipeline and its logs (see module docstring)."""
    salt = _cfg("MESSENGER_PSID_SALT") or _cfg("MESSENGER_APP_SECRET") or "sevi"
    digest = hashlib.sha256((salt + ":" + psid).encode("utf-8")).hexdigest()
    return "msgr-" + digest[:16]


def _split_text(text: str, limit: int = _TEXT_LIMIT) -> list[str]:
    """Split at whitespace under Messenger's per-message ceiling."""
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def _directory_text(card: dict) -> str:
    lines = ["📍 " + str(card.get("office") or "Office")]
    if card.get("location"):
        lines.append(str(card["location"]))
    if card.get("email"):
        lines.append("✉️ " + str(card["email"]))
    if card.get("phone"):
        lines.append("☎️ " + str(card["phone"]))
    if card.get("hours"):
        lines.append("🕐 " + str(card["hours"]))
    return "\n".join(lines)


def to_messenger_messages(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a ChatResponse dict onto an ordered list of Send-API message
    payloads. Text first, then cards it can express, then sources; quick
    replies ride the LAST message (a Messenger rule — earlier ones drop them).
    Unknown card kinds are skipped: the gateway must never 500 on an envelope
    it postdates."""
    messages: list[dict[str, Any]] = [
        {"text": chunk} for chunk in _split_text(str(reply.get("text") or ""))
    ]

    for card in reply.get("cards") or []:
        kind = (card or {}).get("kind")
        if kind == "directory":
            messages.append({"text": _directory_text(card)})
        elif kind == "map":
            label = str(card.get("label") or "campus map")
            messages.append({"text": f"🗺️ {label} — open the interactive campus "
                                     f"map here: {_web_url()}"})
        # dv / table / anything newer: web-only surfaces, skipped on purpose.

    sources = reply.get("sources") or []
    if sources:
        lines = ["Official sources / opisyal na sanggunian:"]
        for s in sources[:3]:
            label = str(s.get("citation") or s.get("label") or s.get("locator") or "source")
            url = s.get("url")
            lines.append(f"• {label}" + (f" — {url}" if url else ""))
        messages.append({"text": "\n".join(lines)})

    if not messages:
        messages = [{"text": "…"}]

    suggestions = [s for s in (reply.get("suggestions") or []) if s and str(s).strip()]
    if suggestions:
        messages[-1]["quick_replies"] = [
            {
                "content_type": "text",
                "title": str(s)[:_QUICK_REPLY_TITLE],
                "payload": str(s)[:1000],
            }
            for s in suggestions[:_QUICK_REPLY_MAX]
        ]
    return messages


# ---------------------------------------------------------------------------
# Transport seams — module-level so tests monkeypatch them; both synchronous
# (the webhook handlers are sync `def`, so FastAPI runs them in the threadpool
# and the event loop never blocks on these).
# ---------------------------------------------------------------------------

def _call_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """One turn through the real pipeline via HTTP — the gateway deliberately
    speaks the public contract instead of importing api.app internals."""
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(_chat_url(), json=payload)
        resp.raise_for_status()
        return resp.json()


def _send_message(psid: str, message: dict[str, Any]) -> None:
    """Deliver one Send-API message to a PSID (RESPONSE messaging type)."""
    token = _cfg("MESSENGER_PAGE_TOKEN")
    if not token:
        _logger.error("MESSENGER_PAGE_TOKEN unset — dropping outbound message")
        return
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{_graph_url()}/me/messages",
            params={"access_token": token},
            json={
                "recipient": {"id": psid},
                "messaging_type": "RESPONSE",
                "message": message,
            },
        )
        if resp.status_code >= 400:
            _logger.error("Messenger send failed (%s): %s",
                          resp.status_code, resp.text[:300])


# ---------------------------------------------------------------------------
# Webhook state (POC: in-memory, single worker — same standing assumption as
# the throttles/sessions; the Redis consolidation covers all of them at once)
# ---------------------------------------------------------------------------

_SEEN_MIDS: deque[str] = deque(maxlen=512)
_KNOWN_PSIDS: deque[str] = deque(maxlen=2048)


def _first_contact(psid: str) -> bool:
    if psid in _KNOWN_PSIDS:
        return False
    _KNOWN_PSIDS.append(psid)
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/webhook")
def subscribe(request: Request) -> Response:
    """Meta's one-time GET handshake when the webhook is registered."""
    qs = request.query_params
    if (qs.get("hub.mode") == "subscribe"
            and qs.get("hub.verify_token") == _cfg("MESSENGER_VERIFY_TOKEN")
            and _cfg("MESSENGER_VERIFY_TOKEN")):
        return PlainTextResponse(qs.get("hub.challenge") or "")
    return PlainTextResponse("verification failed", status_code=403)


@router.post("/webhook")
async def receive(request: Request) -> Response:
    """Webhook receiver: verify, dedupe, run each text turn through /chat,
    answer via the Send API. Always 200 on handled batches (anything else and
    Meta redelivers the whole batch). Signature is checked on the RAW body —
    parse only after it passes. The blocking work (chat call + sends) runs in
    the threadpool so the event loop — which is also serving /chat — never
    waits on this handler."""
    raw_body = await request.body()

    if not verify_signature(_cfg("MESSENGER_APP_SECRET"), raw_body,
                            request.headers.get("X-Hub-Signature-256")):
        return PlainTextResponse("invalid signature", status_code=403)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return PlainTextResponse("bad payload", status_code=400)

    from fastapi.concurrency import run_in_threadpool
    await run_in_threadpool(_process_batch, payload)
    return PlainTextResponse("EVENTS_RECEIVED")


def _process_batch(payload: dict[str, Any]) -> None:
    for entry in payload.get("entry") or []:
        for event in entry.get("messaging") or []:
            try:
                _handle_event(event)
            except Exception:  # noqa: BLE001 — one event must not sink the batch
                _logger.exception("messenger event failed")


def _handle_event(event: dict[str, Any]) -> None:
    message = event.get("message") or {}
    psid = ((event.get("sender") or {}).get("id") or "").strip()
    if not psid or message.get("is_echo"):
        return
    mid = message.get("mid")
    if mid:
        if mid in _SEEN_MIDS:
            return
        _SEEN_MIDS.append(mid)

    # Quick-reply taps carry the canonical payload; typed text is the fallback.
    text = ((message.get("quick_reply") or {}).get("payload")
            or message.get("text") or "").strip()
    if not text:
        return  # attachments, likes, read receipts — nothing to answer yet

    if _first_contact(psid):
        _send_message(psid, {"text": DISCLOSURE})

    pseudo_id = hash_psid(psid)
    reply = _call_chat({
        "message": text[:2000],
        "user_id": pseudo_id,
        "session_id": pseudo_id,
        "device_class": "messenger",
    })
    for out in to_messenger_messages(reply):
        _send_message(psid, out)
