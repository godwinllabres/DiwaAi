# HANDOFF — Sevi security hardening (resume point) · 2026-07-23

Paste this back to resume. **P1 + P2 are DONE** (branch `hardening/input-clamps`,
verified — see "Verify after fixing"). The remaining work is **P3, which needs a
decision first**. Delete this file once P3 is settled.

## Where things stand
- **All prior session work is MERGED to `main`** (SeviAi `origin/main` @ `d60e136`):
  moderation/privacy controls, agentic Tier 5.5 workflows (OFF by default, mock
  tool), and the `/chat` input validator. DiwaWeb is on `origin/main`
  (Playwright E2E files added under `DiwaWeb/tests/e2e/`, untracked — not committed).
- **Just completed a security review** of the API input-value surface (10 distinct
  confirmed findings, below). One finder dimension (agentic/dynamic-dispatch) could
  NOT complete — transient API 522s twice. Its principal risk (`intent_hint` →
  arbitrary MCP tool dispatch, P3 below) was already caught by the ingress finder, so
  incremental value is low; optionally re-run that one dimension later.

## ⚠️ Entrypoint caveat (changes several severities)
Two app entrypoints exist:
- `api/app.py` — **deploy-of-record** (`sevi-deploy`→`Dockerfile.local` / `Dockerfile.render`,
  `uvicorn api.app:app`). Here `/logs/*` ARE admin-gated.
- **legacy root `app.py`** (`uvicorn app:app`, used by `deployment/Dockerfile` +
  `docker-compose`) — exposes the same logger routes **UNAUTHENTICATED**.
Several findings are Medium on `api.app` but High if the legacy root ships.
**Retiring/auth-gating the legacy root `app.py` is the highest-leverage single fix.**

## DONE — P1 + P2 (branch `hardening/input-clamps`)

**P1 — input clamps + throttle (tiny, no behavior change for legit clients):**
- [x] Clamp `limit` everywhere it reaches SQL `LIMIT ?`. Two layers: `Query(ge=1, le=200)`
      on the endpoints (422 before the handler runs) **and** `_clamp_limit()` inside each
      `api/logger.py` method, since scripts/the exporter call them directly. Per-method
      caps, not a flat 200, so `export_user_data`(1000) and anti-pattern mining(2000)
      keep working. (SQLite treats `LIMIT -1` as unlimited → full-table dump / OOM.)
- [x] Clamp `days` in `/logs/cleanup`: `Query(ge=1, le=3650)` on the endpoint, plus a
      floor-at-1 clamp and a `cutoff < now` guard before the DELETE in `cleanup_old_logs`.
      (Negative `days` → cutoff in the future → wipes the ENTIRE chat+feedback DB + log files.)
- [x] Rate-limit `/batch`: `_check_chat_rate_limit` per sub-request, on the same
      `chat:{session_id|ip}` key as `/chat`, so both share one budget.
      Note: the throttle fires mid-loop, so a batch that trips it discards the
      sub-responses already computed. Acceptable for a throttle; revisit if noisy.

**P2 — access control:**
- [x] `GET`+`DELETE /conversation/{user_id}` now carry `require_admin`. (SeviWeb defines
      `getConversation`/`clearConversation` in `app/lib/api.ts` but calls neither, so no
      live caller breaks.)
- [x] `secrets.compare_digest` + the `_check_rate_limit` throttle moved INTO
      `require_admin` via `_pin_matches()`; `/admin/verify` uses the same helper.
      Only FAILED attempts consume the 5-per-5-min budget, so a polling dashboard
      with the right PIN is never locked out.
- [x] `user_id` allowlisted in `/logs/export/{user_id}`: `is_safe_user_id()` in
      `api/logger.py` (`[A-Za-z0-9_.-]{1,64}`, no `..`), a 400 at the endpoint, and a
      resolved-path-stays-under-`log_dir` assert before the write.

Regression test: **`python test_input_clamps.py`** (34 checks — clamp bounds, the
allowlist, and real-SQLite proof that `limit=-1` returns 1 row, that `days<=0`
deletes nothing, and that a legitimate 30-day window still purges stale rows).

## PENDING WORK

**P3 — design (bigger, discuss first):**
- [ ] Read-only allowlist for `intent_hint` dispatch (`api/ais_mcp.py:1225`) — it can name
      any MCP tool incl. writes, bypassing the fenced `/ais/write`. Requires the shared
      `X-Internal-Key`, so lower urgency, but the hatch has no `_WRITE_DENYLIST` like the NL loop.
- [ ] Retire or auth-gate the legacy root `app.py` (see caveat above).
- [ ] Bind `session_id` server-side — it's an unbound bearer capability on `/auth/whoami`
      + `/ais/write` (confused deputy; mitigated in practice by UUID minting).
- [ ] (optional) Key the chat rate limiter on client IP, not the client-chosen `session_id`.

## Verify after fixing
```
python test_input_clamps.py           # 34/34  (new — P1/P2 clamps + allowlist)
python test_safety_gate.py            # 55/55 + 11/11
python test_moderation_controls.py    # 36/36
python test_agentic_workflow.py       # 23/23
python -c "import api.app"            # imports clean
```
All five green as of the P1/P2 commit. The API-layer behaviour (401 on
`/conversation` without a PIN, 422 on out-of-range `limit`/`days`, 400 on a
traversal export id, 429 after 5 bad PINs on ANY admin route, `/batch` spending
the chat budget) was checked with a FastAPI `TestClient` — 26 checks, ad hoc,
not committed because importing `api.app` loads the models.

## Also still open (non-code)
- **Governance sign-offs** still block production: crisis copy (Guidance) + consent copy
  (DPO) — `docs/governance_signoff.md`.
- **`godwinllabres/DiwaAi` is a PUBLIC repo** (flagged; user chose to proceed).
- Agentic tier is OFF by default; needs real student auth before enabling.

## Branch state
`main` has everything merged+pushed. The three feature branches
(`feat/moderation-controls`, `feat/agentic-workflows`, `hardening/chat-input-validation`)
are all merged into `main` and can be deleted on the remote when convenient.
The P1/P2 fixes live on `hardening/input-clamps`, not yet merged to `main`.

Note for P3: the legacy root `app.py` imports the SAME `api.logger.ChatLogger`,
so it already inherits the P1 limit clamps, the retention floor, and the export
allowlist. What it still lacks is the auth layer — its logger routes and
`/conversation` remain unauthenticated there.
