# HANDOFF → Gemini Pro (sub-agent): expound the possible POCs for Sevi

**Orchestrator:** Claude Code (this repo, `SeviAI`).
**Sub-agent:** Gemini Pro.
**Date of brief:** 2026-07-23. Facts below are as-built at commit `7743ec9`
(branch `feat/classifier-taxonomy-cleanup`).

**User story this serves:** *As a user, I want a feature-ideation pass over Sevi
so I can see the possible proof-of-concept directions, scoped and ranked, before
committing build time to any one of them.*

---

## 0. Paste-ready prompt

> Copy everything from §1 to §7 into Gemini Pro. §8 is for the orchestrator.

---

## 1. Your role and authority

You are a **sub-agent** operating under a Claude Code orchestrator on the Sevi
codebase. You do **not** have repo write access and are **not** being asked to
write production code.

**You may:** propose POC features, sketch architecture, name the exact files a
change would touch, estimate effort/risk, and write short illustrative snippets.

**You must not:** invent facts about the codebase, claim a component exists that
is not listed in §2, propose anything requiring a new paid external dependency
without flagging it, or produce code intended to be merged unreviewed.

**Hard rule on grounding:** every proposal must anchor to at least one real
artifact named in §2 (a file, a tier, an endpoint, an env flag, a dataset). If
you cannot anchor it, mark the proposal `SPECULATIVE` and say what you'd need to
verify. Fabricated file paths are the single worst failure mode here — the
orchestrator will check them.

---

## 2. Ground truth: what Sevi is today

Sevi is the CvSU (Cavite State University) virtual assistant. Python/FastAPI
backend (`SeviAI`, this repo, ~14.5k LOC under `api/`), Next.js frontend
(`SeviWeb`, separate repo), containerised and deployed via a third repo
(`sevi-deploy`) to a CvSU UAT environment.

### 2.1 The routing cascade (the core design)

A `/chat` turn walks a **precision-first cascade**. Each tier may serve or
decline; declining falls through. `ResponseSource` in
[api/app.py:639-654](api/app.py#L639-L654) is the authoritative list:

```
Tier 1  Safety gate            api/safety.py         (crisis, abuse, profanity, PII)
Tier 5.5 Agentic workflow      api/workflows/        (stateful; OFF by default)
        Campus directory       api/campus_directory.py  (charter contact table)
        Place resolver         api/campus_places.py     (deterministic wayfinding)
        AIS / HR MCP           api/ais_mcp.py, api/hr_mcp.py  (internal mode only)
        Naive Bayes            api/hybrid_chatbot.py
        Neural network         (serves ONLY when it agrees with NB's top guess)
        Intent retrieval       api/intent_retrieval.py
        Charter RAG            api/charter_rag.py    (docs/citizens_charter_text.txt)
        Site RAG               api/site_rag.py       (docs/site_corpus.txt)
        LLM (local / Claude)   llama3.2:3b via host Ollama; qwen3:8b pulled, unused
        Fallback / Refusal     with typed RefusalReason
```

Response envelope v2 = text + typed cards (map, directory, DV detail, table) +
provenance (which tier served) + a `DisplayHint` for frontend layout.

**Standing design rule: precision beats recall.** An LLM-first routing
experiment was built, measured, and *rejected* on 2026-07-22 — slower and worse
on curated queries. Unguarded lowering of thresholds yields ~2–4 wrong answers
per extra correct one. Do not propose "just let the LLM handle it."

### 2.2 Measured state (268-question mirror-grounded eval)

- Eval set: `data/eval/mirror_qa.json`, 268 grounded QA (248 factual + 20
  adversarial) generated from the CvSU content mirror. Runners: `scripts/eval_*.py`.
- Latest classifier-tier numbers (LLM off): served 65/268, correct 51,
  **precision 78.5%**, **recall 22.7%** (up from 3.6%). Holdout 78.8%.
  Crisis phrasings 6/6 correctly routed to `mental_health_immediate`.
- Full-cascade live score was 51.1% at an earlier snapshot (pre-fine-grained
  intents — not directly comparable to the numbers above).
- Taxonomy: **124 intents**, one-owner rule enforced, 0 cross-intent duplicate
  patterns. Data in `data/cavsu_intents.json` + `data/cavsu_intents.db`.

**The honest gap:** recall. ~77% of grounded questions still fall past the
classifier tiers to RAG/LLM/fallback. The deep tail (per-college, per-program,
per-campus specifics) is not in the corpus yet.

### 2.3 What exists but is dormant

- **Agentic Tier 5.5** — one workflow (*book an advising appointment*), stateful,
  ends in a tool call. Tool is a **mock**; tier gated by `AGENTIC_WORKFLOWS_ENABLED`,
  unset by default. Design: `docs/agentic_workflows_poc.md`. Blocked on real
  student auth.
- **Internal copilot mode** — JWT-fenced (`SEVI_INTERNAL_AUTH_MODE=jwt`), embedded
  in the Frappe Desk, reads real AIS disbursement vouchers and HRIS DTR data with
  role tiering (full / self / none). Verified working; runs on the isolated
  `sevi-local` stack (:8091) rather than the public one (:8090).
- **Trust & safety controls** — moderation, privacy/consent, crisis copy. Built
  and tested; **blocked on human governance sign-off** (`docs/governance_signoff.md`).
- **Slot filling / joint NLU** — `api/joint_nlu.py`, `api/slot_schema.py`,
  `api/slot_metrics.py`, corpus under `data/slots/`.
- **Topic recommender** — `api/topic_recommender.py`.
- **Anti-pattern mining** — `api/anti_patterns.py`, `scripts/mine_anti_patterns.py`,
  mines real fallbacks out of the Postgres chat log.

### 2.4 Constraints you must respect

| Constraint | Detail |
|---|---|
| Precision-first | See §2.1. Never trade precision for recall without a guard. |
| Local LLM only | `llama3.2:3b` on host Ollama. Small. Fabricates on ~15/268 without tight grounding. Claude tier exists but external calls are cost/policy-sensitive. |
| Privacy | RA 10173 (PH Data Privacy Act). No storing personal data on the anonymous surface. PII scrubbing in `api/pii.py`. |
| Governance | Anything touching crisis copy, consent copy, or student records needs human sign-off before production. |
| Anonymous ≠ authenticated | The public surface has no user identity. Any per-student feature implies an auth story that does not exist yet. |
| Deploy | Images `sevi-api` / `sevi-web` are built and published by `sevi-deploy`; app repos must not self-publish. |
| Offline-ish | UAT runs against a *local content mirror*, not the live cvsu.edu.ph. |

### 2.5 Already-agreed roadmap (do not re-propose as new)

1. ~~Intent taxonomy cleanup + retrain~~ — **DONE** 2026-07-22.
2. Close the grounding gap — add college/program catalog pages to the site RAG corpus.
3. Chase governance sign-offs to unblock trust & safety controls.
4. Make the feedback loop routine — mine fallbacks weekly → patterns → retrain → gate.
5. Ops debts — repo-of-record, model-name labels, UAT auto-deploy leg.

You may propose work that *builds on* these. Do not hand back items 2–5 as if
they were your idea.

---

## 3. Your task

Produce a **ranked slate of candidate POC features** for Sevi — things that are
demonstrable in a POC timeframe, defensible to a university stakeholder, and
buildable against the architecture in §2.

Cover at minimum these five directions, and add your own if you see something
better:

- **A. Recall** — close the 77% gap without breaking precision.
- **B. Agentic** — what the second and third workflows should be, given #1 is a
  mocked advising booking and real auth is the blocker.
- **C. Internal copilot** — extend the fenced staff-facing mode (AIS/HRIS).
- **D. Trust & evaluation** — make Sevi provably safe/correct, not just safe.
- **E. Experience** — multilingual (Taglish is the real usage pattern), the
  typed-card surface, accessibility, low-bandwidth realities.

**Quantity over none, quality over volume:** aim for **8–14 proposals total**.
Better to give 8 sharp ones than 20 padded.

---

## 4. Output contract

Return **Markdown**. For each proposal, exactly this block:

```markdown
### P<n>. <Short imperative title>
- **Direction:** A | B | C | D | E | Other
- **One-liner:** <what it does, in one sentence a dean would understand>
- **Why now:** <what in §2 makes this the right next thing>
- **Anchors:** <real files/tiers/flags from §2 this touches>
- **Sketch:** <3-6 sentences of how it works. Mechanism, not marketing.>
- **Demo:** <the single moment that makes a stakeholder nod. Be concrete —
  the literal question typed and the literal thing that happens.>
- **Effort:** S (<1 day) | M (2-4 days) | L (1-2 weeks)
- **Risk:** Low | Med | High — <the specific failure mode, not "it might not work">
- **Kills it:** <the one finding that would make you abandon this>
- **Grounded:** YES | SPECULATIVE
```

Then close with:

```markdown
## Ranked slate
| Rank | ID | Title | Effort | Risk | Why this rank |
```

Rank by **(stakeholder-visible value) ÷ (effort × risk)**, and say so in one
line per row. Put your single strongest recommendation at rank 1 and defend it
in 2–3 sentences under the table.

Finally:

```markdown
## What I could not verify
- <anything you assumed; anything you'd want the orchestrator to check first>
```

---

## 5. Rubric — how your output will be judged

1. **Grounded** — anchors resolve to real artifacts. Invented paths fail the item.
2. **Non-obvious** — "add more training data" is not a POC. What's the *mechanism*?
3. **Demoable** — if you can't name the moment on screen, it isn't a POC.
4. **Honest about risk** — a proposal with `Risk: Low` on everything is a tell.
5. **Respects the constraints** — anything violating §2.4 is rejected outright.
6. **Kill criteria present** — a proposal you can't disprove is a wish.

---

## 6. Anti-patterns (automatic rejection)

- "Fine-tune a larger model" without addressing the local-LLM constraint.
- Anything requiring student PII on the anonymous surface.
- Rewriting the cascade wholesale. It was measured; it won.
- Vendor-shaped answers ("integrate with <SaaS>") without a self-hosted path.
- Re-listing roadmap items 2–5 from §2.5 as new ideas.
- Proposals whose entire content is "use an LLM for it."

---

## 7. Definition of done

You are done when you have returned: 8–14 proposal blocks in the §4 format, the
ranked slate table, a defended rank-1 pick, and the "could not verify" list.
**One pass.** Do not iterate on your own output — hand it back and the
orchestrator will come back with targeted follow-ups if needed.

---

## 8. Orchestrator notes (not for the sub-agent)

**How I'll ingest the result:**

1. Verify every `Anchors:` path against the repo. Any invented path → the whole
   proposal drops to `SPECULATIVE` regardless of its self-label.
2. Cross-check claimed metrics against `data/eval/mirror_qa.json` and
   `docs/eval/redesign_report.md`. Gemini has no access to either — any specific
   number it quotes is either from this brief or fabricated.
3. Filter against §2.4 constraints and §2.5 (already-agreed work).
4. Bring the surviving top 3 back to the user with an effort/risk read, and only
   then scope an implementation.

**Gates any accepted proposal must eventually pass** (from `HANDOFF.md`):

```
python test_input_clamps.py           # 34 checks
python test_safety_gate.py            # 55 + 11
python test_moderation_controls.py    # 36
python test_agentic_workflow.py       # 23
python -c "import api.app"            # imports clean
```

Plus the 268-Q eval must not regress precision below ~78%.

**Open security items that constrain direction B and C** (see `HANDOFF.md` P3):
`intent_hint` can dispatch arbitrary MCP tools including writes; the legacy root
`app.py` exposes logger routes unauthenticated; `session_id` is an unbound bearer
capability. Any agentic or internal-mode proposal inherits these as prerequisites.
