# SeviAI Security Assessment — Local Scan Results

**Date:** 2026-07-14 · **Scope:** SeviAI backend (`api/`, `scripts/`, `training/`, `deployment/`)
**Tooling (local stand-ins for the CI stack):**

| Class | CI tool | Local runner used |
|-------|---------|-------------------|
| SAST (code) | SonarQube / Checkmarx One | Bandit 1.x |
| IaC / secrets | Checkmarx KICS | KICS (Docker, same engine as CI) |
| SCA (deps) | Snyk | pip-audit |

Runs are report-only. Counts below are raw scanner output; the **Triage**
column reflects manual verification against the code.

---

## Summary

| Scanner | Findings | Highest | Verified real |
|---------|---------:|---------|---------------|
| Bandit (SAST) | 79 | 5× HIGH | Yes — see below |
| KICS (IaC) | 22 | 3× HIGH | Mixed (1 false positive) |
| pip-audit (SCA) | 1 | — | Yes |

---

## SAST — Bandit (79 findings: 5 HIGH, 22 MEDIUM, 52 LOW)

### HIGH — `shell=True` subprocess (B602) ×5 — `training/automated_training.py`
Lines 21, 48, 133, 136, 150 run `subprocess.run(cmd, shell=True)`.

**Triage: LOW real risk, but fix anyway.** The commands are f-strings with only
an internal `port` int and fixed script names (e.g.
`f"python test_intents.py {port} 10"`) — no external/user input reaches the
shell, so it is not exploitable today. It is a training-only utility, not on the
request path. Still worth fixing because it is the kind of pattern that becomes
an injection the moment someone parameterizes it with a value from outside.
**Fix:** pass a list and drop `shell=True`: `subprocess.run(["python","test_intents.py",str(port),"10"])`.

### MEDIUM/HIGH-confidence
- **B310 URL open ×4** — `urllib.urlopen` on f-string URLs. Confirm the base
  host is constant (fetching the CvSU site corpus); pin the scheme to `https`.
- **B301 Pickle load ×2** — `api/hybrid_chatbot.py:173,175` (`pickle.load`) plus
  `joblib.load` at 134. **Real, accepted risk:** these load the committed model
  artifacts (`nn_tokenizer.pkl`, `nn_label_encoder.pkl`). Pickle executes
  arbitrary code on load, so this is only safe because the files are
  repo-controlled. **Mitigation:** integrity-check the artifacts (the
  `model_registry` already fingerprints them by hash — gate load on that hash).

The 52 LOW are mostly B404/B603 (subprocess import) and try/except/pass noise —
standard to mute via a Bandit baseline.

---

## IaC / Secrets — KICS (22 findings: 3 HIGH, 9 MEDIUM, 8 LOW)

### HIGH
- **Missing USER instruction** — `deployment/Dockerfile`. Container runs as
  **root**. Real. **Fix:** add a non-root `USER` before `CMD`.
- **Generic Password ×2** — `deployment/docker-compose.yml:16,45`.
  **FALSE POSITIVE (verified):** both lines are
  `POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-sevi}` — an env interpolation with a
  dev default, not a hardcoded production secret. Note for real deployments:
  ensure `POSTGRES_PASSWORD` is actually set so the `sevi` default isn't used.

### MEDIUM (all `docker-compose.yml`, all real hardening gaps)
Container capabilities unrestricted · memory not limited · `security_opt` not
set (no `no-new-privileges`) · traffic not bound to a host interface · apt
package versions not pinned (Dockerfile). None critical; standard container
hardening backlog.

---

## SCA — pip-audit (1 finding)

- **nltk 3.9.4 — PYSEC-2026-597.** Real. NLTK is a core dependency. Check the
  advisory for a fixed version and bump `deployment/requirements_local.txt`
  (and `_render.txt`) once confirmed non-breaking.

---

## Recommended remediation order

1. **nltk CVE** — one-line dependency bump (SCA, quick win).
2. **Dockerfile `USER` + non-root** — real privilege issue, easy fix.
3. **Pickle integrity gate** — wire model load to the existing hash registry.
4. **compose hardening** — memory limits, `no-new-privileges`, cap_drop.
5. **`shell=True` cleanup** — low risk, good hygiene.
6. Add a **Bandit baseline** + KICS false-positive suppression so CI shows only
   new issues.

---

## Remediation applied (2026-07-14)

| # | Fix | Result |
|---|-----|--------|
| 1 | Bumped `nltk` → 3.10.0 in all four requirements files | pip-audit: **0 vulnerabilities** (was 1) |
| 2 | Added non-root `USER appuser` to `Dockerfile` + `Dockerfile.local` | KICS "Missing User Instruction" **cleared** |
| 3 | Added SHA-256 integrity gate: `verify_artifact()` in `hybrid_chatbot.py` refuses to load a model whose hash isn't pinned in `models/trusted_hashes.json`; regen via `scripts/update_trusted_hashes.py`; escape hatch `SEVI_ALLOW_UNVERIFIED_MODELS=1` | Verified: passes clean artifacts, **blocks tampered pickle**, override works |
| 4 | `docker-compose.yml`: `no-new-privileges`, `cap_drop: ALL` (api), `mem_limit` on both services | Hardening in place |
| 5 | Removed all 7 `shell=True` calls in `training/automated_training.py` (list-form args, `Popen`, `shutil`) | Bandit: **0 HIGH** (was 5), **0 B602** |

**Net:** Bandit HIGH 5 → 0 · pip-audit 1 → 0 · KICS 22 → 12 (remaining are the
POSTGRES_PASSWORD false positive, apt version pinning, and compose caps/memory
that KICS wants in v3 `deploy.resources` syntax — functional `mem_limit` is set;
postgres keeps default caps because its entrypoint needs CHOWN/SETUID).

Not yet done (backlog): Bandit baseline + KICS suppression file so CI reports
only *new* findings; B310 URL-scheme pinning; wiring SonarQube/Checkmarx
One/Snyk tokens in CI.
