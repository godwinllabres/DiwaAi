# 🔴 HANDOFF — READ ME FIRST · Sevi security hardening · 2026-07-25

> **Paste this file back at the start of the next session.**
>
> **⚠️ TWO THINGS ARE NOT DONE AND WILL BITE YOU — see [§1 ACTION REQUIRED](#1--action-required-before-this-ships).**
> The code is committed and tested, but two settings are deliberately left
> unset, and until you set them the work is inert in production.

---

## 1 · ⚠️ ACTION REQUIRED BEFORE THIS SHIPS

### 1a. Set the trusted-proxy header, or every user shares one rate-limit bucket

**Where:** `sevi-deploy/sevi.env` (copy from `sevi.env.example`) — **not `.env`.**
Compose's `environment:` block outranks `env_file:`, so these are deliberately
kept out of `compose.yaml`; setting them in `.env` has no effect.

```bash
TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP
TRUSTED_PROXY_HOPS=1
```

**Why it matters:** the API keys its chat and admin-PIN throttles on the
caller's IP. Inside the compose stack the API's peer is the **web container** —
the same address for every visitor. Until this is set, one abusive client
rate-limits the whole university, and five bad PIN attempts by anyone locks out
every admin for five minutes.

**Why it is not already on:** trusting a forwarded header when nothing strips it
lets any caller pick their own throttle bucket. It is safe only behind the
tunnel, so it defaults to off. Set it **when running with
`COMPOSE_PROFILES=tunnel`.**

### 1b. Confirm which repos actually deploy

`sevi-deploy/.github/workflows/deploy.yml` builds from:

```
SEVIAI_REPO:  godwinllabres/DiwaAi
SEVIWEB_REPO: godwinllabres/DiwaWeb
```

…but the org repos are `Cavite-State-University-Official/sevi-{api,web}`.
**Merging a PR into the org repo does not change the built image.** Confirmed:
`DiwaAi/main` is **10 commits ahead** of the org main and `DiwaWeb/main` **6
ahead** — the personal repos are the deploy source and hold work the org repos
do not. Decide which is authoritative and make the sync one-directional, or this
will keep drifting.

---

## 2 · Where the work lives

| Repo | Branch | Contains |
|---|---|---|
| sevi-api (org) | `hardening/admin-decoupling` | 3 commits, based on org main |
| sevi-web (org) | `hardening/admin-decoupling` | 4 commits, rebased onto current org main |
| **DiwaAi** | **`sync/hardening-personal`** | **everything, merged onto your personal main — this is the deployable one** |
| **DiwaWeb** | **`sync/hardening-personal`** | **everything, merged onto your personal main** |
| sevi-deploy | `hardening/trusted-proxy-ip` | compose + `.env.example` wiring for §1a |

**Merged to `main` on DiwaAi, DiwaWeb and sevi-deploy** (clean fast-forwards).
Nothing was force-pushed. The org branches still hold only the first batch —
see §6.

---

## 3 · What changed this session

**Access control**
- Legacy root `app.py` **retired** (with `deployment/Dockerfile` and
  `deployment/docker-compose.yml`, the only things that built it). It served the
  same logger routes unauthenticated and published port 8000. Nothing imports
  it; live paths are `Dockerfile.render` / `Dockerfile.local`, both `api.app:app`.
- **Import-time admin-surface audit** in `api/app.py`: walks every route's
  dependency tree and **raises at boot** if a protected path lacks
  `require_admin`. 34 routes covered; 5 public-by-design entries listed
  explicitly in `_PUBLIC_BY_DESIGN`. Forgetting the gate now breaks the boot
  instead of shipping an open endpoint.

**Credentials out of JavaScript**
- **Admin PIN echo retired.** The PIN is presented once to `/admin/verify`,
  which returns an **httpOnly, SameSite=Strict** session cookie. It is no longer
  kept in `sessionStorage` or re-sent per request, so an XSS on the admin origin
  can no longer lift the shared secret. `X-Admin-Pin` still works for
  scripts/CI. `/admin/logout` revokes server-side.
- **AIS session bound server-side.** `auth_ais.login()` no longer accepts a
  caller-supplied `session_id`; it mints a 256-bit one and returns it **only** as
  an httpOnly cookie. Previously this value was the *chat* session id — chosen by
  the client and echoed through feedback payloads and logs — while being a bearer
  that authorizes `/ais/write` to approve/post/cancel a disbursement voucher.
- **`session_id` out of the whoami URL** → `X-Sevi-Session` header (CWE-598).

**Other**
- `intent_hint` MCP bypass made **read-only** — it could invoke
  `approve_dv`/`post_dv`/`cancel_dv` directly, skipping `/ais/write`'s confirm
  and per-user token. `WRITE_TOOLS` is now canonical in `ais_mcp`; caller args
  are stripped of `__auth_token`/`confirm`.
- **Two-tier chat throttle**: per-session (honest bursts; advisory, since the
  client picks the id) **plus** a looser per-IP ceiling (the tier a caller cannot
  rotate around, kept generous for campus NAT).
- **Admin panel decoupled** into its own Vite entry + bundle + API client
  (`app/lib/adminApi.ts`); the public chat bundle now contains zero admin code
  or admin route names. nginx serves `/admin/`.

---

## 4 · Every setting introduced

All are documented in the `.env.example` files; none are secrets.

| Variable | Where | Default | Set it when |
|---|---|---|---|
| `TRUSTED_CLIENT_IP_HEADER` | sevi-deploy `sevi.env` | *(unset)* | **behind the tunnel — see §1a** |
| `TRUSTED_PROXY_HOPS` | sevi-deploy `sevi.env` | `1` | XFF chains: position from the RIGHT |
| `CHAT_RATE_LIMIT_IP_MAX` | sevi.env | `240` | tuning the per-IP ceiling |
| `CHAT_RATE_LIMIT_MAX` | sevi.env | `30` | tuning the per-session limit |
| `ADMIN_SESSION_TTL_SECONDS` | sevi.env | `3600` | shorter/longer admin sessions |
| `COOKIE_SECURE` (alias `ADMIN_COOKIE_SECURE`) | sevi.env | `1` | **`0` for local http:// only** — governs BOTH the admin and AIS cookies |

⚠️ **Local dev gotcha:** with `ADMIN_COOKIE_SECURE=1` (the default) over plain
`http://`, the browser silently drops the admin cookie and the dashboard looks
like it never unlocks. `sevi-api/.env` sets `0` for local runs.

---

## 5 · Verify

```bash
# API — all green as of this commit
python test_input_clamps.py           # clamps + export allowlist
python test_intent_hint_guard.py      # intent_hint is read-only
python test_client_ip_throttle.py     # trusted proxy + two-tier throttle
python test_admin_session_cookie.py   # admin cookie + AIS session binding
python test_safety_gate.py
python test_moderation_controls.py
python test_agentic_workflow.py
python -c "import api.app"            # must not raise — runs the admin audit

# Web
npm run build                         # tsc -b + two entries (main + admin)
npm run demo:admin-split              # proves no admin code in the public bundle
npm test                              # 88 pass / 2 fail — SAME on origin/main
```

The 2 web failures (`useChat`, `useTypewriter`) are **pre-existing**; measured
directly on `origin/main` for comparison. Not caused by this work.

---

## 6 · Still open

- **Phase 3** — put `/admin/` behind Cloudflare Access. nginx already isolates
  the whole surface behind that one prefix, so this is ops config.
- **Org repos are behind.** `hardening/admin-decoupling` on
  `Cavite-State-University-Official/sevi-{api,web}` contains only the first
  batch (admin decoupling + whoami header). Everything after it landed on the
  personal repos, which is where the deploy builds from. Either sync org ← personal
  or retire the org branches; do not merge them expecting the full change set.

### Known limitations accepted (from the pre-merge review)
- **Cross-origin deployments keep the PIN header.** On the GitHub Pages build
  `VITE_API_URL` points at the Render API, so a cookie cannot apply
  (cross-site + `allow_credentials=False` + `SameSite=Strict`). `adminApi`
  detects this and uses `X-Admin-Pin` there — its pre-existing behaviour. The
  cookie (and the "no PIN in storage" win) applies to the same-origin
  nginx/tunnel stack, which is the deployment that matters.
- **`SameSite=Strict` and iframes.** If AIS login is ever needed from inside the
  embedded widget or an ERPNext Desk iframe, the cookie will not be sent —
  that is cross-site by definition. Moving to `SameSite=None` would require
  `Secure` plus CORS credentials; left as a deliberate decision rather than a
  guess, since no current flow needs it.
- **Fix the 2 pre-existing web test failures.**
- **Multi-worker**: the admin sessions, throttles, and AIS token cache are all
  in-memory and assume single-worker uvicorn. Multi-worker needs Redis.
- **Governance sign-offs** still block production: crisis copy (Guidance) +
  consent copy (DPO) — `docs/governance_signoff.md`.
- **`godwinllabres/DiwaAi` is a PUBLIC repo** (previously flagged; you chose to
  proceed).
- Agentic tier is OFF by default; needs real student auth before enabling.

---

## 7 · Local run

```bash
# API  (reads sevi-api/.env — DASHBOARD_PIN + ADMIN_COOKIE_SECURE=0)
python -m uvicorn api.app:app --port 8090

# Web
npm run dev            # http://localhost:5173  ·  admin: /admin
```

The dev proxy override must go in **`.env.development.local`**, not
`.env.local` — Vite ranks `.env.[mode]` above `.env.local`, so an override there
is silently ignored.
