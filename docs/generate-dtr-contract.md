# `generate_dtr` — DTR (CSC Form 48) generation — design & contract

**What:** let staff ask Sevi *"generate my DTR for June 2026"* and get the **Daily Time Record
(CSC Form No. 48)** rendered from live attendance — self-scoped, DRAFT until signed.
**Reality:** the codebase has **zero HR/attendance coverage** (grep-confirmed; AIS tools are
accounting-only). This is a **new connector group** (`cvsu-hr` MCP), parallel to `cvsu-ais` —
*not* an AIS add-on.

> **One-line contract.** DTR generation is **read-and-render**: it reads the employee's checkins
> for a month, buckets them into AM/PM arrival/departure, computes tardiness/undertime **from the
> source system only** (never invented), and renders CSC Form 48 as a preview/PDF watermarked
> **DRAFT** until the employee and supervisor sign. Sevi never certifies attendance.

---

## 1. Where the data comes from (the prerequisite)

A DTR is a monthly view over an attendance source. The standard ERPNext HR model:

| Source | Role in the DTR |
|---|---|
| **Employee Checkin** (IN/OUT logs) | raw punches → AM-in, AM-out, PM-in, PM-out per day |
| **Shift Type** | official hours (arrival/departure), grace period, break |
| **Holiday List** | weekends/holidays → no undertime |
| **Leave Application / Official Business** | leave/OB days → not "absent", annotated |

**Open prerequisite:** does CvSU attendance live in **ERPNext HR (Employee Checkin)**, or in a
**separate biometric/DTR system**? That decides whether `cvsu-hr` reads Frappe directly or wraps a
device export imported into Employee Checkin. **This must be answered before building.**

---

## 2. CSC Form 48 — what we render

Per-employee, per-month. Columns per day (1–31): **AM Arrival · AM Departure · PM Arrival ·
PM Departure · Undertime (H · M)**, plus totals, and two signature blocks:
- Employee: *"I CERTIFY on my honor that the above is a true and correct report of the hours of
  work performed…"*
- Supervisor: *"Verified as to the prescribed office hours."*

Governed by the CSC Omnibus Rules on attendance — the **signed** form is the official record.

---

## 3. Capability shape (fits the copilot)

- **Self-service (default):** "Generate my DTR for June 2026" → Sevi renders the employee's own
  CSC Form 48 (preview + PDF), DRAFT-watermarked, ready to print/sign.
- **Supervisor batch:** "Generate DTRs for my unit for June" → scoped to their team only.
- **Two storage modes** (office chooses):
  - **(a) Print-only** — render from attendance, no stored record. Employee prints, signs,
    files physically. *Lowest risk; read + render only.*
  - **(b) DTR DocType** — a submittable `Daily Time Record` at `docstatus=0`, **Certified-by =
    employee**, then supervisor **Verifies** (maker-checker). Sevi drafts; humans sign in Desk.

---

## 4. Tools (new `cvsu-hr` MCP group)

**READ** (safe for the agentic loop — self/unit scoped):
- `get_employee_shift(employee)` → official hours, grace, break (Shift Type).
- `get_attendance(employee, month, year)` → per-day computed rows (below) + day type.
- `list_unit_employees(department|supervisor)` → team roster for batch DTRs.

**RENDER / WRITE** (behind the confirm/preview flow, **not** in `_LLM_TOOLS`, in the write-denylist):
- `render_dtr(employee, month, year, signatories?)` → CSC Form 48 PDF/preview (read + render, no stored record).
- *(optional, mode b)* `draft_dtr(employee, month, year)` → `Daily Time Record` at `docstatus=0`, Certified-by = employee.

### `get_attendance` response (the heart of it)
```jsonc
{
  "employee": "2021-00456", "employee_name": "Dela Cruz, Juan",
  "month": 6, "year": 2026,
  "shift": { "am_in": "08:00", "am_out": "12:00", "pm_in": "13:00", "pm_out": "17:00", "grace_min": 0 },
  "days": [
    { "day": 1, "type": "workday",
      "am_in": "07:58", "am_out": "12:03", "pm_in": "12:59", "pm_out": "17:05",
      "undertime_h": 0, "undertime_m": 0, "flags": [] },
    { "day": 2, "type": "workday",
      "am_in": "08:22", "am_out": "12:00", "pm_in": "13:00", "pm_out": "16:40",
      "undertime_h": 0, "undertime_m": 42, "flags": ["tardy_am","early_out_pm"] },
    { "day": 7, "type": "weekend", "flags": [] },
    { "day": 12, "type": "holiday", "name": "Independence Day", "flags": [] },
    { "day": 15, "type": "leave", "leave_type": "Vacation Leave", "flags": [] },
    { "day": 18, "type": "workday", "am_in": "08:01", "am_out": null, "pm_in": null,
      "pm_out": "17:02", "flags": ["missing_punch"] }
  ],
  "totals": { "undertime_h": 1, "undertime_m": 12, "days_present": 20, "days_leave": 1 }
}
```

---

## 5. Computation rules — data, not guesses

- **Bucket** punches into AM/PM in/out; a single punch or gaps → `missing_punch` flag, **rendered
  blank, never fabricated**.
- **Tardiness / undertime** vs the employee's **Shift Type** (start/end, grace, break) — all
  **configurable settings**, not hardcoded (statutory-values-as-data).
- **Weekends/holidays** from Holiday List → no undertime. **Leave/OB** from Leave Application →
  annotated, not counted absent.
- **Half-day / flexi-time / official time** handled per the office's policy settings.
- Sevi renders **exactly** what the source holds; corrections happen in the attendance data
  (with audit), never by editing the DTR output.

---

## 6. Governance & DPA (heavier than accounting)

| Rule | How DTR generation satisfies it |
|---|---|
| **DPA (RA 10173) — attendance is personal data** | Strict self-scope: employees see **only their own** DTR; supervisors **only their unit** (User Permission by Department); HR admin broader; behind permlevel. Never anonymous. |
| **Access is an event** | Log **who generated whose** DTR — a supervisor pulling a subordinate's DTR is an audited access. |
| **Draft-not-certify** | Sevi renders/drafts only; the employee's "I certify on my honor" and the supervisor's verification are **human** signatures (wet or e-sign in Desk). Sevi never signs/certifies. |
| **Integrity / no fabrication** | Missing punches shown blank + flagged; times come only from the source. An LLM-styled DTR can never pass as a signed record — **DRAFT watermark** until signed. |
| **Maker-checker (mode b)** | `draft_dtr` stamps Certified-by = employee; supervisor "Verify" is a distinct Desk action. |

---

## 7. Roadmap fit

DTR is a **separate HR track**, sequenced *after* (or parallel to) the accounting phases, because
it needs a new data source + connector. Prereqs, in order:

1. **Decide the attendance source** (ERPNext HR Employee Checkin vs biometric export) — §1 open question.
2. Configure ERPNext HR: Employee, Shift Type, Holiday List, Leave — and get **Employee Checkin populated** (biometric integration).
3. Build the **CSC Form 48 print format** in Frappe (letterhead, signature blocks).
4. Stand up the **`cvsu-hr` MCP** with `get_attendance` / `render_dtr` (+ optional `draft_dtr`).
5. Wire **DPA self/unit scoping** (per-user token) and the access-audit log.
6. Then expose in Sevi: `get_attendance` to the agentic read loop; `render_dtr`/`draft_dtr` behind the preview/confirm flow.

---

## 8. Open questions (answer before building)

- **Attendance source of truth** — ERPNext HR, or a separate biometric/DTR system? (Determines the connector.)
- **Storage** — print-only, or a submittable `Daily Time Record` DocType (maker-checker)?
- **Attendance policy as settings** — grace period, flexi-time, half-day, OB/leave handling, rounding.
- **Signatures** — is e-signature accepted for CSC forms, or wet-signature only?
- **Permission matrix** — who may generate whose DTR (self / supervisor / HR / payroll)?
- **Payroll linkage** — does the DTR feed payroll deductions (tardiness/undertime), or is it attendance-only for now?

---

## 9. Definition of done

- [ ] `cvsu-hr` MCP with `get_attendance` returning the §4 shape from the agreed source.
- [ ] Tardiness/undertime computed from **Shift Type settings**, weekends/holidays/leave honored, missing punches flagged (never fabricated).
- [ ] `render_dtr` produces CSC Form 48 from Frappe, **DRAFT-watermarked** until signed.
- [ ] Self/unit DPA scoping enforced via per-user token; access-audit log records who generated whose DTR.
- [ ] `render_dtr`/`draft_dtr` absent from `_LLM_TOOLS`, present in `agentic_loop._WRITE_DENYLIST`; `get_attendance` allowed as READ.
- [ ] (mode b) `draft_dtr` inserts at `docstatus=0`, Certified-by = employee; supervisor Verify is a separate Desk action.
