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

    print("\n" + ("ALL PASS" if not _failures else f"FAILURES: {_failures}"))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
