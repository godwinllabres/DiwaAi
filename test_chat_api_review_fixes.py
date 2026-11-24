"""Public /chat payload regressions for the 2026-11-24 review of the
conversation-audit patch.

Run: LLM_PROVIDER=none python test_chat_api_review_fixes.py

  - a dean answer is tagged source "dean_lookup", never "fallback" (the web
    app appends "I may have missed your question" to fallback replies);
  - "where is the CEIT dean's office?" goes past the dean lookup to directions;
  - two chat tabs of one user keep their own campus: "there" in the Trece tab
    is Trece even after Imus was named in the other tab.

Drives the real FastAPI app (and the real model artifacts) through
TestClient; only the LLM and the grounding fetch are stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LLM_PROVIDER", "none")
# Keep the live-service bridges out of the turn.
os.environ["CONNECTORS_MCP_ENABLED"] = "0"
os.environ["AIS_MCP_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

import api.app as app_module  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f": {detail}"))
    if not cond:
        FAILURES.append(name)


client = TestClient(app_module.app)


def ask(message, session_id, user_id="review-user"):
    res = client.post("/chat", json={"message": message, "session_id": session_id,
                                     "user_id": user_id})
    assert res.status_code == 200, (res.status_code, res.text[:300])
    return res.json()


# ── dean answers are not "fallback" ──────────────────────────────────────
body = ask("who is the dean of CEIT?", "rv-dean")
check("dean reply source is dean_lookup", body["source"] == "dean_lookup", body["source"])
check("dean reply names the CEIT dean", "CEIT" in body["text"] and "dean" in body["text"].lower(),
      body["text"][:200])
check("_classify_source maps Dean Lookup",
      app_module._classify_source("Dean Lookup")[0] == app_module.ResponseSource.DEAN_LOOKUP)

body = ask("who is the dean of hospitality management?", "rv-dean2")
check("hospitality management → CTHM, not CEMDS too",
      body["source"] == "dean_lookup" and "CTHM" in body["text"], (body["source"], body["text"][:200]))

# ── office/location asks skip the dean lookup ────────────────────────────
body = ask("where is the CEIT dean's office?", "rv-office")
check("dean's office question is not a dean lookup", body["source"] != "dean_lookup",
      (body["source"], body["text"][:200]))

# ── per-tab campus context ───────────────────────────────────────────────
class FakeLLM:
    def __init__(self):
        self.calls = []

    def ensure_available(self):
        return True

    def generate(self, msg, conversation_context=None):
        self.calls.append(msg)
        return "Here is what I know about that campus's programs."


bot = app_module.chatbot
saved = (bot.llm, bot.llm_provider, bot._gather_grounding)
gathered = []


def fake_gather(query, campus=None, scope_text=None):
    gathered.append((query, campus))
    return [], "", None


try:
    bot.llm, bot.llm_provider = FakeLLM(), "ollama"
    bot._gather_grounding = fake_gather
    ask("what about trece?", "rv-tabA")
    ask("what about imus?", "rv-tabB")
    ask("what programs are offered there?", "rv-tabA")
    query, campus = gathered[-1] if gathered else ("", "")
    check("tab A follow-up filters on Trece", campus and "Trece" in campus, gathered[-1:])
    check("tab A follow-up searches Trece, not Imus", "Trece" in query and "Imus" not in query,
          gathered[-1:])
    check("tab A prompt resolves 'there' to Trece",
          bot.llm.calls and '"there" = Trece' in bot.llm.calls[-1], bot.llm.calls[-1:])
finally:
    bot.llm, bot.llm_provider = saved[0], saved[1]
    bot._gather_grounding = saved[2]

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
    raise SystemExit(1)
print("\nAll /chat review-fix checks passed.")
