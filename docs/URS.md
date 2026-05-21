# User Requirements Specification (URS)

**Product:** DIWA — Digital Intelligent Web Assistant for Cavite State University
**Project codename:** SeviAI
**Document version:** 1.0
**Date:** 2026-05-21
**Owner:** Godwin Llabres (godwinlorenz.llabres@cvsu.edu.ph)
**Status:** Baseline (production system in deployment)

---

## 1. Introduction

### 1.1 Purpose
This URS defines what users need from DIWA, the public-facing virtual assistant for Cavite State University (CvSU). It describes the system from the user's point of view: who uses it, what they must be able to do, and the quality attributes the system must satisfy. It is the source of truth for scope decisions and acceptance criteria.

### 1.2 Scope
DIWA answers natural-language questions from prospective students, current students, parents, faculty, and the general public about CvSU programs, admissions, campus services, scholarships, fees, schedules, policies, and campus locations. It also includes an authenticated administrative surface for monitoring usage, curating intents, managing the interactive campus map, and reviewing user feedback.

DIWA does **not** process enrollment, payments, grade disputes, or official document requests. It always redirects high-stakes actions to the responsible CvSU office.

### 1.3 Definitions and acronyms
| Term | Meaning |
|---|---|
| DIWA | Digital Intelligent Web Assistant — the chatbot product |
| CvSU | Cavite State University |
| NLU | Natural Language Understanding |
| NB | Naive Bayes (the primary intent classifier) |
| Intent | A labelled category of user request (e.g. `admissions_requirements`) |
| Fallback | Response served when no intent matches with sufficient confidence |
| AIS | Accounting Information System (CvSU finance backend) |
| MCP | Model Context Protocol — used by the optional AIS finance bridge |
| Charter | CvSU Citizen's Charter (2026 edition) — official services reference |
| OSAS | Office of Student Affairs and Services |
| PIN | Numeric secret used to authenticate admin/analytics access |

### 1.4 References
- [README.md](../README.md) — system overview and quick-start
- [docs/PROJECT_STATUS.md](PROJECT_STATUS.md) — current implementation snapshot
- [docs/NLU_IMPLEMENTATION.md](NLU_IMPLEMENTATION.md) — NLU engine internals
- [docs/API_README.md](API_README.md) — REST API reference
- [docs/citizens-charter-2026-edition.pdf](citizens-charter-2026-edition.pdf) — authoritative service catalogue
- [api/app.py](../api/app.py) — primary backend implementation

---

## 2. Stakeholders and user classes

| User class | Description | Authentication |
|---|---|---|
| **Prospective applicant** | High-school student or transferee asking about admissions, programs, requirements, fees | Anonymous |
| **Current student** | CvSU student asking about schedules, services, registrar, scholarships | Anonymous |
| **Parent / guardian** | Asks about fees, scholarships, application process on behalf of an applicant | Anonymous |
| **Faculty / staff (general)** | Asks general informational questions about campus services | Anonymous |
| **Visitor / public** | Asks about location, contact, vision/mission | Anonymous |
| **Campus visitor (in-person)** | Uses the interactive map for wayfinding around the Indang main campus | Anonymous |
| **Finance / accounting staff** | Uses the optional AIS finance lookups (DV status, budget balances, UACS) through DIWA chat | Anonymous to DIWA; identity asserted by the AIS MCP server's own OAuth token |
| **DIWA administrator** | Curates intents, monitors logs, manages the campus map and feedback queue | PIN-protected admin endpoints |
| **DIWA content reviewer** | Reviews user feedback, fallback queries, and proposed new intents | PIN-protected admin endpoints |

---

## 3. System context and assumptions

### 3.1 Operating environment
- Backend: Python 3.11 FastAPI service (production), deployable via Docker (`deployment/Dockerfile`) or Render (`render.yaml`).
- Frontend: React/Vite web app (`web/app/`) plus a static HTML chat surface (`web/web_interface.html`) and an analytics dashboard (`web/logs_dashboard.html`).
- Data stores: SQLite for intents (`data/cavsu_intents.db`), chat history (`logs/chat_history.db`), and operational state. JSON exports kept as backups.
- Optional dependencies: a local Ollama server for LLM routing on AIS queries; an AIS MCP server (CvSU Accounting) for finance lookups.

### 3.2 Assumptions
- Users access DIWA over the public internet from desktop or mobile browsers.
- The CvSU Citizen's Charter is the authoritative source for service descriptions, deadlines, and contact paths.
- Time-sensitive content (deadlines, fees, exam schedules) requires periodic refresh by an administrator.
- The AIS MCP server, when present, runs on the same network as the API and shares a single OAuth identity (segregation of duties does not apply to MCP-routed queries).
- A single uvicorn worker is the supported topology for in-memory rate limiting; multi-worker deployments need an external store (Redis) to keep limits coherent.

### 3.3 Constraints
- DIWA must not collect, store, or display personally identifiable information about specific students, in line with Republic Act 10173 (Data Privacy Act).
- DIWA must not transact: no enrollment, payment, document request, or grade-related action.
- DIWA must not give legal interpretations of university policy.
- DIWA must not fabricate figures, names, deadlines, or course codes.
- The classifier must run on commodity Python without GPU dependencies for the baseline NB model.

---

## 4. Functional user requirements

Each requirement is identified `UR-<area>-<n>` and uses the form "The system shall…". Acceptance criteria are stated where useful.

### 4.1 Conversational core (UR-CHAT)

- **UR-CHAT-01** The system shall accept a free-text message from an anonymous user and return an answer in the same language register the user used (English, Filipino, or Taglish).
- **UR-CHAT-02** The system shall classify each message into one of the curated intents (≥120 intents in the baseline) or return a fallback when no intent meets the confidence threshold.
- **UR-CHAT-03** The system shall return an answer within 1 second end-to-end for the baseline NB path under normal load (target: <75 ms server-side classification).
- **UR-CHAT-04** The system shall, for time-sensitive answers (deadlines, fees, schedules), include a qualifier and direct the user to verify with the responsible office.
- **UR-CHAT-05** The system shall maintain per-session conversational context (last DV, UACS query, report, last intent) for at least 10 minutes to support follow-up questions ("what's its status?") without re-stating the subject.
- **UR-CHAT-06** The system shall offer up to four follow-up suggestion chips after each answer so a user can continue without retyping.
- **UR-CHAT-07** The system shall return a structured response object containing: the answer text, a short UI summary, the resolved intent, optional map data, optional directory card, optional AIS DV card, optional results table, the active context, and suggestion chips.
- **UR-CHAT-08** The system shall refuse to answer (with a graceful redirect) when asked to predict individual admission outcomes, compare CvSU unfavourably with other institutions, give legal interpretations, provide policy workarounds, or share personal contact details of staff.
- **UR-CHAT-09** The system shall detect and silently absorb nonsense input (random characters, profanity-only messages, gibberish) without producing a misleading "best-guess" intent.

### 4.2 Disambiguation and campus scope (UR-DISAMB)

- **UR-DISAMB-01** The system shall ask one targeted clarifying question when the campus matters and the user did not name one (Indang, Imus, Rosario, Silang, Naic, Trece Martires, Tanza, General Trias, Carmona, Cavite City, Bacoor, and other satellite campuses).
- **UR-DISAMB-02** The system shall ask one targeted clarifying question when the applicant type (freshman, transferee, graduate) determines the answer.
- **UR-DISAMB-03** The system shall limit clarifying questions to one per turn unless answering correctly is impossible without more information.

### 4.3 Campus map and wayfinding (UR-MAP)

- **UR-MAP-01** The system shall expose an interactive map of the Indang main campus with at least 48 official place markers.
- **UR-MAP-02** The system shall return map data (coordinates, label, waypoints) inline with a chat answer when the user asks where a place is or how to get to it.
- **UR-MAP-03** The system shall let an administrator override marker coordinates, add custom markers, and edit the waypoint graph (with optional adjacency lists for routing) without code changes.
- **UR-MAP-04** The system shall persist coordinate and waypoint overrides in the database and survive restarts.
- **UR-MAP-05** The system shall return a `PlaceMeta` record for a known place ID, including display name, coordinates, and any directory details (office, contact, hours).

### 4.4 Topic recommendations (UR-TOPIC)

- **UR-TOPIC-01** The system shall surface seasonal topic chips (e.g. enrolment opens, CvSUAT, scholarship deadlines) so a first-time user has a starting point.
- **UR-TOPIC-02** The system shall let an administrator define season windows that drive the recommended topics.

### 4.5 Feedback (UR-FB)

- **UR-FB-01** The system shall let a user rate an answer as helpful or not helpful, with optional 1–5 star rating and free-text comment.
- **UR-FB-02** The system shall offer a structured reason taxonomy (e.g. *wrong info*, *missing key details*, *outdated*, *confusing*, *answered something else*) so feedback is classifiable.
- **UR-FB-03** The system shall accept a user-suggested intent label or alternative phrasing as part of a feedback submission.
- **UR-FB-04** The system shall let an administrator browse feedback, view fallback queries, and run aggregate analyses (per session, per user, per intent).

### 4.6 AIS finance bridge (UR-AIS) — optional / staff

> Staff-facing capability surfaced through the same chat endpoint when the optional AIS MCP bridge is enabled. When the bridge is disabled or unreachable, DIWA falls back to its normal NLU pipeline.

- **UR-AIS-01** The system shall, when enabled, route finance-shaped queries (disbursement vouchers, budget balances, RAPAL/RAOD/RBUD reports, UACS lookups) to the AIS MCP server.
- **UR-AIS-02** The system shall return a structured `dv_card` for a successful DV lookup, including control number, payee, amount, workflow status, posting date, fund cluster, OR/BURS reference, DV type, and a Desk deep-link.
- **UR-AIS-03** The system shall return a multi-row table for list/find/group AIS results, with a plain-text fallback for older clients.
- **UR-AIS-04** The system shall enforce a hard ceiling on any single MCP call (default 8 seconds) and trip a circuit breaker after repeated failures to stop a downed MCP server from hanging every chat.
- **UR-AIS-05** The system shall make it possible for an admin tool to call a specific AIS MCP tool by name via `intent_hint` and `intent_args`, bypassing the NLU router.
- **UR-AIS-06** The system shall expose an admin-only metrics snapshot of the AIS MCP bridge (call counts, latencies, breaker state).

### 4.7 Administration and content curation (UR-ADMIN)

- **UR-ADMIN-01** The system shall authenticate administrators using a server-side PIN supplied in the `X-Admin-Pin` header.
- **UR-ADMIN-02** The system shall rate-limit PIN attempts per source IP (default: 5 attempts per 5 minutes) and return HTTP 429 when the budget is exhausted.
- **UR-ADMIN-03** The system shall let an administrator sanitise a candidate intent (collision detection against existing intents, classifier-confidence pre-check) before persisting it.
- **UR-ADMIN-04** The system shall let an administrator add a new intent (tag, patterns, responses) with optional `?force=true` to override non-fatal sanitation errors, and shall tell the caller to re-train and restart to activate it.
- **UR-ADMIN-05** The system shall let an administrator reload the live classifier without restarting the process.
- **UR-ADMIN-06** The system shall expose only sanitised model information to unauthenticated callers (intent count); detailed model internals shall be admin-only.

### 4.8 Analytics and operations (UR-OPS)

- **UR-OPS-01** The system shall log every chat exchange (message, resolved intent, confidence, session, user pseudonym) to a local SQLite database with rolling JSON backups.
- **UR-OPS-02** The system shall provide admin endpoints for: today's totals, intent breakdown, sessions list, user history, session history, full-text search of logs, and per-user export.
- **UR-OPS-03** The system shall let an administrator delete logs older than a configurable retention window.
- **UR-OPS-04** The system shall expose a public `/health` endpoint that returns OK while the API process is alive.
- **UR-OPS-05** The system shall ship a real-time analytics dashboard (`web/logs_dashboard.html`) that auto-refreshes and surfaces totals, top intents, and recent activity.

### 4.9 Batch and integration (UR-INT)

- **UR-INT-01** The system shall accept a batch of messages on `POST /batch` for offline evaluation and testing.
- **UR-INT-02** The system shall serve OpenAPI/Swagger documentation at `/docs` in non-production environments and suppress it in production.
- **UR-INT-03** The system shall accept CORS only from an explicit allow-list of origins (no wildcards in production), configurable via `CORS_ORIGINS`.

---

## 5. Non-functional requirements

### 5.1 Performance
- **NFR-PERF-01** Median chat round-trip ≤ 1 s under normal load; classification ≤ 75 ms server-side.
- **NFR-PERF-02** Async log writes shall not block the chat response path (target ≤ 20 ms log overhead).
- **NFR-PERF-03** Trained NB model size ≤ 100 KB so the service starts cold in seconds.

### 5.2 Reliability
- **NFR-REL-01** The chat endpoint shall remain available when the optional AIS MCP server is unreachable; failures shall fall back to normal NLU.
- **NFR-REL-02** The system shall recover automatically from transient MCP transport failures via a circuit breaker (default: 3 consecutive failures → 30 s cooldown).
- **NFR-REL-03** A failed model load at startup shall produce a clear error in the logs and a degraded but still-reachable `/health` endpoint.

### 5.3 Security
- **NFR-SEC-01** All admin endpoints shall require a valid PIN; PIN comparisons shall be constant-time-safe (string equality on a server-side secret).
- **NFR-SEC-02** Per-session chat rate limit (default: 30 requests / 60 s) shall protect the anonymous `/chat` endpoint from burst abuse.
- **NFR-SEC-03** Internal error details shall never be returned to the client; the server shall log full detail and return a generic message keyed by status code.
- **NFR-SEC-04** CORS shall be configured with an explicit origin allow-list; wildcards are forbidden in production.
- **NFR-SEC-05** OpenAPI, ReDoc, and the raw OpenAPI schema shall be disabled when `RENDER` or `PRODUCTION` env vars are set.
- **NFR-SEC-06** The system shall not request, store, or display personally identifying information about students; it shall redirect personal inquiries to the registrar or guidance office.

### 5.4 Privacy and compliance
- **NFR-PRIV-01** All anonymous chats shall be storable under a pseudonymous session ID; users shall not be required to log in.
- **NFR-PRIV-02** Per-user export and per-user deletion shall be supported by admin endpoints to satisfy data-subject requests under RA 10173.

### 5.5 Usability
- **NFR-UX-01** Responses shall lead with the direct answer, then supporting detail, then caveats and verification reminders.
- **NFR-UX-02** Responses shall offer next steps ("Is there anything else I can help you with?").
- **NFR-UX-03** The web chat surface shall be responsive and work on phones, tablets, and desktop browsers (latest Chrome, Edge, Firefox, Safari).
- **NFR-UX-04** The chat shall preserve conversation history within a session for the user's own scrollback.

### 5.6 Localisation
- **NFR-LOC-01** The assistant shall reply in English, Filipino, or Taglish, matching the user's input register.
- **NFR-LOC-02** Official Filipino academic terms (e.g. *Pagsusulit sa Pagpasok*, *Rehistrar*) shall be used when discussing those concepts.

### 5.7 Maintainability
- **NFR-MAINT-01** Intents shall be editable through the admin API and persisted in SQLite, with a JSON export kept as a backup.
- **NFR-MAINT-02** Retraining shall be a single-command operation (`python training/train_naive_bayes.py`) that produces a model artefact and an evaluation summary.
- **NFR-MAINT-03** Documentation shall live under `docs/` and shall be the canonical onboarding path for a new operator.

### 5.8 Portability and deployment
- **NFR-DEP-01** The system shall be runnable from a clean checkout on Python 3.11 within one command (`uvicorn api.app:app`).
- **NFR-DEP-02** A Docker image (`deployment/Dockerfile`) shall produce a runnable container; a docker-compose file shall start the API with persistent log/data volumes.
- **NFR-DEP-03** A Render deployment recipe (`render.yaml`, `Dockerfile.render`) shall produce a hosted deployment without modification.

### 5.9 Observability
- **NFR-OBS-01** Every chat exchange shall be logged with timestamp, session ID, user pseudonym, message, resolved intent, confidence, and the path that handled it (NB, AIS MCP, fallback).
- **NFR-OBS-02** The AIS MCP bridge shall publish a metrics snapshot (counts, latencies, breaker state) for admin inspection.

---

## 6. External interface requirements

### 6.1 User interfaces
- React/Vite web app at `web/app/` (primary student/visitor surface).
- Static chat HTML at `web/web_interface.html` (fallback / embeddable widget).
- Analytics dashboard at `web/logs_dashboard.html` (admin).
- Printable QR code asset at `data/diwa_qr.png` for in-campus posters.

### 6.2 API interfaces (selected, see `/docs` for full schema)
- `POST /chat` — primary conversational endpoint.
- `POST /batch` — batch evaluation.
- `GET /health`, `GET /` — health and liveness.
- `GET /map`, `GET /map/coords`, `GET /map/waypoints`, `GET /map/custom_markers`, `GET /map/{place_id}` — campus map (public reads).
- `PUT/DELETE /map/...` — admin map mutations.
- `GET /intents`, `GET /intents/{tag}`, `GET /model/info` — intent and model introspection.
- `POST /feedback`, `GET /feedback/*` — feedback capture and (admin) review.
- `GET /topics/recommended` — seasonal topic chips.
- `GET /logs/*`, `POST /logs/export/{user_id}`, `DELETE /logs/cleanup` — admin analytics.
- `POST /admin/verify`, `POST /admin/intents`, `POST /admin/intents/sanitize`, `POST /model/reload`, `GET /ais_mcp_stats` — admin operations.

### 6.3 External system interfaces
- **AIS MCP server** (optional): SSE endpoint at `AIS_MCP_URL` (default `http://127.0.0.1:8765/sse`). The server holds a single OAuth identity; DIWA users do not authenticate to it individually.
- **Ollama** (optional): used by the opt-in LLM router for AIS query extraction when regex routing misses (`OLLAMA_BASE_URL`, default `http://localhost:11434`).
- **Anthropic API** (optional, secondary fallback): used by the same LLM router when configured via `LLM_PROVIDER=claude`.
- **CvSU Desk** (link-out only): DV cards deep-link to the Desk record at `AIS_DESK_URL`.

### 6.4 Configuration interface (env vars)
| Variable | Purpose | Default |
|---|---|---|
| `CORS_ORIGINS` | Explicit allow-list of frontend origins | `http://localhost:5173` |
| `DASHBOARD_PIN` | PIN that gates all admin endpoints | unset (admin disabled) |
| `CHAT_RATE_LIMIT_MAX` / `CHAT_RATE_LIMIT_WINDOW` | Per-session chat rate limit | 30 / 60 s |
| `AIS_MCP_URL` / `AIS_MCP_ENABLED` | AIS bridge endpoint and toggle | `http://127.0.0.1:8765/sse` / `1` |
| `AIS_MCP_TIMEOUT_SECONDS` | Per-call MCP ceiling | 8.0 |
| `AIS_MCP_BREAKER_THRESHOLD` / `_COOLDOWN` | AIS circuit breaker | 3 / 30 s |
| `AIS_MCP_LLM_ROUTER` | Enable LLM extraction fallback for AIS routing | `0` |
| `AIS_DESK_URL` | Base URL for DV deep-links | `http://accounting.localhost:8002` |
| `RENDER` / `PRODUCTION` | Hide `/docs`, `/redoc`, `/openapi.json` | unset |

---

## 7. Data requirements

- **Intent corpus:** ≥120 intents covering admissions, programs, fees, scholarships, registrar, campus services, location, events, vision/mission, charter-sourced services, greetings, and small-talk. Backed by SQLite (`data/cavsu_intents.db`) with JSON export (`data/cavsu_intents.json`).
- **Campus places:** 48 baseline locations with coordinates, plus user-defined waypoints, custom markers, and adjacency for routing.
- **Chat history:** SQLite (`logs/chat_history.db`) with daily JSON backups.
- **Responses map:** intent → response template(s) (`models/responses_map.json`).
- **Model artefact:** serialised NB pipeline (`models/cavsu_classifier.pkl`, ≤100 KB).

---

## 8. Acceptance criteria (rollup)

A release of DIWA is acceptable when:

1. **Accuracy:** baseline NB classifier reports ≥95% training accuracy and ≥85% holdout accuracy on the curated intent corpus.
2. **Latency:** median `/chat` round-trip ≤1 s on a 1-vCPU production node; classification ≤75 ms.
3. **Coverage:** all charter-sourced intents resolve to a non-fallback response.
4. **Safety:** the nonsense-gate suppresses gibberish; the assistant refuses each prohibited request class from §4.1 (UR-CHAT-08).
5. **Admin auth:** every admin endpoint returns 401 without a valid PIN and 429 after the configured brute-force window.
6. **Privacy:** no endpoint returns a response containing a real student's name, ID, grades, or contact details.
7. **Map:** the 48 baseline places render; admin overrides persist across restart.
8. **Feedback:** a user can submit a rating, reason, and comment; an admin can browse, filter, and export.
9. **AIS bridge (when enabled):** DV lookup returns a `dv_card` with a working Desk deep-link; the circuit breaker activates after the configured failure count and recovers after the cooldown.
10. **Deployment:** `docker compose -f deployment/docker-compose.yml up -d` brings the service to a healthy state on a clean host.

---

## 9. Out of scope

The following are explicitly **not** DIWA requirements in this baseline:

- Enrollment processing, payment processing, or document request issuance.
- Storing or retrieving any individual student's records.
- Replacing the staff-facing AIS Desk UI or the Claude-Desktop AIS MCP surface.
- Voice input/output.
- Mobile-native apps (the web surface is responsive instead).
- Multi-tenant or multi-institution support.
- Bridging the anonymous student chat to authenticated Frappe / `chat.relay` calls.

---

## 10. Change control

| Version | Date | Author | Summary |
|---|---|---|---|
| 1.0 | 2026-05-21 | Godwin Llabres | Baseline URS captured from the production system (DIWA v1.0). |
