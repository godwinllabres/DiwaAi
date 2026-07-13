# Security Scanning PoC — SonarQube, Checkmarx, Snyk

Proof-of-concept integration of three scanners into SeviAI. The CI entry point
is `.github/workflows/security-scan.yml`; jobs needing paid/registered
accounts skip cleanly until their secrets exist, so the pipeline is green from
day one and lights up incrementally.

| Tool | Class | What it catches here | Account needed |
|------|-------|----------------------|----------------|
| SonarQube | Code quality + SAST | Bugs, smells, hotspots in `api/`, `scripts/`, `training/` | SonarCloud free tier (public repo) or self-hosted |
| Checkmarx KICS | IaC + secrets | Dockerfile/compose/workflow misconfigs, leaked keys | **None** — open source |
| Checkmarx One | Enterprise SAST/SCA | Full taint-flow SAST over Python | Checkmarx One tenant |
| Snyk | Dependency SCA | Known CVEs in `requirements_local.txt` pins | Snyk free tier |

## Enabling each tool

### SonarQube
- **SonarCloud** (simplest): import the repo at sonarcloud.io, add
  `SONAR_TOKEN` secret, and append `sonar.organization=<org>` to
  `sonar-project.properties`.
- **Self-hosted** (fits the local-server deployment model):
  ```bash
  docker run -d --name sonarqube -p 9000:9000 sonarqube:community
  # first login admin/admin → create project seviai → generate token
  docker run --rm -v "$PWD:/usr/src" sonarsource/sonar-scanner-cli \
    -Dsonar.host.url=http://host.docker.internal:9000 -Dsonar.token=<token>
  ```
  In CI, set both `SONAR_TOKEN` and `SONAR_HOST_URL` secrets.

### Checkmarx
- **KICS** runs unauthenticated in CI on every push (results land in the
  GitHub Security tab as SARIF). Local run:
  ```bash
  docker run --rm -v "$PWD:/path" checkmarx/kics scan \
    -p /path/deployment -o /path/kics-local --report-formats json
  ```
- **Checkmarx One**: add `CX_TENANT`, `CX_BASE_URI`, `CX_CLIENT_ID`,
  `CX_CLIENT_SECRET` secrets (OAuth client from the Checkmarx One console).

### Snyk
- Create a token at app.snyk.io → Account settings, add as `SNYK_TOKEN`.
- Local run: `npm i -g snyk && snyk auth && snyk test --file=deployment/requirements_local.txt`
- Free stand-in with no account: `pip install pip-audit && pip-audit -r deployment/requirements_local.txt`

## PoC posture (deliberate)

- All jobs **report, never block** (`fail_on: none`, `continue-on-error`).
  After the first triage pass, tighten: KICS `fail_on: high`, Snyk
  `--severity-threshold=high`, Sonar quality gate on new code.
- SARIF from KICS/Snyk feeds the repo **Security tab**, so findings live where
  reviewers already look.
- `sonar-project.properties` excludes models/data/archive — scan code, not
  artifacts.
