# Messenger Channel Gateway (POC)

**Status:** proof of concept, OFF by default (`MESSENGER_ENABLED` unset → the
router is never mounted and the API surface is unchanged).
**Code:** `api/channels/messenger.py` · **Tests:** `python test_messenger_gateway.py`

## Why this exists

Theme 2 of the 2026-08 research synthesis
(`docs/RESEARCH-SYNTHESIS-IMPROVEMENTS-20260811.md`): Messenger is used by
~90% of Philippine internet users, and zero-rated prepaid promos keep it
reachable when sevi.cvsu.edu.ph is not — web-only structurally excludes the
students with the least load. Every outcome-bearing PH deployment surveyed
(Globe Gie, DOH KIRA, SSS, BPI BEA) is Messenger-first. The gateway is a pure
adapter over `POST /chat`: no tier, threshold, or safety behavior changes, and
every Messenger turn is logged, screened, and PII-masked exactly like a web
turn.

## Architecture

```
Meta webhook ──POST /channels/messenger/webhook──▶ verify X-Hub-Signature-256
                                                   dedupe message ids
                                                   PSID → salted hash (msgr-…)
                                                   POST /chat  (same pipeline)
                                                   ChatResponse → Send API msgs
Meta Send API ◀── text chunks ≤2000 · quick replies ≤13 · directory/map cards
```

Design decisions worth knowing:

- **The gateway speaks the public `/chat` contract over HTTP** instead of
  importing `api.app` internals — it stays deployable as a sidecar later, and
  the tests stub the two transport seams (`_call_chat`, `_send_message`).
- **The raw PSID never reaches the pipeline or its logs.** `user_id` /
  `session_id` are `msgr-<sha256(salt+psid)[:16]>` (RA 10173 data
  minimization). Replies are addressed to the raw PSID only inside the Send
  API call.
- **First contact per PSID gets a bilingual AI-disclosure message** (NPC
  Advisory No. 2024-04: disclose AI processing; answers can be wrong; where
  the humans are). Tracking is in-memory for the POC, so a restart re-sends
  it — over-disclosure is the safe failure direction.
- **Webhook always answers 200 for a verified batch**; each event is handled
  in its own try/except (one poison event must not trigger Meta redelivery of
  its siblings), and message ids are deduped against redelivery.
- **Card mapping is best-effort:** directory cards → contact text block, map
  cards → link to the web app, unknown/DV/table kinds are skipped (web-only
  surfaces). Quick replies ride the last message (platform rule), capped at
  13 × 20-char titles.

## Setup (when you're ready to pilot)

1. **Meta side:** create a Meta app + a CvSU-owned Facebook Page; add the
   Messenger product; generate a **page access token**; note the **app
   secret**; subscribe the webhook to `messages` with callback URL
   `https://<host>/channels/messenger/webhook` and a **verify token** you
   invent.
2. **Sevi side** (`sevi-deploy` env — same file that carries the other
   secrets):

   ```bash
   MESSENGER_ENABLED=1
   MESSENGER_VERIFY_TOKEN=<the string you gave Meta>
   MESSENGER_APP_SECRET=<meta app secret>
   MESSENGER_PAGE_TOKEN=<page access token>
   MESSENGER_PSID_SALT=<any long random string>   # optional; defaults to app secret
   # MESSENGER_CHAT_URL=http://127.0.0.1:8000/chat  # container-internal default
   ```

3. Restart the API — the boot log prints
   `Messenger channel gateway mounted at /channels/messenger`.
4. In the Meta app dashboard, verify the webhook (Meta calls the GET
   handshake), then message the Page from a tester account.

## Compliance checklist before a public pilot

- [ ] Update the privacy notice + PIA for the new processing surface
      (Messenger PSIDs, hashed; Meta as a channel processor) — DPO sign-off,
      same flow as `docs/governance_signoff.md`.
- [ ] Keep proactive/outbound messaging OFF until opt-in tags are designed —
      the POC only ever replies inside Meta's standard messaging window.
- [ ] Page verification + takedown contact documented with ICTO.

## Limitations (POC, by design)

- In-memory dedupe/disclosure state — single worker, resets on restart (the
  planned Redis consolidation covers this together with throttles/sessions).
- No attachment handling: images/stickers get no answer (silently skipped).
- No live-agent handoff yet — pairs with the escalation subsystem
  (research synthesis §2.2b) when that lands.
