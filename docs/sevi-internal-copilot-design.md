# Sevi Internal Copilot — Design & Roadmap

**Audience:** CvSU internal staff (accounting, budget, admin), inside the ERPNext Desk.
**Scope:** staff-only agentive assistant — live AIS queries, document drafting, guided
maker-checker workflows. The public student widget stays a separate, anonymous, read-only bot.
**Status:** design grounded in a full read of `api/app.py`, `api/hybrid_chatbot.py`,
`api/ais_mcp.py`, `api/auth_ais.py`, and `SeviWeb/app/components/AisWriteModals.tsx`.

> **Vision.** Sevi becomes a **maker-checker copilot** in the Desk: staff ask in plain
> language and Sevi answers from **live AIS data under their own identity**, drafts official
> documents at `docstatus=0` as *"Prepared by"* them, and shepherds records through
> Prepared → Reviewed → Certified → Approved — but **every state change routes through an
> explicit human confirm gate under the user's own token**. Sevi never submits, approves,
> posts, or cancels on its own.

---

## 1. Reality check — what exists today (don't rebuild it)

| Area | State today |
|---|---|
| **Brain** | Live entrypoint is `api.app:app` (root `app.py` is legacy). Cascade: Naive Bayes → BiLSTM NN → intent-retrieval → LLM fallback → verbatim quote → static. |
| **LLM** | `ClaudeLLM` (claude-haiku-4-5) or `LocalLLM` (Ollama qwen3:8b, live `.env`). **Called with NO `tools=` and no agentic loop** — plain, scope-locked text only. |
| **Tool-calling** | Exists **only** in the AIS bridge (`api/ais_mcp.py`) as a **single-shot pre-brain router**: advertises 10 read schemas → extracts one `{tool,args}` → calls over MCP-SSE → deterministic card/table. No result-fed-back loop, no chaining, **no NL path to writes**. |
| **AIS read tools (live)** | `get_dv`, `list_pending_dvs`, `find_dv`, `budget_balance`, `lookup_uacs`, `dv_totals`, `run_report`, `list_bir_2307`/`get_bir_2307`/`find_bir_2307`. |
| **AIS write tools** | `approve_dv`, `post_dv`, `cancel_dv`, `set_dv_status` — reachable **only** via `POST /ais/write` + per-user OAuth (`api/auth_ais.py`), **never** from chat. **No `create_dv` tool exists.** |
| **Identity** | Reads run under **one shared MCP identity** → office-scoping / permlevel / SoD are *not* applied on reads (`list_pending_dvs` returns the bot's queue, not the user's). Writes already carry the per-user token. |
| **UI foundation** | Typed `ChatResponse` v2 envelope with cards (`DvCard`, `TableCard`, `MapCard`), source-provenance chips, suggestions. `AisWriteModals` confirm flow + DV transition matrix exist. `useAuth.hasAnyRole` is **defined but unused**. |
| **Audit** | `ChatLogger` logs every turn (Postgres/SQLite). Admin is a single shared `DASHBOARD_PIN`. |
| **Activation blocker** | `mcp[sse]` is **absent from `deployment/requirements*.txt`** → the bridge is a **silent no-op in prod**; the `cvsu-ais` SSE server must be deployed and reachable; `AIS_MCP_LLM_ROUTER` defaults to `0` on Render. |

**Takeaway:** the agentive backbone is ~70% built but **dormant and identity-unsafe**. The work
is *activation + per-user identity + one real agentic loop + a few write/create tools* — not a rewrite.

---

## 2. Governance guardrails (apply to every agentive action)

These are non-negotiable for a PH-government/SUC deployment; the design assumes the separate
Frappe accounting app enforces them server-side (see Open Questions).

- **Draft-not-approve** — Sevi only creates `docstatus=0` records and only *proposes* a state
  change. There is intentionally **no natural-language path to a write.**
- **Maker-checker** — drafts stamp *Prepared-by = the logged-in user*; Reviewed/Certified/Approved
  each require a distinct human action, one role per signatory.
- **Separation of duties (creator ≠ approver)** — enforced server-side because every read *and*
  write carries the user's **own** OAuth bearer; client `hasAnyRole` is a UX guard only.
- **Confirm-before-write** — every write goes through a modal requiring the user to **type the
  exact DV name** (plus a reason to cancel), with an `idempotency_key` (safe re-submit), an
  optimistic-lock `expected_modified` token (stale card rejected), and a write **kill-switch**.
- **Audit + never-delete** — writes recorded to Frappe's Version trail; corrections are
  cancel-with-reason then amend; chat/tool turns audit-logged with **redacted args**; move chat
  logs to **append-only** retention.
- **Office-scoped visibility** — finance surface is **never anonymous**; per-user token lets Frappe
  apply User Permission by Department/Cost Center and permlevel (HR/personal fields).
- **DPA (RA 10173) masking** — mask personal/financial data before logs and LLM prompts (payee
  names, amounts, student/employee numbers), on top of existing TIN-last-4 / address / phone redaction.
- **Official-record integrity** — COA print formats render **from Frappe**, watermarked **DRAFT**
  until Approved; an LLM-styled preview can never pass as a signed record.

---

## 3. Capability catalog

Legend — effort **S/M/L**, value **high/med/low**. "GAP" = must be built.

### 3.1 Query (read, live AIS)
| Feature | E/V | Backed by | Note |
|---|---|---|---|
| **My action queue** — "what's waiting on me?" grouped by stage, Desk deep-links | M/high | `list_pending_dvs` + cards | GAP: thread per-user token onto reads (else it's the bot's queue) |
| **Where's my voucher** — find DV by control no./payee/ORS, shows current signatory step | S/high | `find_dv`+`get_dv`+`DvCard` | near-turnkey once bridge live + per-user |
| **Obligate-safely budget check** — resolve UACS/PAP then remaining allotment in one turn | M/high | `lookup_uacs`+`budget_balance` | GAP: needs the agentic loop (2 reads, 1 turn) |
| **BIR 2307 finder** — search withholding certs by payee/DV/period | S/med | `find/get/list_bir_2307` | TIN/address already redacted |
| **Disbursement summaries** — `dv_totals` grouped sums w/ NL date ranges | S/med | `dv_totals` | figures from Frappe, never the LLM |

### 3.2 Document creation (drafts, `docstatus=0`)
| Feature | E/V | Backed by | Note |
|---|---|---|---|
| **Draft a DV from natural language** — resolve account, check funds, assemble DRAFT, Prepared-by=user | L/high | `lookup_uacs`+`budget_balance` | **GAP: build `create_dv` write tool** (insert at `docstatus=0`) + generalized proposed-document card |
| **COA print preview (DRAFT watermark)** | M/med | `get_dv` | GAP: expose Frappe print-format render as a tool; no PDF path in SeviAI today |
| **Official COA print/PDF (submitted only)** | M/med | same print tool | policy: non-watermarked only for submitted records |
| **BIR 2307 draft from a DV** | L/med | `find/get_bir_2307` | GAP: `draft_bir_2307` tool |
| **Transmittal / routing memo draft** | L/low | `get_dv` | GAP: correspondence DocType/template + tool |

### 3.3 Agentive workflow (proposes; human executes)
| Feature | E/V | Backed by | Note |
|---|---|---|---|
| **Guided DV lifecycle w/ SoD** — narrate state, propose next legal transition, type-the-name confirm → `/ais/write` | M/high | write tools + `AisWriteModals` + transition matrix | GAP: wire unused `hasAnyRole` to hide wrong-role pills |
| **Cancel-and-amend assistant** — cancel-with-reason, then draft corrected replacement | L/med | `cancel_dv`+`get_dv` | needs `create_dv` for the amended copy |
| **Multi-step read planner (agentic loop)** — decompose a compound request into a READ-only tool plan, narrate | L/high | existing read tools | **GAP: add `tools=` + a real model→tool→result loop** (the keystone) |

### 3.4 Report
| Feature | E/V | Backed by | Note |
|---|---|---|---|
| **Narrated COA report run** — RAPAL/RAOD/RBUD/RANCA/FAR + plain-language narration + Desk link | M/high | `run_report` | GAP: LLM narration of report JSON (evidence-gated to returned rows) |
| **Budget near-exhaustion alert** | L/med | `budget_balance` | GAP: scheduler + notification surface (chat is pull-only) |

### 3.5 Admin / navigation
| Feature | E/V | Backed by | Note |
|---|---|---|---|
| **"Who acts next" + Desk hand-off** — where the DV sits, what you can/can't do, deep-link | S/med | `get_dv`+transition matrix+deep-link | pure explain-and-link |
| **Desk onboarding / "how do I" navigator** — answer from Citizens' Charter/site corpora w/ citations + Desk link | M/med | `charter_rag`+`site_rag` | GAP: register `charter_retrieve`/`desk_link` as callable tools |

---

## 4. Embedding UX (in the Desk)

A slide-in panel inside the ERPNext Desk (workspace widget + launcher), **not** the public iframe.
It **inherits the logged-in Frappe session as its identity** (no separate AIS login in the common
case), so every read/write runs under the real user's roles/offices/permlevel. The thread renders
the existing typed cards + a **source-provenance chip** ("AIS live data" / "Citizens Charter" /
"AI assistant"). Multi-step plans stream a lightweight *"running lookup_uacs → budget_balance"*
progress line. Writes surface as **proposed-action cards**: click a workflow pill → confirm modal
(type the exact DV name, reason for cancel) → success toast + deep-link to the authoritative Desk
form. Drafts/previews carry a visible **DRAFT watermark** until Approved. Wrong-role actions are
hidden; anything Sevi can't do, it explains and links to the right Desk screen.

---

## 5. Roadmap

**Phase 0 — Foundation & activation** *(nothing ships to staff until this is done)*
- Ship `mcp[sse]` in `deployment/requirements*.txt` and rebuild (today it's a silent no-op).
- Deploy/run the `cvsu-ais` SSE server; point `AIS_MCP_URL` at it on a **private** network; keep `AIS_MCP_ENABLED=1`.
- **Fence the finance surface behind an authenticated identity** (reuse the Desk/Frappe session) — no anonymous `/chat` user can reach finance tools.
- **Thread the per-user token onto the READ path** (`auth_ais.get_user_token`) so scoping/permlevel/SoD apply.
- PII masking before logging & before LLM prompts; append-only chat-log retention.
- **Verify the Frappe accounting app enforces** immutability-after-submit, maker-checker, creator≠approver, User Permission scoping, permlevel, Version audit.

**Phase 1 — Read-only queries.** Where's my voucher · My action queue · BIR 2307 finder · Disbursement summaries · Who-acts-next · Desk navigator. *(Prereq: Phase 0; `AIS_MCP_LLM_ROUTER=1` with a tool-capable model everywhere.)*

**Phase 2 — Reports, narration & the agentic read loop.** Budget check (lookup_uacs→budget_balance) · Narrated COA report · Multi-step read planner. *(Prereq: add `tools=` to `generate()` + a READ-only tool-use loop replacing the single-shot router; per-session tool state.)*

**Phase 3 — Drafting.** Draft DV · DRAFT print preview · 2307 draft · memo draft. *(Prereq: build `create_dv`/`draft_bir_2307`/memo write tools at `docstatus=0`; a generalized proposed-document card; Frappe print-format render as a tool + DRAFT watermark.)*

**Phase 4 — Guided approvals (confirmed writes).** Guided DV lifecycle · cancel-and-amend · official print. *(Prereq: `VITE_AIS_WRITE_ENABLED=1`; server-driven write kill-switch; wire `hasAnyRole`; per-admin creds replacing `DASHBOARD_PIN`.)*

**Phase 5 — Proactive alerts.** Budget near-exhaustion. *(Prereq: scheduler + notification surface; externalize session/rate-limit/circuit-breaker state to Redis before multi-worker.)*

---

## 6. Gaps to build (consolidated)

1. **Agentic tool-use loop** — `tools=` on `ClaudeLLM`/`LocalLLM.generate` + a model→tool→result loop (keep it READ-only). *The keystone unlock.*
2. **Per-user token on the READ path** (reuse `auth_ais.get_user_token`).
3. **`create_dv`** MCP write tool (`docstatus=0`, Prepared-by=user); later `draft_bir_2307`, memo tools + DocTypes.
4. **Frappe print-format render** exposed as a tool + preview/PDF card with DRAFT watermark.
5. **Generalized proposed-document/action card** (current `ConfirmWriteModal` is DV-lifecycle-hardcoded).
6. **Report-result narration** step (LLM summary of `run_report` JSON, evidence-gated).
7. **PII masking layer** before logs/prompts; **append-only** chat-log retention.
8. Register `charter_retrieve`/`desk_link` as callable tools (today hardcoded branches).
9. **Scheduler + notification surface** for proactive alerts.
10. **Redis** for session/token/rate-limit/circuit-breaker/audit state (multi-worker).
11. **Per-admin credentials + admin-action audit** replacing shared `DASHBOARD_PIN`; wire `hasAnyRole`.
12. Ship `mcp[sse]` + deploy the `cvsu-ais` SSE server (activation, but blocks everything).

---

## 7. Open questions (resolve before Phase 3+)

- **Does the Frappe accounting app actually enforce** immutability-after-submit, the maker-checker chain, creator≠approver SoD, User Permission scoping, permlevel HR fields, Version audit, and COA print formats? *The whole guardrail story collapses if it doesn't — verify in that repo.*
- **Identity model for the Desk embed:** can Sevi reuse the Frappe Desk session as the AIS OAuth identity (SSO/JWT), or is the password-grant login modal still needed per session?
- Does **Cloudflare Access** intercept Sevi's server-side egress to the Frappe OAuth/token endpoint?
- How is the maker-checker workflow modeled in Frappe (native Workflow states vs the client `DV_AVAILABLE_ACTIONS` matrix) — and do they stay in sync?
- What's the correct backing query for **"pending MY action"** per signatory role once reads are per-user?
- **Scope of drafting authority:** which DocTypes may Sevi create at `docstatus=0` (DV only, or also 2307/memos/obligation requests)? Who owns the print-format templates?
- **DPA artifacts:** PIA, breach runbook, data-sharing agreements, updated privacy notice covering finance data + tool calls + retraining — required before production.
- **Cross-border LLM posture:** stay local-only (Ollama) to avoid RA 10173 cross-border transfer of finance/PII, or is Claude acceptable with safeguards? Drives the tool-calling model choice.
- **Later scope:** HR/payroll or procurement? Zero coverage today (no intents, no tools) — new connector groups + higher-permlevel handling.

---
*Generated from a multi-agent read of the SeviAI/SeviWeb/AIS codebase. Effort/value tags are
first-pass estimates; treat the phase gates and Open Questions as the real acceptance criteria.*
