"""Regression: the LLM reachability probe must not mistake an auth gateway for a
running model server.

Both LocalLLM._probe() and OpenAICompatLLM._probe() used to return True whenever
urlopen() did not raise. urlopen() follows redirects, so anything that fronts the
model server with a sign-in page — Cloudflare Access, an SSO proxy, a captive
portal — turns a 302 into a contented HTTP 200 and the probe reports a healthy
tier.

That is not hypothetical. On 2026-07-28 the Render deployment served
/health with llm_ready=True while this probe was landing on
https://godwincreates.cloudflareaccess.com/cdn-cgi/access/login/... (text/html,
title "Sign in - Cloudflare Access"). Every real generate() call failed and the
chat tier returned llm_unavailable. The health endpoint reported green for a
tier that was entirely down, which is the single most expensive kind of lie a
health check can tell.

No network access: everything below is served by throwaway localhost servers.
Run with `python test_llm_probe.py` (this repo's tests are scripts, not pytest —
pytest cannot even collect them, see ci.yml).
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.hybrid_chatbot import LocalLLM, OpenAICompatLLM  # noqa: E402

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


def _handler(status, content_type, body):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return H


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_address[1]


# A real Ollama: JSON carrying a "models" key.
ollama_url = serve(_handler(
    200, "application/json",
    json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode()))

# An auth gateway: HTTP 200, but an HTML sign-in page. This is what a followed
# Cloudflare Access redirect looks like to urlopen().
gateway_url = serve(_handler(
    200, "text/html",
    b"<!DOCTYPE html><html><head><title>Sign in</title></head><body></body></html>"))

# Valid JSON, but not Ollama's shape — a proxy or unrelated service answering.
wrong_json_url = serve(_handler(
    200, "application/json", json.dumps({"error": "forbidden"}).encode()))

# An OpenAI-compatible /models listing.
openai_url = serve(_handler(
    200, "application/json",
    json.dumps({"object": "list", "data": [{"id": "gpt-oss"}]}).encode()))

print("=" * 64)
print("LLM PROBE — auth gateway must not read as a healthy server")
print("=" * 64)

check("real Ollama still probes True", LocalLLM(base_url=ollama_url)._probe(), True)
check("HTML sign-in page (HTTP 200) rejected",
      LocalLLM(base_url=gateway_url)._probe(), False)
check("JSON without a 'models' key rejected",
      LocalLLM(base_url=wrong_json_url)._probe(), False)
check("unreachable server still rejected",
      LocalLLM(base_url="http://127.0.0.1:1")._probe(), False)

check("OpenAI-compat /models still probes True",
      OpenAICompatLLM(base_url=openai_url)._probe(), True)
check("OpenAI-compat rejects an HTML sign-in page",
      OpenAICompatLLM(base_url=gateway_url)._probe(), False)

print()
if FAILED:
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1)
print(f"ALL PASS | {PASSED} checks")
raise SystemExit(0)
