# `create_dv` — MCP write-tool contract (spec)

**Owner:** `cvsu-ais` MCP server + the Frappe `accounting` app.
**Consumer:** SeviWeb, via `POST /ais/write` (the confirm flow) — **never** the chat NLU.
**Status:** interface spec for Phase 3 (Drafting). Implement on the AIS side; SeviAI only
proposes the draft and routes the confirmed write.

> **One-line contract.** `create_dv` inserts **one Disbursement Voucher at `docstatus=0`**
> (Draft), stamped **Prepared-by = the calling user**, under that user's own token, and returns
> the draft. It **never** submits, approves, posts, or cancels — those stay as the existing
> lifecycle tools, each behind its own human confirm.

---

## 1. Placement & guardrails (read this first)

- **WRITE class.** Registered on the MCP server as a write tool, gated by the write kill-switch
  and requiring the **per-user OAuth bearer** (no shared identity).
- **NOT advertised to the agentic loop.** `create_dv` must **not** appear in `ais_mcp._LLM_TOOLS`
  and is already in `agentic_loop._WRITE_DENYLIST` — so there is **no natural-language path** to it.
  The only way to reach it is: user reviews a proposed-document card → clicks *Create draft* →
  `POST /ais/write {action:"create_dv", …}`.
- **Draft-only.** Produces `docstatus=0`. A draft is reversible (cancellable), so its confirm gate
  is **lighter** than approve/post (no "type the DV name") — but it is still an explicit human click,
  and Sevi never auto-submits.

---

## 2. Request

`POST /ais/write` body:

```jsonc
{
  "action": "create_dv",
  "idempotency_key": "c1f2…",     // client-generated UUID; safe re-submit
  "args": {
    "payee": "Manila Electric Company",   // required — resolved to a Party server-side
    "amount": 45320.50,                    // required — gross PHP, > 0
    "particulars": "June 2026 electricity, Main Campus", // required
    "dv_type": "Regular",                  // enum: Regular | Trust | … (AIS list)
    "fund_cluster": "01 Regular Agency Fund",
    "uacs_object_code": "5020402000",      // required — validated via lookup_uacs
    "pap_code": "310100100001000",         // optional — STF/PAP where applicable
    "ors_burs_ref": "ORS-2026-0123",       // optional — links the obligation
    "responsibility_center": "Accounting Office", // optional — defaults to user's office
    "posting_date": "2026-06-30",          // optional — defaults to today; must be an OPEN period
    "withholding": [                        // optional — for auto 2307 later
      { "tax_type": "EWT", "rate": 0.02, "base": 45320.50 }
    ]
  }
}
```

Headers: the user's OAuth bearer (as `/ais/write` already requires).

---

## 3. Server behavior (ordered)

1. **AuthZ** — resolve session user from the bearer → `prepared_by = user`. Reject with
   `permission_denied` if the user lacks *create Disbursement Voucher* (Frappe role/permlevel) or
   the target office isn't in their User Permission scope.
2. **Idempotency** — if `idempotency_key` already produced a DV, **return that DV** (no duplicate).
   If the key is reused with *different* args, return `duplicate_conflict` with the existing DV name.
3. **Validate** — `amount > 0`; `uacs_object_code` exists (lookup); `payee` resolvable; `fund_cluster`
   valid; `ors_burs_ref` exists if given; `posting_date` in an **open** accounting period.
   On failure → `validation_error` naming the field.
4. **Advisory budget check** — call `budget_balance` for the account/allotment. If insufficient,
   **do not block** (a draft is allowed) — attach a `warning` so the preparer decides.
5. **Create** — `frappe.new_doc("Disbursement Voucher")`, set fields, `prepared_by = user`,
   `workflow_state = "Draft"`, then `.insert()` (stays `docstatus=0`). Naming series consumes
   `DV-.YYYY.-.#####`.
6. **Never** call submit / approve / post / cancel.
7. **Audit** — the insert is captured by Frappe's Version trail; the MCP layer logs
   `{action, user, dv_name, idempotency_key, redacted args}`.

---

## 4. Response

Success (`isError=false`):
```jsonc
{
  "ok": true,
  "dv_name": "DV-2026-00123",
  "workflow_status": "Draft",
  "docstatus": 0,
  "prepared_by": "jdcruz@cvsu.edu.ph",
  "amount": 45320.50,
  "desk_url": "https://erp.cvsu.edu.ph/app/disbursement-voucher/DV-2026-00123",
  "warnings": ["MOOE 5020402000 is 92% utilized — ₱12,340.00 remaining"],
  "next_actions": ["review_in_desk", "route_for_review"]  // hints for the UI, not auto-run
}
```

Sevi renders this as a success toast + a deep-link to the authoritative Desk form (with a
**DRAFT watermark** until Approved).

## 5. Error taxonomy (`isError=true`, machine `code`)

| `code` | Meaning | Sevi surfaces |
|---|---|---|
| `validation_error` | bad/missing field | the field + fix ("amount must be > 0") |
| `account_not_found` / `payee_not_found` / `ors_not_found` | lookup failed | "couldn't find …; check the code" |
| `permission_denied` | user can't create / wrong office | "you don't have rights to draft this — ask …" |
| `period_closed` | posting_date in a closed period | "that period is closed; use an open date" |
| `duplicate_conflict` | idempotency key reused w/ different args | link to the existing DV |
| `upstream_error` | Frappe 5xx (sanitized) | generic "AIS is having trouble" (internals never leak) |

---

## 6. Guardrail mapping

| Governance rule | How `create_dv` satisfies it |
|---|---|
| Draft-not-approve | inserts `docstatus=0` only; no submit path in the tool |
| Maker-checker | `prepared_by` = the authenticated user; Reviewed/Certified/Approved remain separate human actions in Desk |
| Separation of duties | writes run under the user's own token → Frappe owns the real preparer for later creator≠approver checks |
| Confirm-before-write | reachable only via the proposed-document card → explicit *Create draft* click; `idempotency_key` guards double-submit |
| Audit + never-delete | Version trail on insert; corrections are cancel-then-amend, never delete |
| Office-scoped | per-user token → User Permission by Department/Cost Center applies server-side |
| DPA (RA 10173) | mask volunteered PII (payee, amounts) before logs/LLM prompt; the tool stores only what the DocType needs |
| Record integrity | official COA print is generated from Frappe and watermarked DRAFT until Approved |

---

## 7. Explicitly out of scope

- **Submit / approve / post / cancel** — already exist (`approve_dv`, `post_dv`, `cancel_dv`,
  `set_dv_status`); each keeps its own type-the-DV-name confirm.
- **Amend** — a later `amend_dv` (cancel-and-amend assistant) reuses `create_dv` for the corrected copy.
- **Multi-DV batch creation** — one DV per call.
- **`draft_bir_2307` / memo tools** — separate contracts, same pattern.

---

## 8. Definition of done

- [ ] Tool inserts a DV at `docstatus=0`, `prepared_by`=caller, under the caller's token.
- [ ] Idempotency verified (same key → same DV; conflicting key → `duplicate_conflict`).
- [ ] Validation + advisory (non-blocking) budget warning wired.
- [ ] Absent from `_LLM_TOOLS`; present in `agentic_loop._WRITE_DENYLIST` (regression test).
- [ ] Version/audit entry on insert; MCP write log with redacted args.
- [ ] `POST /ais/write` accepts `action:"create_dv"` and returns the shape in §4.
- [ ] SeviWeb proposed-document card + *Create draft* confirm wired end-to-end.
- [ ] **Verified** the Frappe app enforces immutability-after-submit, permlevel, and creator≠approver
      (the open question in the design doc) — before this ships to staff.
