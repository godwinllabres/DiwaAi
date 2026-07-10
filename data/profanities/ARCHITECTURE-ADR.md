# ADR-001: Package the Philippine Profanity Lexicon as a Versioned Data Package with Thin Language Wrappers

**Status:** Proposed
**Date:** 2026-07-10
**Deciders:** win (project owner)

## Context

We now have `ph-profanity-lexicon` v0.1.0: a canonical JSON dataset of 207 entries across
10 language codes (tgl, ceb, ilo, hil, war, bcl, pam, pag, cbk, eng) with severity (1–4),
register (formal→archaic), categories, typed slur flags, 328 spelling/text-speak variants,
euphemism chains, formal equivalents, cross-language false friends, and a 29-phrase
allowlist. The goal is to turn this into a **reusable dependency** consumable from multiple
runtimes and use cases: chat/comment moderation, NLP dataset labeling, toxicity-model
feature engineering, and writing tools that suggest register-appropriate rewording.

Forces specific to this problem:

1. **The data changes faster than any code will.** New slang, meme spellings, and native
   reviewer corrections should ship without waiting on a library release.
2. **Consumers live in different ecosystems.** Web/Node moderation bots want npm; NLP
   pipelines want PyPI/pandas; researchers want a plain file they can `curl`.
3. **Philippine text is code-switched by default.** Any matcher must run several language
   lists simultaneously and resolve cross-language false friends (*puke*, *buto*, *libog*,
   *boto*, *agi*, *atay*) — so matching logic is non-trivial and must be consistent across
   ecosystems, not reimplemented ad hoc by every consumer.
4. **Sensitive content needs governance.** Slur entries and context-dependent identity
   terms (*bakla*, *bayot*, *agi*) carry moderation-policy weight; changes need review
   gates, provenance, and an intended-use statement — this is data stewardship, not just
   code maintenance.
5. **Single maintainer today**, potential community contributors per language tomorrow.
6. **Privacy:** moderation input text must never need to leave the consumer's process.

Prior art examined: [`jromest/filipino-badwords-list`](https://github.com/jromest/filipino-badwords-list)
(npm, flat array embedded in a JS package — simple but single-ecosystem and metadata-free)
and [`dsojevic/profanity-list`](https://github.com/dsojevic/profanity-list) (JSON with
severity/tags/exceptions consumed via npm — closest to the shape proposed here), plus
HuggingFace-hosted corpora ([`mginoben/tagalog-profanity-dataset`](https://huggingface.co/datasets/mginoben/tagalog-profanity-dataset),
[`hate_speech_filipino`](https://huggingface.co/datasets/legacy-datasets/hate_speech_filipino))
which serve research but not application runtimes.

## Decision

Adopt **Option B: a canonical data package + thin per-ecosystem wrappers.**

The versioned JSON dataset is the single source of truth, validated by JSON Schema in CI
and distributed as a standalone artifact (GitHub Releases + pinned CDN + registry data
packages). Thin wrapper libraries (npm/TypeScript first, PyPI second) vendor a pinned copy
of the data and implement one shared matching pipeline (normalization → multi-pattern scan
→ boundary rules → allowlist veto → context flags → severity threshold), kept behaviorally
identical via a shared conformance-test vector file. Data and wrappers are versioned
independently; a wrapper major version declares which data schema major it accepts.

## Options Considered

### Option A: Monolithic library per ecosystem (data embedded in code, like `filipino-badwords-list`)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — one repo, one publish command |
| Cost | Minimal infra; high long-term duplication cost |
| Scalability | Poor — every new ecosystem forks the data; drift is guaranteed |
| Team familiarity | High — everyone has shipped a plain npm package |

**Pros:** Fastest to ship; zero cross-repo coordination; consumers get one `npm install`.
**Cons:** Data corrections require a code release per ecosystem; Python/researchers are
second-class; no canonical citable dataset; drift between copies of a *safety-relevant*
list is a real hazard (a slur fixed in npm but stale in PyPI).

### Option B: Data package + thin wrappers (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — monorepo with a build step and a publish matrix |
| Cost | Free tier throughout (GitHub Actions, npm, PyPI, jsDelivr) |
| Scalability | Strong — new ecosystems (Go, PHP, WASM) add a wrapper, never fork data |
| Team familiarity | Medium — data/code split and conformance vectors are extra concepts |

**Pros:** One source of truth; data releases decoupled from code; per-ecosystem idiomatic
APIs; the dataset itself stays citable/downloadable for research; validation (already
implemented in `build.py`) becomes a CI gate; offline and private by construction.
**Cons:** More release engineering; matcher logic duplicated per wrapper (mitigated by
shared conformance vectors, or later a WASM core); two version numbers to explain.

### Option C: Hosted API / microservice

| Dimension | Assessment |
|-----------|------------|
| Complexity | High — service, auth, uptime, abuse handling |
| Cost | Ongoing hosting + on-call, for a dataset that fits in 170 KB |
| Scalability | Central updates are instant, but adds a network hop per check |
| Team familiarity | Medium |

**Pros:** Every client sees updates immediately; language-agnostic by HTTP.
**Cons:** **Privacy killer** — consumers must send user text to a third party, which many
moderation contexts (and the PH Data Privacy Act mindset) cannot accept; latency; offline
impossible; overkill for static-ish lexical data. Reasonable later as an *optional* layer
on top of B, never as the foundation.

### Option D: HuggingFace dataset only

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low |
| Cost | Free |
| Scalability | Good for ML training workflows only |
| Team familiarity | High for data scientists, low for app developers |

**Pros:** Discoverability in the NLP community; dataset viewer; DOI-style citation.
**Cons:** Not a runtime dependency story — no matcher, no semver contract for apps, no
npm/PyPI ergonomics. Best treated as a **mirror** generated from B, not the home.

## Trade-off Analysis

The decisive trade is **release engineering (B's cost) vs. data integrity and reach (B's
prize)**. For a safety-relevant lexicon, the worst failure mode of Option A — silently
divergent copies of slur data across ecosystems — outweighs the convenience of a single
`npm publish`. Option C trades away the two properties moderation consumers value most
(privacy and latency) to solve a freshness problem that pinned-CDN data releases already
solve adequately. Option D serves one audience well and everyone else not at all.

B's real risk is *matcher divergence*: two implementations (TS, Python) of normalization
and boundary rules will drift unless forced together. Mitigation is baked into the design:
a `tests/conformance.json` of input → expected-matches vectors (including the nasty cases:
`"leche flan"` must not fire, `"t@ng 1n4 mo"` must fire, `"nalibog ko"` must fire only
with a Tagalog hint, `"pagtatae"` never fires) that both wrappers must pass in CI. If
wrapper count grows, extract a single Rust/WASM matcher core and shrink wrappers to
bindings — that is an evolution of B, not a new decision.

## Target Architecture

```
ph-profanity-lexicon/                  (GitHub monorepo)
├── data/lexicon.json                  # SOURCE OF TRUTH (today's dist/ file, promoted)
├── schema/lexicon.schema.json         # JSON Schema (draft 2020-12), CI-enforced
├── build/                             # today's build.py + emitters, evolved
├── dist/                              # generated: canonical.json, .min.json, .csv,
│   ├── ...                            #   per-language plain .txt lists, HF parquet
├── tests/conformance.json             # shared match/no-match vectors (all wrappers)
├── packages/
│   ├── js/                            # npm: ph-profanity-lexicon  (TS)
│   │   └── src/{data.ts,normalize.ts,matcher.ts,censor.ts,index.ts}
│   └── py/                            # PyPI: ph-profanity-lexicon
│       └── ph_profanity_lexicon/{data.py,normalize.py,matcher.py}
├── .github/workflows/ci.yml           # validate schema → run build → conformance matrix
├── .github/workflows/release.yml      # tag → GitHub Release + npm + PyPI + CDN purge
├── CONTRIBUTING.md                    # native-review gates (see Governance)
└── LICENSE-DATA / LICENSE-CODE
```

**Wrapper API surface (identical semantics in TS and Python):**

```ts
loadLexicon(opts?)                                    // typed entries + metadata
matches(text, {langs, minSeverity, includeContextDependent, includeSlurs})
  // -> [{entryId, term, span, matchedVariant, severity, isSlur, contextDependent}]
isProfane(text, opts)                                 // boolean convenience
censor(text, opts)                                    // "p*********a" masking
explain(entryId)                                      // gloss, notes, etymology (writing tools)
suggestFormal(entryId)                                // formal_equivalent lookup (writing tools)
```

**Consumption paths by use case:** moderation bots → npm/PyPI wrapper; data labeling →
PyPI wrapper or raw `dist/canonical.json`; research → HF mirror / CSV; anything else →
pinned CDN URL (`cdn.jsdelivr.net/gh/<user>/ph-profanity-lexicon@v1.2.0/dist/canonical.min.json`).

**Versioning contract:** data uses semver where **major = schema change, minor = entries
added/expanded, patch = corrections**; wrappers pin `data >=1.0 <2.0` and vendor the data
at build time (no runtime fetch by default — moderation behavior must be reproducible;
optional `refresh()` exists but is explicit and logged).

**Licensing:** dual-license — **data CC BY 4.0** (attribution, maximum commercial adoption;
choose CC BY-SA 4.0 instead if copyleft on derivative wordlists matters more to you than
adoption) and **code MIT**, mirroring how existing profanity lists are received well.

## Governance (because this data is sensitive)

- Slur-category and `context_dependent` changes require a native-speaker reviewer for the
  affected language (CODEOWNERS per `data/` language block); two approvals to *remove* a
  context flag.
- Every entry keeps `confidence` + `sources`; `model`-sourced entries graduate to `high`
  only via native review — the field already exists, the workflow just enforces it.
- Intended-use statement ships inside the JSON itself (already present) and in every
  package README: detection and moderation, not harassment generation.
- Issue templates per language to recruit the missing coverage (war, pag, pam are thin).

## Consequences

- **Easier:** shipping a slang correction the day it's reported (data patch release, no
  code churn); adding Go/PHP/WASM consumers; citing the dataset in research; generating
  downstream artifacts (per-language txt, HF parquet, even a future API) from one source.
- **Harder:** first-time setup (schema, CI matrix, two-package publishing); explaining
  data-version vs wrapper-version to users; keeping two matcher implementations honest
  (conformance vectors are now load-bearing infrastructure).
- **Revisit when:** wrapper count ≥3 or conformance drift appears (→ WASM core); frequency
  weights land (→ schema major bump); someone actually needs central real-time updates
  (→ optional API layer on top); coverage grows past ~1–2k entries (→ split per-language
  data files, keep single-build output).

## Action Items

1. [ ] Create the GitHub repo; promote this session's `build.py`, `data_*.py`, and
       `dist/` into the monorepo layout above.
2. [ ] Write `schema/lexicon.schema.json` and wire `build.py` validation + schema check
       into GitHub Actions CI.
3. [ ] Author `tests/conformance.json` (~50 vectors: leet, spacing, repeats, allowlist,
       false-friend language hints, context-dependent identity terms).
4. [ ] Ship npm wrapper MVP (`normalize` → Aho-Corasick over terms+variants → boundary +
       allowlist rules → threshold API); pass conformance suite.
5. [ ] Port to PyPI wrapper; run the same vectors in CI matrix.
6. [ ] Add `release.yml`: tag push → GitHub Release + `npm publish` + `twine upload` +
       jsDelivr pin documented in README.
7. [ ] Recruit native reviewers for Waray, Pangasinan, Kapampangan; add CODEOWNERS gates.
8. [ ] Publish the HuggingFace mirror generated from `dist/` (research discoverability).
9. [ ] Define v1.0.0 exit criteria: schema frozen, ≥1 native review pass per language,
       conformance suite green on both wrappers.
