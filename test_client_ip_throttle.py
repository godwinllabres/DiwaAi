"""Regression tests — trusted-proxy client IP + the two-tier chat throttle.

Behind the production ingress (Cloudflare tunnel -> nginx -> api) the peer
address is the nginx container, identical for everyone. Keying throttles on it
would put the whole university in one bucket. Reading a forwarded header fixes
that, but only if a caller cannot simply send their own.

Run:  python test_client_ip_throttle.py
"""
import importlib
import os
import sys

_failures = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _failures.append(name)


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Minimal stand-in — _client_ip only touches .client and .headers."""

    def __init__(self, peer="10.0.0.9", headers=None):
        self.client = _FakeClient(peer) if peer else None
        self.headers = headers or {}


def load_app(**env):
    """Re-import api.app with the given env so module-level config is re-read."""
    for key in ("TRUSTED_CLIENT_IP_HEADER", "TRUSTED_PROXY_HOPS"):
        os.environ.pop(key, None)
    os.environ.update({k: str(v) for k, v in env.items()})
    os.environ.setdefault("DASHBOARD_PIN", "test-pin")
    sys.modules.pop("api.app", None)
    return importlib.import_module("api.app")


def main() -> int:
    # ── default: trust nothing ──────────────────────────────────────────────
    m = load_app()
    req = _FakeRequest(peer="10.0.0.9", headers={
        "X-Forwarded-For": "1.2.3.4",
        "CF-Connecting-IP": "5.6.7.8",
    })
    check("default ignores forwarded headers (spoof-proof)",
          m._client_ip(req) == "10.0.0.9")

    # ── Cloudflare mode: single-valued header ───────────────────────────────
    m = load_app(TRUSTED_CLIENT_IP_HEADER="CF-Connecting-IP", TRUSTED_PROXY_HOPS=1)
    check("CF-Connecting-IP is used when configured",
          m._client_ip(_FakeRequest(headers={"CF-Connecting-IP": "203.0.113.7"})) == "203.0.113.7")
    check("falls back to peer when the trusted header is absent",
          m._client_ip(_FakeRequest(peer="10.0.0.9", headers={})) == "10.0.0.9")
    check("garbage header value falls back to peer (no junk throttle keys)",
          m._client_ip(_FakeRequest(peer="10.0.0.9",
                                    headers={"CF-Connecting-IP": "not-an-ip"})) == "10.0.0.9")

    # ── X-Forwarded-For: count from the RIGHT ───────────────────────────────
    # This stack: nginx appends the cloudflared peer, so the chain the API sees
    # is "<real client>, <cloudflared>" and the client sits at position 2.
    m = load_app(TRUSTED_CLIENT_IP_HEADER="X-Forwarded-For", TRUSTED_PROXY_HOPS=2)
    xff = _FakeRequest(headers={"X-Forwarded-For": "203.0.113.7, 172.18.0.4"})
    check("XFF hops=2 picks the real client, not the internal hop",
          m._client_ip(xff) == "203.0.113.7")

    # A client prepending its own entry must not be able to choose its bucket:
    # everything we append lands to the RIGHT of whatever they sent.
    spoofed = _FakeRequest(headers={
        "X-Forwarded-For": "9.9.9.9, 203.0.113.7, 172.18.0.4"})
    check("client-prepended XFF entry is ignored (still resolves real client)",
          m._client_ip(spoofed) == "203.0.113.7")

    check("port suffix stripped from a forwarded address",
          m._client_ip(_FakeRequest(headers={
              "X-Forwarded-For": "203.0.113.7:51234, 172.18.0.4"})) == "203.0.113.7")

    # ── two-tier chat throttle ──────────────────────────────────────────────
    m = load_app(TRUSTED_CLIENT_IP_HEADER="CF-Connecting-IP", TRUSTED_PROXY_HOPS=1,
                 CHAT_RATE_LIMIT_MAX=3, CHAT_RATE_LIMIT_IP_MAX=5)
    caller = _FakeRequest(headers={"CF-Connecting-IP": "203.0.113.50"})

    # Same session: stopped at the per-session ceiling.
    allowed = 0
    for _ in range(10):
        try:
            m._enforce_chat_limits("sid-fixed", caller)
            allowed += 1
        except Exception:
            break
    check(f"per-session ceiling enforced (3 allowed, got {allowed})", allowed == 3)

    # Rotating session_id: the per-IP ceiling must still stop it. Without the
    # IP tier this loop would run forever — that was the original hole.
    m._chat_hits.clear()
    allowed = 0
    for i in range(50):
        try:
            m._enforce_chat_limits(f"sid-rotating-{i}", caller)
            allowed += 1
        except Exception:
            break
    check(f"rotating session_id cannot evade — IP ceiling holds (5 allowed, got {allowed})",
          allowed == 5)

    # Distinct IPs must not share a bucket (the NAT/shared-proxy failure).
    m._chat_hits.clear()
    ok = True
    for n in range(3):
        other = _FakeRequest(headers={"CF-Connecting-IP": f"198.51.100.{n}"})
        try:
            m._enforce_chat_limits(f"sid-{n}", other)
        except Exception:
            ok = False
    check("separate client IPs get separate buckets", ok)

    print("\n" + ("ALL PASS" if not _failures else f"FAILURES: {_failures}"))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
