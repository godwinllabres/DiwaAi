"""Smoke-test the running local apps and Mac tunnel without printing the admin PIN."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import http.cookiejar
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "sideproject-llm:latest"
INSTALLED = {DEFAULT_MODEL, "qwen3:4b-instruct"}
REPORT = {"created_at": datetime.now(timezone.utc).isoformat(), "checks": []}


def request(base, path, body=None, pin=None, timeout=90):
    headers = {"Content-Type": "application/json"}
    if pin:
        headers["X-Admin-Pin"] = pin
    req = urllib.request.Request(base + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw.decode()
            return response.status, data, round(time.monotonic() - start, 3)
    except urllib.error.HTTPError as error:
        return error.code, {}, round(time.monotonic() - start, 3)


def check(name, passed, **details):
    REPORT["checks"].append({"name": name, "passed": bool(passed), **details})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {json.dumps(details)}", flush=True)


def logged_version(root, message_id):
    with closing(sqlite3.connect(root / "logs/chat_history.db")) as db:
        row = db.execute("SELECT mv.version_label FROM chat_messages cm "
                         "JOIN model_version mv ON mv.id=cm.model_version_id WHERE cm.id=?",
                         (message_id,)).fetchone()
        return row[0] if row else None


def cookie_smoke(name, web, pin):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, body=None):
        req = urllib.request.Request(web + path, headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with opener.open(req, timeout=15) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    check(name + " PIN login", call("/api/admin/verify", {"pin": pin}) == 200)
    check(name + " cookie admin access", call("/api/admin/llm") == 200)
    check(name + " admin logout", call("/api/admin/logout", {}) == 200)
    check(name + " revoked cookie", call("/api/admin/llm") == 401)


def chat_and_health(name, base, root, message, expected_model):
    body = {"message": message, "session_id": "config-smoke-" + uuid.uuid4().hex}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request, base, "/chat", body)
        time.sleep(0.5)
        status, _, elapsed = request(base, "/health", timeout=2)
        check(name + " health during chat", status == 200 and elapsed < 1,
              status=status, elapsed_seconds=elapsed, chat_pending=not future.done())
        status, answer, elapsed = future.result(timeout=100)
    source = answer.get("source")
    expected_source = "llm" if name.startswith("nova") else "llm_local"
    check(name + " LLM answer", status == 200 and source == expected_source and bool(answer.get("text")),
          status=status, source=source, elapsed_seconds=elapsed)
    version = logged_version(root, answer.get("message_id"))
    check(name + " logged model", version == expected_model, version=version)


def smoke_app(name, port, web_port, root):
    base = f"http://127.0.0.1:{port}"
    web = f"http://127.0.0.1:{web_port}"
    pin = (root / "logs/local-admin-pin.txt").read_text().strip()
    status, _, elapsed = request(base, "/health")
    check(name + " health", status == 200, status=status, elapsed_seconds=elapsed)
    status, config, _ = request(base, "/admin/llm", pin=pin)
    check(name + " model discovery", status == 200 and config.get("available")
          and config.get("model") == DEFAULT_MODEL
          and set(config.get("ollama_models", [])) == INSTALLED
          and set(config.get("openai_models", [])) == INSTALLED, status=status, model=config.get("model"))
    for route, body in [("/admin/llm", None), ("/admin/llm", {"provider": "none"})]:
        status, _, _ = request(base, route, body)
        check(name + " admin auth " + ("GET" if body is None else "POST"), status == 401, status=status)
    status, _, _ = request(base, "/chat", {"message": " "})
    check(name + " empty chat", status == 400, status=status)
    curated = "Tell me about the Sevi Web project." if name == "nova" else "hello"
    status, answer, _ = request(base, "/chat", {"message": curated, "session_id": uuid.uuid4().hex})
    check(name + " curated reply", status == 200 and answer.get("source") != "llm" and bool(answer.get("text")),
          status=status, source=answer.get("source"), intent=answer.get("intent"))
    status, details, _ = request(base, "/admin/status", pin=pin)
    check(name + " admin status", status == 200 and details.get("llm", {}).get("available"), status=status)
    if name == "nova":
        check("nova portfolio intents", details.get("total_intents") == 75, total_intents=details.get("total_intents"))
    for route in ["/", "/api/health", "/api/admin/llm"]:
        status, _, _ = request(web, route, pin=pin)
        check(name + " frontend " + route, status == 200, status=status)
    cookie_smoke(name, web, pin)
    llm_message = "Explain why rainbows appear after rain." if name == "nova" else (
        "What is the difference between a transcript of records and a certificate of good moral character?")
    chat_and_health(name, base, root, llm_message, DEFAULT_MODEL)
    try:
        status, config, _ = request(base, "/admin/llm",
                                   {"provider": "ollama", "model": "qwen3:4b-instruct"}, pin)
        check(name + " native model switch", status == 200 and config.get("available")
              and config.get("model") == "qwen3:4b-instruct", status=status)
        chat_and_health(name + " native", base, root, llm_message, "qwen3:4b-instruct")
    finally:
        status, config, _ = request(base, "/admin/llm",
                                   {"provider": "openai", "model": DEFAULT_MODEL}, pin)
        check(name + " default restored", status == 200 and config.get("available")
              and config.get("model") == DEFAULT_MODEL, status=status)


def main():
    status, models, _ = request("http://127.0.0.1:11435", "/v1/models")
    actual = {m["id"] for m in models.get("data", [])}
    check("Mac tunnel model list", status == 200 and actual == INSTALLED, models=sorted(actual))
    for name, port, web_port, root in [
        ("nova", 8009, 5180, ROOT.parent.parent / "nova-api"),
        ("sevi", 8010, 5181, ROOT),
    ]:
        try:
            smoke_app(name, port, web_port, root)
        except Exception as error:
            check(name + " smoke exception", False, error_type=type(error).__name__, error=str(error))
    REPORT["passed"] = sum(c["passed"] for c in REPORT["checks"])
    REPORT["failed"] = len(REPORT["checks"]) - REPORT["passed"]
    target = ROOT / "reports/mac-model-smoke.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(REPORT, indent=2) + "\n", encoding="utf-8")
    print(f"RESULT: {REPORT['passed']} passed, {REPORT['failed']} failed", flush=True)
    raise SystemExit(bool(REPORT["failed"]))


if __name__ == "__main__":
    main()
