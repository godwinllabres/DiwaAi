"""Regression tests — admin session cookie + server-bound AIS session.

Two credentials used to sit in JavaScript-readable storage and ride every
request:

  * the admin PIN — kept in sessionStorage and re-sent as X-Admin-Pin, so one
    XSS on the admin origin lifted the shared secret itself;
  * the AIS session id — chosen by the client and reused from the chat session,
    while being a bearer for the user's OAuth token (it authorizes /ais/write
    to approve, post, or cancel a disbursement voucher as them).

Both are now httpOnly cookies the page cannot read. These tests pin that down.

Run:  DASHBOARD_PIN=test-pin python test_admin_session_cookie.py
"""
import os
import sys

os.environ.setdefault("DASHBOARD_PIN", "test-pin")
os.environ.setdefault("ADMIN_COOKIE_SECURE", "0")  # TestClient speaks http://

from fastapi.testclient import TestClient  # noqa: E402

import api.app as app_module  # noqa: E402

_failures = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _failures.append(name)


def main() -> int:
    client = TestClient(app_module.app)
    app_module._pin_attempts.clear()

    # ── the PIN still works for scripts (CI, curl, training) ────────────────
    check("X-Admin-Pin header still authenticates (script path)",
          client.get("/admin/status", headers={"X-Admin-Pin": "test-pin"}).status_code == 200)
    app_module._pin_attempts.clear()
    check("no credential -> 401", client.get("/admin/status").status_code == 401)
    app_module._pin_attempts.clear()

    # ── unlock exchanges the PIN for a cookie ───────────────────────────────
    bad = client.post("/admin/verify", json={"pin": "wrong"})
    check("wrong PIN rejected", bad.status_code == 401)
    check("no cookie set on a failed unlock",
          app_module._ADMIN_COOKIE not in bad.cookies)
    app_module._pin_attempts.clear()

    ok = client.post("/admin/verify", json={"pin": "test-pin"})
    check("correct PIN accepted", ok.status_code == 200)
    set_cookie = ok.headers.get("set-cookie", "")
    check("session cookie is issued", app_module._ADMIN_COOKIE in set_cookie)
    check("cookie is HttpOnly (unreadable from JS)", "httponly" in set_cookie.lower())
    check("cookie is SameSite=Strict (not sent cross-site)",
          "samesite=strict" in set_cookie.lower().replace(" ", ""))
    check("PIN is not echoed back in the response body",
          "test-pin" not in ok.text)

    # ── the cookie now authenticates, with NO PIN header ────────────────────
    check("cookie alone authenticates an admin route",
          client.get("/admin/status").status_code == 200)
    check("cookie works on a second, unrelated admin route",
          client.get("/logs/today").status_code == 200)

    # A valid session must not spend the PIN brute-force budget, or a polling
    # dashboard would lock itself out.
    app_module._pin_attempts.clear()
    for _ in range(8):
        client.get("/admin/status")
    check("valid cookie never consumes the PIN attempt budget",
          not app_module._pin_attempts)

    # ── logout revokes server-side, not just locally ────────────────────────
    token = next(iter(app_module._admin_sessions), None)
    check("session is tracked server-side", token is not None)
    check("logout succeeds", client.post("/admin/logout").status_code == 200)
    check("token is revoked server-side (not just cleared in the browser)",
          not app_module._admin_session_valid(token or ""))
    client.cookies.clear()
    app_module._pin_attempts.clear()
    check("after logout the cookie no longer authenticates",
          client.get("/admin/status").status_code == 401)

    # ── a forged/expired token is refused ───────────────────────────────────
    app_module._pin_attempts.clear()
    client.cookies.set(app_module._ADMIN_COOKIE, "forged-token-value")
    check("forged session cookie rejected",
          client.get("/admin/status").status_code == 401)
    client.cookies.clear()

    # ── AIS session is minted server-side, never client-chosen ──────────────
    import inspect
    from api import auth_ais
    sig = inspect.signature(auth_ais.login)
    check("auth_ais.login no longer accepts a caller-supplied session_id",
          "session_id" not in sig.parameters)
    check("auth_ais.login returns (sid, identity)",
          "tuple" in str(sig.return_annotation).lower())
    src = inspect.getsource(auth_ais.login)
    check("the session id is minted with secrets.token_urlsafe",
          "secrets.token_urlsafe" in src)
    check("/auth/login sets the AIS session as a cookie",
          "_AIS_COOKIE" in inspect.getsource(app_module.auth_login))
    check("/ais/write resolves the session from the cookie",
          "_ais_sid" in inspect.getsource(app_module.ais_write))

    # ── HTTP-level wire compatibility ───────────────────────────────────────
    # Signature checks alone missed that AuthLoginRequest.session_id was still
    # REQUIRED while the new client had stopped sending it — every login would
    # have 422'd. Exercise the real request shapes, old and new.
    async def fake_login(username, password):
        return "server-minted-sid", {"user": username, "full_name": "T",
                                     "roles": [], "expires_in": 3600}

    real_login = app_module._ais_auth.login
    app_module._ais_auth.login = fake_login
    try:
        new_shape = client.post("/auth/login", json={"username": "u", "password": "p"})
        check("new client shape {username,password} is accepted (no 422)",
              new_shape.status_code == 200)
        check("login sets the AIS session cookie",
              app_module._AIS_COOKIE in new_shape.headers.get("set-cookie", ""))
        check("login response body carries identity only, never the session id",
              "server-minted-sid" not in new_shape.text)

        old_shape = client.post("/auth/login", json={
            "session_id": "client-chosen", "username": "u", "password": "p"})
        check("old client shape (with session_id) still accepted during a deploy",
              old_shape.status_code == 200)
        check("a client-supplied session_id is ignored, not used as the session",
              "client-chosen" not in old_shape.headers.get("set-cookie", ""))
    finally:
        app_module._ais_auth.login = real_login
        client.cookies.clear()

    # /ais/write must not 422 merely because the body omits session_id — the
    # cookie is the authority. With no session at all it should be a clean 401.
    no_sid = client.post("/ais/write", json={
        "action": "approve_dv", "name": "DV-1", "idempotency_key": "k1"})
    check("/ais/write without session_id is 401 (auth), not 422 (schema)",
          no_sid.status_code == 401)

    print("\n" + ("ALL PASS" if not _failures else f"FAILURES: {_failures}"))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
