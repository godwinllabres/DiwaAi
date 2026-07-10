# Privacy & Compliance Review Areas — Diwa (PH)

**Status:** discussion checklist, 2026-07-11 · **Not legal advice** — every
item here is framed for review with the **CvSU Data Protection Officer**
(the chat widget already links to the DPO office's privacy notice).

## 1. Governing framework

| Instrument | Why it applies |
|---|---|
| **RA 10173 — Data Privacy Act of 2012** + IRR | Core law. Note §3(l): *sensitive personal information* explicitly includes **education records** (grades, enrollment — the P2-Academic connectors) and **health** (self-harm signals the SafetyGate now captures). |
| NPC Circular 2022-04 (registration), 16-03 (breach), PIA guidance, NPC advisories on AI systems | Registration of the processing system, 72-hour breach notification, Privacy Impact Assessment before production, AI transparency expectations. |
| RA 10175 — Cybercrime Prevention Act | Threat messages the moderation log captures may become evidence; preservation and lawful-disclosure questions. |
| CHED / university records rules | Registrar-owned education records surfaced via connectors stay governed by the source system's rules. |

## 2. What Diwa actually processes today (data inventory)

| Data | Where it lives | Classification | Notes |
|---|---|---|---|
| Chat messages + bot replies, session_id/user_id, timings | `logs/chat_history.db` (SQLite, **gitignored — verified**) | PI; can contain volunteered SPI (student numbers, health disclosures) | No retention schedule yet |
| Moderation trips incl. raw flagged text | in-memory ring (last 20) + chat log rows | **SPI when self-harm (health)** | `/admin/moderation` displays it — access control matters |
| Feedback (thumbs, reasons, message text) | same DB | PI | |
| Campus/session context | in-memory, 30-min TTL | minimal | good minimization example |
| AIS login pass-through (`/auth/login`) + server-side session tokens | memory, session-keyed | staff credentials/PI | deliberately not persisted client-side |
| Connector outputs shown in chat | transient | staff names in ORPS ticket logs; DTS remarks; future: DV payees/amounts, **grades/admission status = SPI** | proportionality + source-system notice coverage |
| Hosting today | personal domain + Render (US) + Cloudflare tunnel | — | data residency / outsourcing review → the university-hosting decision is also a compliance fix |
| LLM processing | **local Ollama only** (Claude disabled) | — | no cross-border AI transfer while this holds |

## 3. Areas to discuss (the agenda)

1. **Roles & accountability** — CvSU as Personal Information Controller; dev/
   hosting as processor; DPO sign-off; NPC registration of the system.
2. **Legal basis & notice** — what the consent gate + privacy notice must
   disclose: chat logging, safety moderation, analytics, **use of chats for
   intent retraining** (a secondary purpose that must be stated), connector
   lookups, and — if ever re-enabled — commercial AI processing.
3. **PIA** — run a Privacy Impact Assessment before production on
   cvsu.edu.ph (new system + AI + SPI = squarely in NPC's expectation).
4. **Retention & minimization** — define: chat logs (e.g., N months then
   anonymize), moderation samples, feedback; pseudonymized analytics;
   documented disposal.
5. **Sensitive categories** —
   - *Self-harm logs are health SPI*: restrict who can view, decide
     retention, and document the escalation protocol (disclosure to Guidance
     is defensible under DPA §13 protection-of-life, but write it down).
   - *Minors*: freshmen applicants can be under 18 — notice readability and
     parental-consent posture for the admission connector.
   - *Education records*: grades/enrollment connectors process SPI — DPO
     gate before launch (already in the architecture roadmap).
6. **Security measures** (DPA-mandated) — HTTPS end-to-end in production;
   **replace the single shared DASHBOARD_PIN with per-admin credentials +
   an audit trail** before real moderation use (it gates SPI views);
   encryption-at-rest and backup handling for the chat DB; secret
   management (the .env key episode); breach-detection logging.
7. **Third parties & cross-border** — hosting providers review; if Claude
   (or any cloud AI) is re-enabled: outsourcing/data-sharing agreement,
   §21 cross-border accountability, and notice disclosure FIRST.
8. **Data subject rights ops** — how a student requests access/erasure of
   their chat history (keyed by session/user id — feasible today); route
   correction requests for connector data to the source system; DPO as the
   contact channel.
9. **Connector proportionality** — chat displays third parties' PI (staff
   names in ticket histories, payees on DVs). Confirm each source system's
   own privacy notice covers this surface, and put internal data-sharing
   arrangements in writing with each system owner.
10. **Breach preparedness** — 72-hour NPC + data-subject notification;
    runbook naming owners; scenarios: chat-DB exfiltration, admin-PIN leak,
    connector over-exposure.
11. **AI transparency** (NPC AI guidance) — bot disclosure at first contact,
    source labels on answers (built), no-fabrication measures (built),
    a human alternative always named (offices/contacts), and keeping any
    write-action human-confirmed (built: kill switch + confirm modal).
12. **Artifacts to produce** — PIA, updated privacy notice text, retention
    schedule, data-sharing agreement template, moderation escalation
    protocol, breach runbook.

## 4. Already right (say so in the DPO meeting)

Consent gate + DPO link in the widget; fully local LLM (no cross-border AI
transfer); scope gates + refusal token (no fabrication); provenance labels
on every answer; kill-switched writes with confirmation UX; 30-min TTL
session memory (minimization by design); chat logs outside git (verified);
moderation designed around graded responses rather than blanket blocking.

## 5. Immediate action items

1. Retention policy for `logs/chat_history.db` + keep it out of any backup
   that syncs to personal accounts.
2. Per-admin auth + audit log to replace the shared `DASHBOARD_PIN`.
3. Untrack `data/cavsu_intents.db-shm/-wal` (runtime artifacts in git). ✔ done with this commit
4. University hosting (existing Decision 2) — now also a compliance argument.
5. Draft the privacy-notice additions (chat logging, moderation, retraining,
   connectors) for DPO review.
6. Schedule the PIA before the cvsu.edu.ph production launch.
