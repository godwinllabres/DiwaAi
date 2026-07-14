# Sevi intent redesign — validation report

**Date:** 2026-07-14
**Method:** built a 268-question grounded QA dataset from the CvSU mirror
(`http://192.168.10.130:9090/`, 31 docs → 248 factual + 20 adversarial), drove
the chat pipeline over every question, and graded each answer against ground
truth with a 34-agent LLM judge (strict rubric).

## Headline — DEPLOYED & VERIFIED LIVE

| variant | pass | correct | misroute | refused_bad | wrong |
|---|--:|--:|--:|--:|--:|
| 1. Baseline live (old models) | **23.5%** | 10 | 130 | 21 | 1 |
| 2. Redesign in-process (preview) | 57.1% | 145 | 7 | 12 | 13 |
| 3. Redesign live, grounding OFF | 33.6% | 24 | 21 | 41 | 6 |
| 4. **Redesign live, grounding ON (deployed)** | **51.1%** | 129 | 20 | 9 | 15 |

**Live result: 23.5% → 51.1% (+27.6 pts). 105 newly pass, 31 regressed.**

### Critical infra bug found & fixed
Rows 2→3 exposed it: `.dockerignore` excluded `docs/`, so `site_corpus.txt` and
`citizens_charter_text.txt` **never shipped in the image** — both RAG tiers were
silently disabled in every deployment, so the LLM tier answered ungrounded and
over-refused (43 `out_of_scope`). Un-ignoring the two corpora (row 4) restored
grounding: refusals 43→11, pass 33.6%→51.1%. This bug also depressed the
baseline — Sevi has never had working grounding in production until now.

> Note on comparability: the baseline ran against the **live** api (post the
> session's routing/qualifier fixes). The redesign was measured **in-process**
> with the retrained NB+NN and the **same host Ollama (llama3.2:3b)** the live
> api uses — the container was NOT redeployed. Endpoint-level gates
> (safety/campus/AIS) were not replicated; they are unchanged by the redesign.

## What changed

- **+4 durable, date-qualified intents**: `licensure_results`,
  `university_rankings`, `awards_recognition`, `accreditation_status`.
- **+28 patterns** to `vision_mission`, `university_officials`, `events`;
  **trimmed** `about_cvsu`'s greedy tokens (`CvSU`, `Who is CvSU`).
- **Retrained NB + NN** (125 intents). Leak-checked: 0 patterns duplicate an
  eval question.
- Net routing effect (in-process A/B): confident misroutes **126 → 2**; the new
  NN defers more, sending volatile-fact questions to the **grounded LLM tier**
  (79% of answers now come from site/charter-grounded LLM vs 32% before).

## Per-category pass rate (baseline → redesign)

| category | base | new | n |
|---|--:|--:|--:|
| awards | 6% | 50% | 16 |
| rankings | 16% | 65% | 31 |
| events | 13% | 59% | 39 |
| governance | 16% | 64% | 25 |
| licensure | 30% | 63% | 79 |
| transparency | 25% | 62% | 8 |
| history | 33% | 67% | 9 |
| accreditation | 20% | 40% | 20 |
| mission_vision | 44% | 33% ⬇ | 9 |
| research | 20% | 20% | 5 |

## The remaining ceiling: the local LLM (llama3.2:3b)

Routing is essentially fixed (misroutes 130→8). The residual failures are now
**grounding-quality** errors from the weak 3B model, not routing:

- **15 fabrications** — grabbed the wrong article (Criminologist vs LEPT), gave
  the *national* number instead of CvSU's ("2,030 of 3,287" vs "10 of 12"),
  invented dates ("June 20" vs gold "April 13"), or miscited the Citizens'
  Charter for a news fact. A few are harsh judge calls (correct figure, wrong
  citation).
- **23 regressions** — mostly `fallback_ok → fallback_bad`: the baseline gave an
  honest "ask the office"; the new pipeline *attempts* an answer and gets it
  partly wrong. Trades a safe non-answer for a risky attempt.

### Levers to close it (not yet applied)
1. **Stronger local model** — `qwen3:8b` is already pulled on the host; swapping
   `OLLAMA_MODEL` would likely cut fabrications materially.
2. **Tighter grounding prompt** — "answer only if the excerpt explicitly states
   it, else defer" (converts fabrications → honest fallbacks); forbid citing the
   Charter for news facts.
3. **Let the durable canned intents fire more** (lower their effective gate) for
   a safe headline instead of risky extraction on the most volatile asks.

## Reproduce

```
scripts/eval_run_chat.py         # baseline (live api) -> responses.json
scripts/redesign_intents.py      # apply the 4 intents + patterns (leak-checked)
scripts/retrain_nn.py            # NN via api trainer (compat); NB via training/train_naive_bayes.py
scripts/eval_run_inprocess.py    # new models + host Ollama -> responses_new.json
scripts/eval_split_batches.py    # + judge workflow -> verdicts
scripts/eval_compare.py          # baseline vs redesign
```

## Status

Redesign is applied to the repo (backed up) and NB+NN retrained, but **not
deployed** — the live `sevi-api` image still serves the old-intent models. Live
redeploy + hash re-pin is the gated next step.
