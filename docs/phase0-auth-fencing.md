# Phase 0 — Auth-fencing the internal surface

**Goal:** internal capabilities (AIS finance, HR/DTR) must be reachable **only** by an
authenticated internal user — never by the anonymous public bot. This is the gate that lets the
capabilities graduate off `localhost` onto a shared URL.

## What's built (fail-closed gate) ✅

`api/app.py :: _internal_identity(http_request) -> Optional[str]` resolves the authenticated
internal user, or `None`. The AIS and HR `/chat` short-circuits run **only** when it returns a user;
anonymous callers get `None` and fall through to the student NLU. So even with the connectors
enabled, the public surface stays student-only.

Proven:

| Caller | Result |
|---|---|
| anonymous (`mode=off`, no headers) | `None` → internal tools skipped |
| `mode=demo` (local) | `SEVI_DEMO_USER` |
| valid `X-Internal-Key` + `X-Sevi-User` | that user |
| wrong `X-Internal-Key` (constant-time compare) | `None` |

### Modes (`SEVI_INTERNAL_AUTH_MODE`)
- **`off`** (default, dev/staging/prod) — no built-in identity source; internal tools never fire
  unless the trusted-proxy headers below are present. **Fail-closed.**
- **`demo`** — LOCAL ONLY. Returns `SEVI_DEMO_USER` for every request so a local stack works
  without real auth. Never set outside `local`.

### Trusted-proxy path (production mechanism, any mode)
An auth proxy that has **already validated the Frappe/Desk session** sits in front of Sevi and injects:
- `X-Internal-Key` — a shared secret, constant-time compared to `INTERNAL_KEY`.
- `X-Sevi-User` — the authenticated username.

Sevi never sees the user's password. Requests without valid headers (i.e. anyone reaching Sevi
directly, bypassing the proxy) are anonymous → denied.

## What remains (the decisions + deeper Phase-0 work)

### 1. Identity mechanism — DECIDED: **B, Desk-minted signed JWT** ✅
The widget runs cross-origin (an iframe on Sevi's origin), so a forwarded cookie (C) or
proxy-injected headers (A) don't cleanly reach the browser's requests to Sevi. A JWT passed
explicitly survives cross-origin, is short-lived, and is **stateless to verify** (no per-request
Frappe round-trip). Sevi-side verification is **built and proven** (`_jwt_identity`, HS256,
`aud=sevi`, fail-closed on expired/bad-sig/wrong-aud). The trusted-proxy path (A) remains as a
fallback if `INTERNAL_KEY` is set.

**Remaining: the Frappe (`cvsu_web`) minting endpoint + widget token flow.**

```python
# cvsu_web/cvsu_web/api.py  — whitelisted; the Desk session authenticates the caller
import jwt, time, frappe
@frappe.whitelist()                        # logged-in Desk users only
def sevi_token():
    secret = frappe.conf.get("sevi_jwt_secret")   # SAME as Sevi's SEVI_JWT_SECRET (>=32 bytes)
    now = int(time.time())
    return {"token": jwt.encode(
        {"sub": frappe.session.user, "aud": "sevi", "iat": now, "exp": now + 300},
        secret, algorithm="HS256")}
```

Widget flow (extend the `cvsu_web` injector, `docs/embed/cvsu_web`):
1. On the Desk page, `frappe.call("cvsu_web.api.sevi_token")` → get a 5-min token.
2. Hand it to the Sevi widget; the widget sends it on `/api/chat` as `Authorization: Bearer <jwt>`.
3. Refresh the token before expiry (re-call on 401 / every ~4 min).

Sevi then resolves `sub` → the user, and (item 2) scopes AIS/HR to that user.

### 2. Per-user scoping (reads + writes under the user's own token)
The gate proves *who* the user is; the AIS/HR servers must then apply that user's permissions:
- Thread the per-user token (reuse `api/auth_ais.py`) onto the AIS **read** path (today reads use a
  shared identity — so `list_pending_dvs` returns the bot's queue, not the user's).
- Map `X-Sevi-User` → the HR employee record server-side (the stub uses a demo id; prod derives it
  from the token). Never trust a free-text employee id.

### 3. Direct-network hardening
`SEVI_INTERNAL_AUTH_MODE=off` means the ONLY way in is the trusted proxy headers. Ensure Sevi's api
is **not** directly reachable (private network / the proxy is the sole ingress), or option A's
header trust can be spoofed.

### 4. Also decide: connectors + writes
- **Connectors MCP** (helpdesk/course catalog) is currently *not* behind the fence — decide whether
  helpdesk tickets are personal enough to fence too.
- **Writes** (`/ais/write`) already require the per-user OAuth token; keep that, and keep the write
  kill-switch escalation (off → pilot → governed) from the env tiers.

## Definition of done (Phase 0)
- [x] Fail-closed `_internal_identity` gate; AIS + HR fire only for an authenticated user.
- [x] Per-env auth mode (`demo` local, `jwt` staging/prod).
- [x] Identity mechanism chosen — **B (Desk-minted JWT)**; Sevi-side verify built + proven.
- [ ] Frappe `cvsu_web.api.sevi_token` minting endpoint + widget token flow wired.
- [ ] Per-user token on the AIS read path; `sub` → employee mapping for HR.
- [ ] Sevi api not directly reachable (proxy is sole ingress).
- [ ] PII masking before logs/prompts; append-only chat/audit retention.
- [ ] Verified the Frappe AIS/HR apps enforce permlevel / User Permission / SoD server-side.
