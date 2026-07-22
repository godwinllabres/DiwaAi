# Governance Sign-off — Sevi safety & privacy copy

Two pieces of user-facing copy in Sevi are **placeholders pending an owner's
formal approval**. Shipping them without sign-off is the single biggest
governance gap in the product: when a student in crisis reads the self-harm
referral, or a parent questions what the consent notice promised, the copy has
to be defensible by the office that owns it — not by engineering.

This checklist turns "pending sign-off" into a closed loop. It also records the
technical controls that back the promises the copy makes, so the reviewing
offices can see that the words match the system's behavior.

Owners: **CvSU Guidance and Counseling Services** (crisis copy) and the
**Data Protection Officer** (consent + retention). Related engineering docs:
[moderation_plan.md](moderation_plan.md) §5–6, [privacy_compliance.md](privacy_compliance.md) §3, §5.

---

## 1. Crisis / self-harm referral — Guidance office sign-off

**Copy under review** (`api/safety.py` → `RESPONSES["self_harm"]`, shown verbatim,
never rewritten by any downstream tier):

> I'm really sorry you're going through this — you matter, and you don't have to
> face it alone. Please reach out to the **CvSU Guidance and Counseling Services**
> on your campus, or call the **NCMH Crisis Hotline at 1553** (toll-free, 24/7).
> If you're in immediate danger, contact campus security or 911. Kung gusto mo,
> nandito lang ako para sa impormasyon tungkol sa mga student support services ng CvSU.

Also in scope: the `mental_health_immediate` intent copy (Hopeline numbers) and
the threat-boundary copy (`RESPONSES["threat"]`).

### Review checklist

- [ ] **Tone** is supportive and non-clinical; never scolds or diagnoses.
- [ ] **NCMH 1553** is current (toll-free, 24/7) — re-verify the number.
- [ ] **Hopeline PH** numbers in `mental_health_immediate` are current:
      (02) 8804-4673 / 0917 558-4673 / 0918 873-4673.
- [ ] **Campus escalation path** named correctly ("Guidance and Counseling
      Services", campus security, 911) and matches each campus's real service.
- [ ] **Filipino line** is warm and accurate; no machine-translation stiffness.
- [ ] **Escalation policy decided**: does a self-harm trip notify a human
      (email/webhook to Guidance), or is log-plus-review enough to start?
      (moderation_plan.md §5, open decision 3.)
- [ ] **Limits stated honestly**: Sevi is not a counselor and does not follow
      up — the copy points to humans who do.

### System behavior backing this copy (for the reviewer)

- Self-harm is detected at the **front door**, before any other tier, so it can
  never be answered cheerfully by an intent or the LLM.
- Self-harm is **never rate-limited or cooled down**: even a session already in
  an abuse cooldown, or one sending abuse in the same breath, still receives
  this referral (enforced in `_safety_screen`, tested).
- An opt-in **LLM second opinion** catches paraphrased disclosures the wordlist
  misses ("I don't want to be here anymore").

Signed (Guidance office): ______________________  Date: ____________

---

## 2. Consent / privacy notice — DPO sign-off

**Copy under review** (`DiwaWeb/app/App.tsx` → `CONSENT_PROMPT_TEXT`, first
message shown before any chat is possible):

> Hello and welcome to Cavite State University! I'm Sevi — the CvSU Virtual
> Assistant. Before we begin: your messages are logged to improve this service,
> automatically screened for safety, and may be used to make Sevi's answers
> better. For live records (documents, tickets, accounts) I look them up from the
> relevant CvSU system under your own access — I never ask for passwords in chat.
> Full details are in our [Data Privacy Notice](https://cvsu.edu.ph/office-of-the-data-protection-officer/general-data-privacy-notice/).
> If you agree, kindly click the **I Agree** button below.

### Review checklist (RA 10173 — Data Privacy Act)

- [ ] **Purposes stated** match reality: (a) service improvement, (b) safety
      screening, (c) model retraining. No purpose creep beyond these.
- [ ] **Retention disclosed**: rows are auto-deleted after
      `LOG_RETENTION_DAYS` (default 365). Confirm the number with the DPO and
      state it (or a link) in the full notice.
- [ ] **Minimization stated**: volunteered PII (student numbers, emails, phone
      numbers) is masked before storage (`LOG_MASK_PII`, default on). Free-text
      disclosures ("I have anxiety") cannot be auto-masked — note this residual.
- [ ] **Live-records clause** correct: lookups run under the user's own access;
      Sevi never asks for passwords in chat.
- [ ] **DPO notice link** resolves and covers rights (access, correction,
      erasure, complaint) and the DPO's contact.
- [ ] **Decline path**: today, declining simply blocks chat (no data collected).
      Confirm the DPO accepts "agree-to-use" with no partial mode.
- [ ] **PIA** for the chat service scheduled/complete (privacy_compliance.md §5).

### System behavior backing this copy (for the reviewer)

| Promise in the notice | Control | Where |
|---|---|---|
| "logged to improve this service" | audit log, indexed | `api/logger.py` |
| PII minimized before storage | email / phone / id masking on write | `api/pii.py`, `LOG_MASK_PII` |
| data not kept forever | daily retention sweep, `LOG_RETENTION_DAYS` | `api/app.py` `_retention_loop` |
| "screened for safety" | front-door SafetyGate + lexicon | `api/safety.py` |
| "under your own access" | per-session AIS auth; writes human-confirmed | `api/auth_ais.py` |

Signed (DPO): ______________________  Date: ____________

---

## 3. Where these are enforced in code

- Crisis copy: `api/safety.py` `RESPONSES`, ordering in `api/app.py` `_safety_screen`.
- Consent copy + gate: `DiwaWeb/app/App.tsx` (`CONSENT_PROMPT_TEXT`, `useConsent`).
- Masking + retention: `api/pii.py`, `api/logger.py`, `api/app.py` `_retention_loop`.
- Accuracy disclaimer (overtrust control): persistent line under the chat input,
  `DiwaWeb/app/App.tsx`.

## 4. The measurement loop (anti-pattern mining)

Sign-off is not one-and-done. Every month, run the anti-pattern report and bring
the emerging themes to the review:

```
python scripts/mine_anti_patterns.py --days 30
# or, live:  GET /admin/anti_patterns   (X-Admin-Pin)
```

It clusters unanswered fallbacks, off-topic refusals, safety trips, and
low-confidence answers into themes with examples — showing which new intents to
author, which lexicon gaps to close, and whether the crisis/abuse volume is
trending. Self-harm is **counted but never themed or quoted** — dignity first.
The messages it reads are already PII-masked (§2). This is what keeps the copy,
the lexicon, and the intent set honest release over release.
