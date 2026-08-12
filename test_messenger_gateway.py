"""Regression tests — Messenger channel gateway (api/channels/messenger.py).

Everything runs offline: the two transport seams (_call_chat → POST /chat,
_send_message → Graph API) are stubbed, so these tests pin the gateway's
security and mapping contracts without a server, a page token, or Meta:

  * webhook GET handshake accepts only the configured verify token
  * webhook POST rejects bodies whose X-Hub-Signature-256 doesn't verify
  * PSIDs are pseudonymized before they reach the chat pipeline
  * first contact per PSID gets the AI-disclosure message (NPC Advisory
    2024-04), and only the first
  * redelivered message ids are processed once; echo events are ignored
  * ChatResponse → Messenger mapping respects platform limits (2000-char
    texts, 13 quick replies, 20-char titles) and skips card kinds it
    doesn't understand

Run:  python test_messenger_gateway.py
"""
import hashlib
import hmac
import json
import os
import sys

os.environ.setdefault("MESSENGER_ENABLED", "1")
os.environ.setdefault("MESSENGER_VERIFY_TOKEN", "sevi-verify")
os.environ.setdefault("MESSENGER_APP_SECRET", "test-app-secret")
os.environ.setdefault("MESSENGER_PAGE_TOKEN", "test-page-token")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.channels import messenger as mg

_failures = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _failures.append(name)


def _sign(body: bytes) -> str:
    secret = os.environ["MESSENGER_APP_SECRET"].encode("utf-8")
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _event(psid="psid-123", text="magkano tuition?", mid="m-1", **extra):
    message = {"mid": mid, "text": text, **extra}
    return {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": psid}, "message": message}
    ]}]}


def main() -> int:
    app = FastAPI()
    app.include_router(mg.router)
    client = TestClient(app)

    chat_calls, sent = [], []

    def fake_chat(payload):
        chat_calls.append(payload)
        return {
            "text": "Under RA 10931, tuition is free for qualified undergrads.",
            "intent": "tuition_fees",
            "source": "naive_bayes",
            "suggestions": ["Scholarships", "Enrollment steps"],
            "cards": [],
            "sources": [{"kind": "site", "locator": "https://cvsu.edu.ph/x",
                         "citation": "Official CvSU site", "url": "https://cvsu.edu.ph/x"}],
        }

    def fake_send(psid, message):
        sent.append((psid, message))

    real_chat, real_send = mg._call_chat, mg._send_message
    mg._call_chat, mg._send_message = fake_chat, fake_send
    try:
        # ── GET handshake ───────────────────────────────────────────────
        r = client.get("/channels/messenger/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "sevi-verify",
            "hub.challenge": "12345"})
        check("handshake echoes the challenge for the right token",
              r.status_code == 200 and r.text == "12345")
        r = client.get("/channels/messenger/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "wrong",
            "hub.challenge": "12345"})
        check("handshake rejects a wrong verify token", r.status_code == 403)

        # ── Signature gate ──────────────────────────────────────────────
        body = json.dumps(_event()).encode("utf-8")
        r = client.post("/channels/messenger/webhook", content=body,
                        headers={"X-Hub-Signature-256": "sha256=deadbeef"})
        check("bad signature → 403, nothing processed",
              r.status_code == 403 and not chat_calls and not sent)
        r = client.post("/channels/messenger/webhook", content=body)
        check("missing signature → 403", r.status_code == 403)

        # ── Happy path: first contact ───────────────────────────────────
        r = client.post("/channels/messenger/webhook", content=body,
                        headers={"X-Hub-Signature-256": _sign(body)})
        check("valid signature → 200 EVENTS_RECEIVED",
              r.status_code == 200 and r.text == "EVENTS_RECEIVED")
        check("chat pipeline was called once", len(chat_calls) == 1)
        check("PSID is pseudonymized before the pipeline (msgr-…, not the raw id)",
              chat_calls and chat_calls[0]["user_id"].startswith("msgr-")
              and "psid-123" not in chat_calls[0]["user_id"])
        check("turn is tagged with the messenger device_class",
              chat_calls and chat_calls[0].get("device_class") == "messenger")
        check("first contact leads with the AI disclosure",
              sent and sent[0][1].get("text") == mg.DISCLOSURE)
        check("reply text follows the disclosure",
              len(sent) >= 2 and "RA 10931" in (sent[1][1].get("text") or ""))
        check("replies address the RAW psid (delivery needs the real id)",
              sent and all(p == "psid-123" for p, _ in sent))
        last = sent[-1][1]
        check("quick replies ride the last message",
              [q["title"] for q in last.get("quick_replies", [])]
              == ["Scholarships", "Enrollment steps"])
        check("sources are rendered as an official-sources message",
              any("Official sources" in (m.get("text") or "") for _, m in sent))

        # ── Second contact: no duplicate disclosure ─────────────────────
        sent.clear()
        body2 = json.dumps(_event(text="library hours?", mid="m-2")).encode("utf-8")
        client.post("/channels/messenger/webhook", content=body2,
                    headers={"X-Hub-Signature-256": _sign(body2)})
        check("disclosure is sent only on first contact",
              sent and sent[0][1].get("text") != mg.DISCLOSURE)

        # ── Redelivery dedupe + echo skip ───────────────────────────────
        n = len(chat_calls)
        client.post("/channels/messenger/webhook", content=body2,
                    headers={"X-Hub-Signature-256": _sign(body2)})
        check("redelivered mid is processed once", len(chat_calls) == n)
        echo = json.dumps(_event(mid="m-3", is_echo=True)).encode("utf-8")
        client.post("/channels/messenger/webhook", content=echo,
                    headers={"X-Hub-Signature-256": _sign(echo)})
        check("echo events are ignored", len(chat_calls) == n)

        # ── Quick-reply taps use the canonical payload ──────────────────
        tap = json.dumps(_event(text="Scholarships", mid="m-4",
                                quick_reply={"payload": "scholarship requirements"})
                         ).encode("utf-8")
        client.post("/channels/messenger/webhook", content=tap,
                    headers={"X-Hub-Signature-256": _sign(tap)})
        check("quick-reply tap sends the payload, not the label",
              chat_calls[-1]["message"] == "scholarship requirements")
    finally:
        mg._call_chat, mg._send_message = real_chat, real_send

    # ── Pure mapping contracts ──────────────────────────────────────────
    msgs = mg.to_messenger_messages({"text": "word " * 900})  # ~4500 chars
    check("long replies are chunked under the 2000-char ceiling",
          len(msgs) >= 3 and all(len(m["text"]) <= 2000 for m in msgs))

    msgs = mg.to_messenger_messages({
        "text": "ok",
        "suggestions": [f"suggestion number {i} that is quite long" for i in range(20)],
    })
    qrs = msgs[-1]["quick_replies"]
    check("quick replies are capped at 13", len(qrs) == 13)
    check("quick-reply titles are capped at 20 chars",
          all(len(q["title"]) <= 20 for q in qrs))
    check("only the last message carries quick replies",
          all("quick_replies" not in m for m in msgs[:-1]))

    msgs = mg.to_messenger_messages({
        "text": "See the registrar.",
        "cards": [
            {"kind": "directory", "office": "Office of the Registrar",
             "email": "registrarmain@cvsu.edu.ph", "hours": "8:00–5:00"},
            {"kind": "map", "place_id": "registrar", "label": "Registrar"},
            {"kind": "hologram", "zap": True},   # future card kind
        ],
    })
    joined = "\n".join(m.get("text") or "" for m in msgs)
    check("directory cards render office + email",
          "Office of the Registrar" in joined and "registrarmain@cvsu.edu.ph" in joined)
    check("map cards link to the web app", "sevi.cvsu.edu.ph" in joined)
    check("unknown card kinds are skipped, not fatal", "hologram" not in joined)

    check("empty replies still send something", mg.to_messenger_messages({}) != [])

    h1, h2 = mg.hash_psid("abc"), mg.hash_psid("abc")
    check("hash_psid is deterministic and prefixed", h1 == h2 and h1.startswith("msgr-"))
    check("hash_psid separates senders", mg.hash_psid("abc") != mg.hash_psid("abd"))

    check("verify_signature: absent secret or header never verifies",
          not mg.verify_signature("", b"x", "sha256=aa")
          and not mg.verify_signature("s", b"x", None)
          and not mg.verify_signature("s", b"x", "md5=aa"))

    print()
    if _failures:
        print(f"{len(_failures)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
