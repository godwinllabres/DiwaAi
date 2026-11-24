"""Regression: every local-LLM caller honours LLM_PROVIDER=openai|localai.

The main fallback tier (OpenAICompatLLM) has spoken the OpenAI wire format for a
while, but the side modules — the AIS router + glossary fallback, the connectors
router, the agentic loop and the safety second opinion — only knew Ollama. Under
LLM_PROVIDER=openai against a local llama-server (no Ollama installed) they:
  * POSTed to the always-truthy default http://localhost:11434 and got
    connection refused (routers), or
  * silently took the Anthropic branch and returned None with no key (AIS
    glossary), or
  * called Ollama's native /api/chat, which llama-server does not serve (safety).
And OpenAICompatLLM sent no thinking switch, so a qwen3-class model spent the
256-token budget reasoning and returned empty content.

These checks pin where each call goes and what it carries, for both providers.
No network access: everything is served by throwaway localhost servers.
Run with `python test_llm_openai_compat.py` (script, not pytest — see ci.yml).
"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api import agentic_loop, ais_mcp, connectors_mcp, safety  # noqa: E402
from api.hybrid_chatbot import OpenAICompatLLM  # noqa: E402
from api.llm_defaults import local_chat_target  # noqa: E402

PASSED = 0
FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"PASS  {name}: got={got}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}: got={got} want={want}")


class Recorder:
    """A localhost chat server that records every POST and answers with
    `reply` (an OpenAI chat.completion body unless a test swaps it)."""

    def __init__(self):
        self.requests = []
        self.reply = {}
        rec = self

        class H(BaseHTTPRequestHandler):
            def _send(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # /v1/models probe
                self._send({"object": "list", "data": [{"id": "served-model"}]})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                rec.requests.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(raw or b"{}"),
                })
                self._send(rec.reply)

            def log_message(self, *args):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.root = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def last(self):
        return self.requests[-1] if self.requests else {}


def completion(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def tool_call(name, args):
    return [{"id": "c1", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}]


openai_srv = Recorder()   # stands in for llama-server / LM Studio
ollama_srv = Recorder()   # stands in for Ollama's /v1 shim


def use_openai(model="gemma-4-e4b", key="sk-local"):
    os.environ.update({
        "LLM_PROVIDER": "openai",
        "OPENAI_BASE_URL": openai_srv.root + "/v1",
        "OPENAI_MODEL": model,
        "OPENAI_API_KEY": key,
        # A stale Ollama config must NOT win once the provider is openai.
        "OLLAMA_BASE_URL": ollama_srv.root,
        "OLLAMA_MODEL": "qwen3:8b",
    })


def use_ollama():
    os.environ.update({
        "LLM_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": ollama_srv.root,
        "OLLAMA_MODEL": "qwen3:8b",
    })
    for k in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY"):
        os.environ.pop(k, None)


for k in ("AIS_MCP_OLLAMA_MODEL", "CONNECTORS_MCP_OLLAMA_MODEL", "SAFETY_LLM_MODEL"):
    os.environ.pop(k, None)
ais_mcp._LLM_ROUTER_ENABLED = True
connectors_mcp._LLM_ROUTER_ENABLED = True
safety._LLM_SECOND_OPINION = True

print("=" * 64)
print("LOCAL LLM — openai|localai providers reach the OPENAI_* server")
print("=" * 64)

# ── shared target resolution ────────────────────────────────────────────────
use_openai(model="qwen3:8b")
t = local_chat_target("ignored-ollama-model")
check("openai target url", t.url, openai_srv.root + "/v1/chat/completions")
check("openai target model is OPENAI_MODEL", t.model, "qwen3:8b")
check("openai target sends the bearer key", t.headers.get("Authorization"), "Bearer sk-local")
check("openai target turns thinking off for qwen3",
      t.extra_body, {"chat_template_kwargs": {"enable_thinking": False}})
os.environ["LLM_PROVIDER"] = "localai"
check("localai resolves like openai", local_chat_target("x").url, t.url)

use_openai(model="gemma-4-e4b")
check("no thinking switch for a non-thinking model", local_chat_target("x").extra_body, {})

use_ollama()
t = local_chat_target("qwen3:8b")
check("ollama target url unchanged (/v1 shim)", t.url, ollama_srv.root + "/v1/chat/completions")
check("ollama target keeps the caller's model", t.model, "qwen3:8b")
check("ollama target adds no body fields", t.extra_body, {})

# ── main fallback tier ──────────────────────────────────────────────────────
use_openai(model="qwen3:8b")
openai_srv.reply = completion("Enrollment runs Aug 3-14.")
llm = OpenAICompatLLM()
check("OpenAICompatLLM answers", llm.generate("kailan ang enrollment?"), "Enrollment runs Aug 3-14.")
check("OpenAICompatLLM disables thinking for qwen3",
      openai_srv.last()["body"].get("chat_template_kwargs"), {"enable_thinking": False})
openai_srv.reply = completion("<think>The user asks about enrollment and I should")
check("unterminated <think> (cut by max_tokens) never reaches the user",
      llm.generate("kailan ang enrollment?"), None)
openai_srv.reply = {"choices": [{"message": {"role": "assistant", "content": None}}]}
check("null content reads as no reply, not a crash", llm.generate("hi"), None)

use_openai(model="gemma-4-e4b")
openai_srv.reply = completion("ok")
OpenAICompatLLM().generate("hi")
check("no chat_template_kwargs for a non-thinking model",
      "chat_template_kwargs" in openai_srv.last()["body"], False)

# ── AIS router + glossary fallback ──────────────────────────────────────────
use_openai()
openai_srv.reply = completion(tool_calls=tool_call("get_dv", {"name": "DV-2026-00123"}))
before = len(ollama_srv.requests)
routed = asyncio.run(ais_mcp._llm_route("ano na status ng DV-2026-00123?"))
check("AIS router routes via the openai server", routed, ("get_dv", {"name": "DV-2026-00123"}))
check("AIS router used OPENAI_MODEL", openai_srv.last()["body"]["model"], "gemma-4-e4b")
check("AIS router sent the bearer key", openai_srv.last()["auth"], "Bearer sk-local")
check("AIS router never touched Ollama", len(ollama_srv.requests), before)

openai_srv.reply = completion("A DV (Disbursement Voucher) authorises a payment.")
check("AIS glossary fallback answers under openai (was: Anthropic branch, None)",
      asyncio.run(ais_mcp._llm_glossary_reply("what is a DV?")),
      "A DV (Disbursement Voucher) authorises a payment.")
check("AIS glossary fallback hit the openai server",
      openai_srv.last()["path"], "/v1/chat/completions")

use_ollama()
ollama_srv.reply = completion(tool_calls=tool_call("get_dv", {"name": "DV-2026-00123"}))
routed = asyncio.run(ais_mcp._llm_route("ano na status ng DV-2026-00123?"))
check("AIS router on Ollama still routes", routed, ("get_dv", {"name": "DV-2026-00123"}))
check("AIS router on Ollama posts to the /v1 shim", ollama_srv.last()["path"], "/v1/chat/completions")
check("AIS router on Ollama uses OLLAMA_MODEL", ollama_srv.last()["body"]["model"], "qwen3:8b")

# ── connectors router ───────────────────────────────────────────────────────
use_openai()
openai_srv.reply = completion(tool_calls=tool_call("orps_track_ticket", {"ticket_number": "HTKT-07-00001"}))
routed = asyncio.run(connectors_mcp._llm_route("status ng ticket ko HTKT-07-00001", None))
check("connectors router routes via the openai server",
      routed, ("orps_track_ticket", {"ticket_number": "HTKT-07-00001"}))
check("connectors router used OPENAI_MODEL", openai_srv.last()["body"]["model"], "gemma-4-e4b")

# ── agentic loop ────────────────────────────────────────────────────────────
use_openai()
check("agentic loop picks the openai provider", agentic_loop._pick_provider(), "openai")
use_ollama()
check("agentic loop still picks ollama", agentic_loop._pick_provider(), "ollama")

# ── safety second opinion ───────────────────────────────────────────────────
use_openai(model="qwen3:8b")
openai_srv.reply = completion("self_harm")
before = len(ollama_srv.requests)
verdict = asyncio.run(safety.llm_second_opinion("I don't want to be here anymore"))
check("safety judge works under openai (was: Ollama /api/chat, 404)", verdict, "self_harm")
req = openai_srv.last()
check("safety judge posts OpenAI chat/completions", req["path"], "/v1/chat/completions")
check("safety judge caps the verdict at 8 tokens", req["body"].get("max_tokens"), 8)
check("safety judge turns thinking off (8 tokens would all be reasoning)",
      req["body"].get("chat_template_kwargs"), {"enable_thinking": False})
check("safety judge never touched Ollama", len(ollama_srv.requests), before)

print()
if FAILED:
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1)
print(f"ALL PASS | {PASSED} checks")
raise SystemExit(0)
