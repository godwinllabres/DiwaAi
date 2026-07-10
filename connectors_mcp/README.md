# diwa-connectors

The **shared read-only MCP tool server** for Diwa. One process hosts one vetted
tool group per university system; each group has its own on/off switch. This is
the second process in the two-process deployment topology from the connector
architecture document:

- **`ais-mcp`** (in the `cvsu-ais` repo) stays its own service — it holds the
  finance OAuth identity and the write kill switch, and is owned by the AIS team.
- **`diwa-connectors`** (this package) hosts everything read-only and low-risk:
  today the **courses catalog** and **ORPS ticket tracking**; next DTS document
  tracking, HRIS employee verification, and the registrar TOR tracker.

A module graduates out of this service into its own only when it gains
**write tools**, **a different owner team**, **its own credentials**, or
**different scaling needs**.

## Tool menu

| Group | Tool | What it answers |
|---|---|---|
| courses | `courses_list_campuses` | campuses in the catalog |
| courses | `courses_list_programs` | degree programs (optional search) |
| courses | `courses_get_program` | one program by id |
| courses | `courses_list_program_curricula` | curricula of a program |
| courses | `courses_list_curriculum_subjects` | subjects in a curriculum |
| courses | `courses_find_subject` | subjects by code/title |
| courses | `courses_get_subject_prerequisites` | prerequisites of a curriculum subject |
| courses | `courses_get_prerequisites` | two-hop: subject code within a curriculum → its prerequisites |
| orps | `orps_track_ticket` | ICT Helpdesk ticket status + history (public; format `HTKT-07-00001`) |
| dts | `dts_track_document` | "where is my document?" — status + movement history by reference number (public) |

All tools return one envelope: `{"ok": true, "data": ...}` or
`{"ok": false, "error": "..."}`. Upstream failures degrade to a polite error —
they never take the server down (fail-soft, same rule as the AIS bridge).
Tool names are namespaced by group so several MCP servers' menus can merge
inside Diwa without collisions.

## Run

```bash
pip install -e ".[dev]"
cp .env.example .env       # fill in the base URLs you want active
diwa-connectors                            # http on 127.0.0.1:8766 (/sse, /messages/, /health)
diwa-connectors --transport stdio          # for MCP hosts that spawn a subprocess
```

`GET /health` lists the active groups and tools — a group whose base URL is
unset simply does not exist on the wire.

> **Security (same caveat as ais-mcp):** the MCP wire is **unauthenticated**.
> Anything that can reach the port can call every tool. Bind to loopback
> (the default) or front it with a reverse proxy. This service is read-only by
> design — write tools belong in a system-owned server with a kill switch.

## How Diwa connects

Diwa's MCP client (see `api/ais_mcp.py` on the `feat/v2-chatresponse-envelope`
branch) connects to `http://127.0.0.1:8765/sse` for AIS. This server is the
second connection: `http://127.0.0.1:8766/sse`. The client merges both tool
lists; the router picks by namespaced tool name.

## Adding a group (the connector template)

1. Add a `GroupConfig` entry in `config.py` (base URL + enabled flag).
2. Create `groups/<system>.py` with a `build_group(cfg) -> ToolGroup` whose
   tool names start with `<system>_`. Read-only handlers call `http.get_json`
   and return the shared envelope.
3. Register it in `registry.collect_groups`.
4. Add respx-mocked tests under `tests/`.

## Tests

```bash
pytest
```
