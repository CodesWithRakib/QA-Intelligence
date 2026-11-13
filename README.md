# QA Intelligence

A self-hosted platform that imports a software project, builds an architecture inventory, plans and
runs quality checks with real tools, records defects with evidence, and proposes and verifies fixes
under your control. Local-first and free to run: paid AI is optional and off by default.

> **Status: MVP 0.1.0, not production-ready.** The backend passed its end-to-end acceptance run
> (51/51 checks). The dashboard builds but has not yet been exercised in a browser against the
> running API. Read [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) before relying on results.
> Never expose it to a network: it is a single-owner tool bound to localhost.

## What it does today

| Capability | Tools |
|---|---|
| Import | Local folder, ZIP, OpenAPI file, running URL |
| Understand | Languages, frameworks, dependencies, routes, API endpoints, tests, containers (deep analysis: Python, JS/TS) |
| Test | Project's own tests in a Docker sandbox · Playwright + axe-core · OpenAPI contract and negative tests · Trivy · Semgrep |
| Report | Six explicit result states (passed, failed, skipped, blocked, not tested, not applicable), evidence for every result |
| Repair | Dependency upgrades and AI patches for failing tests: approved by you, applied on an isolated branch, verified before/after |
| AI | Ollama (local). Optional Anthropic provider behind explicit approval and hard budgets |

Not implemented yet: Git/GitHub/GitLab import, authenticated testing, OWASP ZAP, k6, visual regression,
live database checks, multi-user accounts. Full list and compatibility matrix:
[docs/PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md).

## Quick start (Windows 11, PowerShell 7)

```powershell
scripts\check-prereqs.ps1          # what is installed, what is missing
scripts\install-prereqs.ps1 -Apply # only if something is missing
scripts\setup.ps1                  # .env, dependencies, database, images
scripts\dev.ps1 -Production        # start everything
scripts\start-demo-target.ps1      # optional: intentionally defective demo app
```

Open http://127.0.0.1:3000 and follow [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) §4.

```powershell
scripts\health.ps1                                    # health of the running stack
scripts\test.ps1                                      # lint, types, unit tests
backend\.venv\Scripts\python scripts\e2e_verify.py    # end-to-end acceptance run
scripts\backup.ps1 -Verify                            # backup with a test restore
scripts\stop.ps1
```

## Architecture in one paragraph

A modular monolith: FastAPI API and a single worker (Python 3.13) on the host, PostgreSQL 17 in a
container, a Next.js 16 dashboard that proxies API calls server-side. Untrusted project code runs only
in capability-less, resource-limited containers; scanners run in containers with the source mounted
read-only; every outbound request is checked against an owner-managed allowlist. Deterministic tools
decide every verdict; AI output is advisory and stored separately.

## Documentation

| Document | Content |
|---|---|
| [LOCAL_SETUP](docs/LOCAL_SETUP.md) | Install, start, demo walkthrough, importing projects, approving repairs |
| [ENVIRONMENT_REQUIREMENTS](docs/ENVIRONMENT_REQUIREMENTS.md) · [ENVIRONMENT_REPORT](docs/ENVIRONMENT_REPORT.md) | What is needed · what was found on this machine |
| [ARCHITECTURE](docs/ARCHITECTURE.md) · [ARCHITECTURE_DECISIONS](docs/ARCHITECTURE_DECISIONS.md) · [DATA_MODEL](docs/DATA_MODEL.md) | Design |
| [SECURITY_MODEL](docs/SECURITY_MODEL.md) · [THREAT_MODEL](docs/THREAT_MODEL.md) | Controls and residual risks |
| [TEST_STRATEGY](docs/TEST_STRATEGY.md) · [ACCEPTANCE_CRITERIA](docs/ACCEPTANCE_CRITERIA.md) | How it is tested · what is verified |
| [OPERATIONS](docs/OPERATIONS.md) | Logs, backup and recovery, housekeeping |
| [PRODUCT_REQUIREMENTS](docs/PRODUCT_REQUIREMENTS.md) · [KNOWN_LIMITATIONS](docs/KNOWN_LIMITATIONS.md) · [ROADMAP](docs/ROADMAP.md) | Scope |
| [PROGRESS](docs/PROGRESS.md) · [CHANGELOG](docs/CHANGELOG.md) | History |

## Responsible use

Only test systems you own or are explicitly authorised to test. Targets must be added to the
authorised list before any browser or API check runs.

## Third-party components

axe-core 4.10.3 is vendored under `backend/app/engines/vendor/` (MPL-2.0, licence included).
