# Chat-History Store: SQLite → PostgreSQL

As of 2026-07-13 the chat-history/feedback store (`logs/chat_history.db`)
supports two backends behind one env var. The intents/campus-map database
(`data/cavsu_intents.db`) **stays on SQLite by design** — see "What was NOT
migrated" below.

| | |
|---|---|
| Backend switch | `DATABASE_URL` env var (unset → SQLite, `postgresql://…` → Postgres) |
| Code touched | `api/logger.py`, `api/model_registry.py`, `scripts/continuous_training.py` |
| Untouched | `api/app.py`, `app.py` (they only call `ChatLogger` methods) |
| Data migration | `scripts/migrate_chat_history_to_postgres.py` (one-time, verified copy) |
| Driver | `psycopg[binary]==3.3.4` (needed only when `DATABASE_URL` is set) |

## Why Postgres (and not MariaDB/MySQL)

The chat log is the write-heavy, multi-reader, ever-growing part of the
system (206 MB / 205k messages / 45k feedback rows at migration time), and it
had outgrown SQLite:

- **Concurrency.** SQLite allows one writer per file; the deployment compose
  runs `--workers 4` and `Dockerfile.local` had to pin `--workers 1` partly
  because of shared file state. Postgres MVCC removes the constraint.
- **File-lock pain.** The dashboard, API, training scripts, and admin tools
  all open the same file; on Windows this produced `WinError 32` locks.
- **Render.** `render.yaml` deploys there, and Render offers managed
  PostgreSQL that injects `DATABASE_URL` — the exact seam this code uses.
  Render has no managed MySQL.
- **Headroom.** JSONB (future v2-envelope logging), `tsvector` full-text
  search for `/logs/search`, partial indexes, `pg_stat_statements`.

MariaDB/MySQL would also have worked (the team already runs MariaDB for the
Frappe/AIS stack), and would centralize DB ops knowledge — but it loses the
Render integration, has weaker JSON indexing, and psycopg3's clean qmark→%s
mapping made the port smaller. Full tradeoff notes in the migration PR text.

## Runbook

### Local / dev (SQLite — nothing to do)
Leave `DATABASE_URL` unset. Everything behaves exactly as before.

> **Quiesce first.** Stop the API (or pause traffic) for the duration of the
> one-time copy. Rows written to SQLite mid-copy fail verification — that is
> safe (the Postgres transaction rolls back and nothing is written) — and
> rows written after the copy simply won't be in Postgres.

### deployment/docker-compose.yml stack
The compose file now includes a `postgres:17-alpine` service (published on
`127.0.0.1:5432` for the migration) and sets `DATABASE_URL` on the app.
Note this legacy compose has a pre-existing defect unrelated to Postgres:
its build context is `deployment/` only, so the image contains no app code —
the sevi-deploy stack is the working deployment. For the DB service itself:

```bash
docker compose -f deployment/docker-compose.yml up -d postgres
# one-time copy of existing SQLite data (runs on the host via 127.0.0.1):
python scripts/migrate_chat_history_to_postgres.py \
  --dsn postgresql://sevi:sevi@127.0.0.1:5432/sevi
docker compose -f deployment/docker-compose.yml up -d
```

Set `POSTGRES_PASSWORD` in `.env` before exposing anything beyond localhost.

### sevi-deploy stack (the live one)
The `db` service is profile-gated and **opt-in** — nothing changes until you
do this. Two different files are involved because Docker Compose reads them
by different mechanisms: **`.env`** (next to `compose.yaml`) feeds compose
variable interpolation and `COMPOSE_PROFILES`; **`sevi.env`** is the api
service's `env_file`. Putting `POSTGRES_PASSWORD` in `sevi.env` does NOT
reach the `${POSTGRES_PASSWORD:-sevi}` reference in compose.yaml.

1. Create/extend `.env` next to `compose.yaml`:
   ```
   COMPOSE_PROFILES=postgres
   POSTGRES_PASSWORD=<password>
   ```
2. Add to `sevi.env`:
   ```
   DATABASE_URL=postgresql://sevi:<password>@db:5432/sevi
   ```
3. `docker compose up -d db` and wait for healthy.
4. `docker compose stop web` (quiesce), then **recreate** the api so the
   `env_file` change applies: `docker compose up -d api`.
5. Migrate the volume's SQLite once (DATABASE_URL is now in the container):
   `docker compose exec api python scripts/migrate_chat_history_to_postgres.py`
   — messages logged between step 4 and 5 stay only in SQLite; keep the
   window short or check the script's verification output.
6. `docker compose up -d web`.

Rollback at any point: remove `DATABASE_URL` from `sevi.env`, recreate the
api — it falls straight back to the SQLite file, which the migration never
modifies (it opens the source read-only).

## Semantics preserved deliberately

- Timestamps stay **TEXT ISO-8601** on both backends (lexical order ==
  chronological; data copies 1:1; every query is portable).
- `feedback.helpful` stays a **0/1 SMALLINT**, not BOOLEAN — `AVG(helpful)`
  powers `helpful_pct` and the Python `int()/bool()` round-trips are unchanged.
- **No foreign keys in the Postgres schema.** SQLite never enforced them
  (no `PRAGMA foreign_keys=ON` on these connections) and `cleanup_old_logs`
  deletes messages that feedback rows reference — enforcing FKs would start
  rejecting legitimate writes. Indexes cover the join paths instead.
- Identity columns are `GENERATED BY DEFAULT` so the migration can insert
  original ids; sequences are advanced past `MAX(id)` afterwards.

## ⚠️ Behavior change: `cleanup_old_logs` now actually deletes

The original `DELETE FROM chat_messages WHERE strftime('%s', timestamp) < ?`
was a **silent no-op for its entire life**: `strftime` returns TEXT, the bind
was a REAL, and SQLite orders REAL before TEXT in cross-type comparisons —
the predicate was always false, so the admin endpoint `DELETE /logs/cleanup`
always reported `deleted=0`. The portable rewrite (`timestamp < <ISO cutoff>`)
is correct on both backends, which means that endpoint (default `days=30`)
is now genuinely destructive against the ~205k-row history. Feedback rows
survive (no FKs) but lose their joined message context. Do not call it
casually; consider adding a dry-run/confirm step to the endpoint before
exposing it to any automation that used to call it "safely".

## Known accepted risks

- **DB-down-at-boot is swallowed** (parity with the original's error-handling
  philosophy): if Postgres is unreachable when the API starts, the logger
  prints `[ERROR]` and every log call returns None/[] until restart. The
  sevi-deploy api now has `depends_on: db (service_healthy, required: false)`
  to order startup when the profile is active.
- **Default password `sevi`** in both compose files keeps the zero-config dev
  path working; the DB is only on the internal compose network. Override
  `POSTGRES_PASSWORD` before any non-local deployment.
- **Postgres `LIKE` treats `\` as an escape character** (SQLite's doesn't), so
  a search query containing a backslash can return slightly different rows
  across backends. Cosmetic for this dashboard search box.

## What was NOT migrated (on purpose)

`data/cavsu_intents.db` (intents, patterns, responses, campus map, seasons —
320 KB) stays on SQLite: its source of truth is `data/cavsu_intents.json`,
and the sync logic (`intents_db.py`) rebuilds the DB **by deleting the file**
whenever JSON and DB diverge. That delete-and-rebuild contract is cheap and
atomic for a small read-mostly cache, and would need a full rework
(TRUNCATE + transactional reseed) on a server database for no operational
gain. Revisit only if multiple writers ever need to edit intents
concurrently in production.

## Verification performed (2026-07-13)

- Real-data migration: 252,811 rows across 6 tables into Postgres 17 in
  15.6 s; row counts and id-sum checksums verified per table.
- Dual-backend smoke: every `ChatLogger` read method returns identical rows,
  aggregates, and JSON-visible types on both backends against the same
  snapshot; `log_chat`/`log_feedback` return correct ids (sequence advanced
  past migrated max); feedback→message join intact; cleanup no-ops correctly.
