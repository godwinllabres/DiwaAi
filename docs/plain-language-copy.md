# Plain-language copy — API-side record

**Date:** 2026-07-25
**Story:** As a user, I want the terminology, labelling, and error/alert/display
messages to be mostly free of jargon.

The full standard and the audit of every changed surface live in
**`sevi-web/docs/plain-language-copy.md`**. This file records the API-side half
so `git log` in this repo leads somewhere.

---

## Where the change actually lives

`api/hybrid_chatbot.py` was edited as part of this pass, but a concurrent
`git commit -a` swept the working tree into an unrelated commit before it could
be committed on its own:

| File | Commit it landed in |
|---|---|
| `api/hybrid_chatbot.py` | `9b89e37` — *fix: regressions found by the pre-merge review* |

That message describes auth/deploy regression work and says nothing about copy.
History was left alone rather than rewritten, because the commit was already
pushed to `origin` and `personal`.

## What changed

`ScopeGate.REFUSAL_MESSAGES[1]`:

```diff
-"That's outside my scope. I'm Sevi, the CvSU virtual assistant — I focus on …"
+"That's not something I can help with. I'm Sevi, the CvSU virtual assistant — I stick to …"
```

"Scope" is a term the system uses about itself, not one a student uses. The other
two refusals in that list were already plain.

## Deliberately not changed

- **`api/safety.py`** — the self-harm, threat, and abuse responses are already
  written in plain, supportive language, and the module carries a *"placeholder
  pending Guidance-office sign-off"* marker (`docs/moderation_plan.md` §5.1).
  Wording there is not ours to change unilaterally.
- **`nlu_fallback` intent copy** (`data/cavsu_intents*.json`) — already plain.
- **4xx/5xx `HTTPException` details** in `api/app.py` (`"reason must be one of …"`,
  `"Batch too large (max N)"`, `"Unknown action: …"`) — these are API-contract
  errors for developers, and the web client no longer surfaces raw server text to
  users anyway: `useChat.plainApiError()` rewrites every failure before it reaches
  a banner.

## Verification

Syntax check only (`python -m py_compile`). No environment on the machine had
`pytest` and `fastapi` installed, so the suite was not run. Nothing in the repo
asserts on the changed string — confirmed by grep across `test_*.py` and `api/`.
