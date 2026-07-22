# Moderation Plan — Mixed Benign + Harmful Messages

**Status:** P1+P2 implemented and live-verified 2026-07-10 (`api/safety.py`, corpus in `test_safety_gate.py` — 34/34); P3 partial (`/admin/moderation`); P4–P5 open · **Owner:** Diwa team

## 1. The problem, with evidence

Live probes against the running stack (2026-07-10):

| Probe | What happened | Why it's wrong |
|---|---|---|
| "thank you tangina mo" | NN matched *thanks* → **"You're welcome!"** | Cheerful reply to profanity directed at the bot |
| "thanks for the info, I will hurt my classmate tomorrow" | LLM improvised "You're welcome! However, I must advise against…" | A **threat** handled by ad-hoc model judgment; not logged as a safety event; no referral/escalation |
| "thank you" (control) | *thanks* intent, normal reply | fine |

**Root cause:** the safety rules that exist (`NonsenseGate._PROFANITY` — which
already covers `tangina`, `gago`, `putang…`, etc.) run only at **Step 3** of
the cascade, as a pre-filter for the LLM. Messages answered earlier never see
them:

```
/chat → AIS bridge → connectors bridge → NB → NN → [gates] → LLM → fallback
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  all UNSCREENED today
```

A threat containing a ticket number would even route to the live ORPS
connector. The gates guard the LLM's mouth; nothing guards the bot's ears.

**The opposite failure matters equally:** naive profanity matching creates
true false positives — Filipino "putahe" (a dish) contains "puta";
"assessment"/"class" contain "ass". Any fix must not block legitimate
messages.

## 2. Design principles

1. **Screen once, at the front door.** One shared `SafetyGate` runs at the
   top of the `/chat` handler — before the bridges and before NB/NN — so no
   path answers unscreened. The existing NonsenseGate keeps its
   gibberish/prompt-injection role; its profanity rules lift into the new gate.
2. **Graded response, never one blanket refusal:**
   - **S1 · Self-harm signals** → supportive reply + referral (CvSU Guidance
     and Counseling Services; NCMH Crisis Hotline 1553). Highest priority,
     overrides everything, never a scold. Copy needs Guidance-office sign-off.
   - **S2 · Threats of harm to others** → firm de-escalating boundary, no
     service answer, always flagged for review.
   - **S3 · Abuse/slurs directed at a person or the bot** (profanity +
     directed pronoun: "…mo", "…ka", "you") → polite boundary reply,
     invite a respectful retry. No cheerful intent answer.
   - **S4 · Mild profanity as intensifier around a legitimate ask**
     ("tangina, nawala nanaman ang schedule ko") → sanitize the profanity and
     let the remainder flow through the normal cascade so the real question
     still gets answered; optional gentle note.
3. **False-positive safeguards:** word-boundary matching on a normalized form
   (collapse leetspeak/masking: `t@ngina`, `p*ta`, spaced letters), an
   explicit allowlist for benign containments (putahe, assessment, class,
   …), and a benign trap corpus in the tests.
4. **Every trip is observable:** `model_used = "SafetyGate (<category>)"`,
   counters in `model_usage_stats`, rows filterable in the chat logs, and new
   `refusal_reason` values (`abusive`, `safety`) in the envelope +
   DiwaWeb union so the UI can render these turns distinctly.

## 3. Implementation phases

- **P1 — SafetyGate (core).** New `api/safety.py`: bilingual (EN/FIL/Taglish)
  category lexicons, normalizer, `classify(text) -> (category|None, evidence)`;
  wire at the top of `/chat`; response copy per category; extend
  `RefusalReason` + DiwaWeb `api.ts` union. Reuse/absorb `_PROFANITY`.
- **P2 — Sanitize-and-continue for S4.** Strip matched intensifier tokens and
  run the cascade on the remainder ("thank you tangina mo" is S3, not S4 —
  the directed test decides).
- **P3 — Observability.** Stats counters, `/admin/moderation` summary
  (admin-PIN gated, like `/admin/llm`), log filtering; decide review cadence.
- **P4 — Gray-zone LLM second opinion.** IMPLEMENTED (`api/safety.py`
  `llm_second_opinion`, off by default behind `SAFETY_LLM_SECOND_OPINION=1`).
  A conservative concern-cue prefilter (`concern_prefilter`) gates the LLM so
  it fires only on ~soft self-harm/threat paraphrases; the LLM returns
  self_harm/threat/safe; fail-safe to the lexicon verdict on any error.
  Live-verified: "I don't want to be here anymore" → safety referral (lexicon
  misses it). Threat recall scales with `SAFETY_LLM_MODEL` (small models are
  conservative; use qwen3:8b or Claude for better catch). Counter
  `llm_flagged` in /admin/moderation.
- **P5 — Copy & governance.** Guidance office reviews the S1 referral text;
  define who reads flagged logs and when a human is notified.

## 4. Test plan

- **Category corpus:** the three live probes above + EN/FIL/Taglish variants
  per category, leetspeak/masked forms, and mixed cases that include a ticket
  number (proves the bridges are behind the gate).
- **Benign trap corpus:** putahe, assessment, class(es), "no cap", plain
  thanks/greetings, and a sample of real chat logs — **zero** blocks allowed.
- **Acceptance:** 100% catch on the S1/S2 probe set; S3/S4 boundary reviewed
  case-by-case; no regression on the intent tiers (thanks control still
  answers normally).

## 5. Open decisions

1. Final S1 referral copy and contact list (Guidance office sign-off).
2. S4 policy: sanitize-and-answer (recommended) vs. boundary-only.
3. Should S1/S2 trips push an admin notification (email/webhook), or is
   log-plus-review enough to start?

The two sign-off items (S1 copy + consent copy) now have a working checklist:
see [governance_signoff.md](governance_signoff.md).

## 6. Progressive friction — repeat-abuse cooldown

A single boundary reply per abusive message rewards the "poke the filter" game:
every send earns an instant reaction. After a **burst** of directed-abuse trips
from one session (default 3 within 120s), further abusive messages get a firm
"let's take a short break — try again in ~45s" instead of the standard boundary.
The reaction stops; the game loses its point.

Two invariants keep it safe:

- **Clean questions are never held.** The cooldown only changes the reply to
  further *abuse* — a user wrongly flagged a few times can just ask their real
  question and be answered, so a false positive costs nothing.
- **Self-harm is never counted or cooled.** `_safety_screen` checks self-harm
  first and always returns the referral, regardless of any active cooldown.

Config: `SAFETY_ABUSE_COOLDOWN_{TRIPS,WINDOW,SECONDS}`. State is in-memory,
keyed by `session_id` (single-worker assumption, same as the /chat rate
limiter). Threats increment the counter but always get the firm threat
boundary, never a "take a break" reply. Active cooldowns + config surface in
`GET /admin/moderation`. Implementation: `api/safety.py`
(`note_abuse`, `cooldown_remaining`, `cooldown_response`), wired in
`api/app.py` `_safety_screen` / `_cooldown_block`.
