# `render_coe` / `render_service_record` — COE & CSC Service Record — design & contract

**What:** let staff ask Sevi *"generate my Certificate of Employment"* / *"print my Service Record"*
and get the official document rendered from the Employee record — self-scoped, DRAFT until an
authorized HR signatory signs.
**Reality:** extends the **`cvsu-hr`** connector introduced for DTR (still zero HR coverage in the
codebase today). **COE is the quick win**; the **CSC Service Record needs service-history data**
that ERPNext doesn't model out of the box (see §4).

> **One-line contract.** Both are **read-and-render**: pull the employee's record, render the
> official document (COE letter / CSC Service Record form) as a preview/PDF watermarked **DRAFT**.
> Sevi never certifies — an **authorized HR signatory** (HRMO / appointing authority) signs. There
> is no multi-step maker-checker workflow, but issuance still requires that human signature.

---

## 1. The two documents

**Certificate of Employment (COE)** — certifies that a person is/was employed: name, position,
employment status (Permanent / Casual / Contractual / Job Order), inclusive dates, and *optionally*
salary. Free-form letter on office letterhead, issued on request (loans, visa, clearance).

**CSC Service Record (Revised)** — the CSC-prescribed chronological record of government service.
Columns per service line: **From–To dates · Designation · Status · Salary · Station/Place of
Assignment · Branch (Nat'l/Local/GOCC) · LWOP · Separation (date & cause) · Remarks**, with a header
(full name, birth date/place) and the HRMO certification: *"I hereby certify that this is a true
record of service…"*.

---

## 2. Capability shape (fits the copilot)

- **Employee self-service:** "Generate my COE for a bank loan" → Sevi renders the employee's own
  COE draft (salary included only if requested/authorized), DRAFT-watermarked, routed to HR to sign.
- **HR staff:** "Generate the Service Record for employee 2018-0087" → HR (permitted role) renders
  any employee's SR/COE; the generation is **audited** (who pulled whose record).
- Sevi **renders a draft**; the **authorized signatory certifies** (wet or e-sign in Desk). No
  approval chain to model — just render → sign.

---

## 3. Tools (extend the `cvsu-hr` MCP group)

**READ** (self/HR scoped; salary field behind permlevel — safe for the agentic loop *without* salary):
- `get_employee_profile(employee)` → COE fields: name, employee_no, position, employment_type,
  date_joined, date_separated?, status, department, (salary — permlevel-gated).
- `get_service_record(employee)` → ordered service-history entries (§4 shape).

**RENDER** (produce official output → **not** in `_LLM_TOOLS`, **in** the write-denylist, behind the preview/confirm flow):
- `render_coe(employee, options)` → COE PDF/preview.
- `render_service_record(employee)` → CSC Service Record form PDF/preview.

`render_coe` options:
```jsonc
{ "include_salary": false,           // default off — salary is sensitive (permlevel + purpose)
  "purpose": "bank loan application", // printed on the certificate
  "as_of": "2026-07-15",             // employment status as of this date
  "signatory": "hrmo" }              // which authorized signatory block to render
```

---

## 4. Data source & the Service-Record gap

**COE — turnkey.** All fields live on the ERPNext **Employee** DocType (name, designation,
employment_type, date_of_joining, relieving_date, status, department) + **Salary Structure
Assignment** for the optional salary. `render_coe` is a straight render.

**Service Record — needs modeling.** A full CSC Service Record is a *chronological history* of every
appointment, promotion, status/salary change, LWOP, and separation. ERPNext scatters fragments
across **Employee Promotion / Transfer / Salary Structure Assignment**, but has **no single service-
history table** matching the CSC columns. So `get_service_record` needs either:
- a **custom `Service Record Entry` child table** (or DocType) on Employee, back-filled from records, **or**
- a mapping/aggregation layer that assembles the CSC lines from the ERPNext fragments.

`get_service_record` response (target shape):
```jsonc
{
  "employee": "2018-0087", "name": "Dela Cruz, Juan Santos",
  "birth_date": "1988-04-12", "birth_place": "Indang, Cavite",
  "entries": [
    { "from": "2015-08-01", "to": "2018-06-30", "designation": "Instructor I",
      "status": "Temporary", "salary": "P32,053", "station": "CvSU–Indang",
      "branch": "National", "lwop": null, "separation": null, "remarks": "" },
    { "from": "2018-07-01", "to": "present", "designation": "Assistant Professor I",
      "status": "Permanent", "salary": "P45,203", "station": "CvSU–Indang",
      "branch": "National", "lwop": null, "separation": null, "remarks": "Promotion" }
  ]
}
```

---

## 5. Governance & DPA

| Rule | How COE/SR generation satisfies it |
|---|---|
| **DPA (RA 10173)** | COE/SR are personal + employment data. **Self-scope** by default; HR roles may generate for others via per-user token + User Permission. Never anonymous. |
| **Salary is extra-sensitive** | `include_salary` defaults **off**; salary field sits behind a higher **permlevel** and is printed only on explicit request/authorization. |
| **Access is an event** | Log **who generated whose** COE/SR — HR pulling a subordinate's record is an audited access. |
| **Render-not-certify** | Sevi renders a **DRAFT** (watermarked); the **authorized signatory** (HRMO / appointing authority) certifies. An LLM-styled certificate can never pass as a signed document. |
| **Integrity** | Every field comes from the Employee/service data; nothing invented. Corrections happen in the HR records (audited), not by editing the rendered output. |

---

## 6. Error taxonomy (`isError=true`, machine `code`)

| `code` | Meaning | Sevi surfaces |
|---|---|---|
| `employee_not_found` | no such employee | "couldn't find that employee" |
| `permission_denied` | caller can't view this employee's record | "you can only generate your own — ask HR" |
| `salary_not_authorized` | `include_salary` but caller lacks permlevel | render without salary + note |
| `service_history_unavailable` | SR entries not yet modeled/back-filled | "Service Record data isn't available yet — HR must complete it" |
| `upstream_error` | Frappe 5xx (sanitized) | generic "HR system is having trouble" |

---

## 7. Roadmap fit

A **quick win on the `cvsu-hr` track** — do it right after (or alongside) DTR, since it reuses the
same connector, identity, and DPA scoping.
1. **COE first** — turnkey off the Employee record; build the COE print format + `render_coe`.
2. **Service Record after** — model/aggregate the service-history data (§4), then `render_service_record`.
Prereqs: ERPNext HR Employee data clean; authorized-signatory config; COE/SR print formats in Frappe;
per-user identity + access-audit (shared with DTR).

---

## 8. Open questions

- **Authorized signatory** — who signs (HRMO, HR Director, appointing authority)? Wet-signature only, or e-sign accepted for CSC documents?
- **Salary-on-COE policy** — when is salary disclosed, and who authorizes it?
- **Service-Record data** — is the service history already in ERPNext (custom table?), or must it be modeled and back-filled? (Determines COE-only vs full SR.)
- **Who may generate whose** — self only, plus which HR roles for others?
- **Templates** — does CvSU have a standard COE wording + the CSC Service Record (Revised) print layout to match?

---

## 9. Definition of done

- [ ] `get_employee_profile` returns COE fields (salary permlevel-gated) from the Employee record.
- [ ] `render_coe` produces the COE from Frappe, **DRAFT-watermarked**, salary off by default.
- [ ] `get_service_record` returns the §4 shape (once service history is modeled/back-filled).
- [ ] `render_service_record` produces the CSC Service Record (Revised) form, DRAFT-watermarked.
- [ ] Self/HR DPA scoping via per-user token; access-audit records who generated whose document.
- [ ] `render_coe`/`render_service_record` absent from `_LLM_TOOLS`, present in `agentic_loop._WRITE_DENYLIST`; profile reads allowed without salary.
