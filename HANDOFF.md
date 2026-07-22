# HANDOFF — Sevi security hardening (resume point) · 2026-07-23

Paste this back to resume. The unfinished work is **applying the security-review
fixes below**. When they're done + verified, **delete this file.**

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

## PENDING WORK — apply these fixes (priority order)

**P1 — input clamps + throttle (tiny, no behavior change for legit clients):**
- [ ] Clamp `limit` everywhere it reaches SQL `LIMIT ?`: `Query(ge=1, le=200)` on the
      endpoints + `limit = max(1, min(int(limit), 200))` inside each method.
      `api/logger.py`: `search_logs`(659), `get_user_history`(497), `get_session_list`(582),
      `get_feedback_entries`(878), `get_fallback_examples`(906), `get_anti_pattern_rows`(947).
      (SQLite treats `LIMIT -1` as unlimited → full-table dump / OOM.)
- [ ] Clamp `days` in `/logs/cleanup`: `Query(ge=1)` + `assert cutoff < datetime.now()`
      before the DELETE in `cleanup_old_logs` (`api/logger.py:994`, endpoint `api/app.py:2116`).
      (Negative `days` → cutoff in the future → wipes the ENTIRE chat+feedback DB + log files.)
- [ ] Rate-limit `/batch`: call `_check_chat_rate_limit` per sub-request in the loop
      (`api/app.py:1962`). (Unauthenticated 20× compute amplifier behind the global lock.)

**P2 — access control:**
- [ ] Gate `GET`+`DELETE /conversation/{user_id}` with `require_admin` (or bind to caller).
      `api/app.py:1932/1947` — currently unauthenticated IDOR (read + wipe any user's history).
- [ ] Move `secrets.compare_digest` + the `_check_rate_limit` throttle INTO `require_admin`
      (`api/app.py:318-324`) — today the PIN throttle exists only on `/admin/verify`, so
      ~30 admin routes are an unthrottled brute-force oracle. Also fixes the timing compare.
- [ ] Sanitize `user_id` in `/logs/export/{user_id}` (`api/logger.py:973`): allowlist
      `[A-Za-z0-9_.-]`, reject `..`/separators, assert resolved path stays under `log_dir`.
      (Windows `%5C` path traversal → arbitrary-dir file write.)

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
python test_safety_gate.py            # 55/55 + 11/11
python test_moderation_controls.py    # 36/36
python test_agentic_workflow.py       # 23/23
python -c "import api.app"            # imports clean
```
Add tests for the new clamps (limit/days bounds).

## Also still open (non-code)
- **Governance sign-offs** still block production: crisis copy (Guidance) + consent copy
  (DPO) — `docs/governance_signoff.md`.
- **`godwinllabres/DiwaAi` is a PUBLIC repo** (flagged; user chose to proceed).
- Agentic tier is OFF by default; needs real student auth before enabling.

## Branch state
`main` has everything merged+pushed. The three feature branches
(`feat/moderation-controls`, `feat/agentic-workflows`, `hardening/chat-input-validation`)
are all merged into `main` and can be deleted on the remote when convenient.
Suggested: do the P1/P2 fixes on a new `hardening/input-clamps` branch.
