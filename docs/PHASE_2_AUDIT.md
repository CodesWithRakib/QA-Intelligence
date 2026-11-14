# Phase 2 audit of the Phase 1 system

Audited on 2026-11-14 by running the platform and its checks, not by reading its documentation.
Workspace: `C:\Sofof_tech_2026\QA-Intelligence` (there is no `D:` drive on this machine).

## How the audit was done

| Step | Result |
|---|---|
| `scripts\dev.ps1 -Production` (first real execution of this script) | Stack started: PostgreSQL, API, worker, dashboard |
| `scripts\health.ps1` | **Defect found**: crashed under `Set-StrictMode` on checks without an `optional` field. Fixed |
| New `scripts\ui_smoke.py`: every dashboard screen in headless Chromium, desktop and mobile | **Defect found**: every API call through the dashboard returned HTTP 500 in production mode. Fixed (see below). After the fix 46 of 48 screen/viewport combinations passed; the 2 failures were horizontal overflow on mobile (fixed) |
| `pytest`, Ruff, mypy, `tsc`, ESLint | All passing before any change (76 tests) |
| Phase 1 end-to-end script | Passed 51/51 on 2026-11-13; not re-run unchanged because Phase 2 changes the plan contents |

## Defects found in Phase 1 by this audit

| # | Defect | Cause | Fix |
|---|---|---|---|
| 1 | Dashboard unusable with `next start`: proxy answered "QAI_API_TOKEN is not configured" | `loadEnvConfig()` in `next.config.ts` does not populate the environment of route handlers in production | `frontend/src/lib/server-env.ts` reads the repository `.env` directly, server-side only |
| 2 | `scripts\health.ps1` threw on the first check | Property access on a missing JSON field under strict mode | Guarded property lookup |
| 3 | `/plans` and `/projects/{id}` overflow horizontally at 390 px | Grid children default to `min-width: auto` | `min-w-0` on `Card` |

Phase 1 reported the dashboard as "built, not exercised in a browser". That gap was real: defect 1
made the dashboard non-functional in the documented production start mode.

## What exists and works (verified)

| Area | State |
|---|---|
| Backend: FastAPI, SQLAlchemy, Alembic, 26 tables, PostgreSQL job queue, single worker with heartbeat | Working |
| Authentication: bearer token, Host allowlist, same-origin proxy | Working (tests) |
| Ingestion: local folder, ZIP, OpenAPI file, URL; safe extraction; secret files excluded | Working (tests) |
| Discovery: languages, frameworks, dependencies, routes, endpoints, env names | Working; pattern-based |
| Engines: sandboxed pytest, Trivy, Semgrep, built-in credential check, OpenAPI contract tests, Playwright (Chromium) page health / crawl / responsive / axe / timing | Working (Phase 1 E2E) |
| Repair: dependency bump, AI patch, approval, isolated branch, policy review, before/after | Working (Phase 1 E2E) |
| AI: Ollama provider, routing, budgets, benchmark | Working when Ollama runs (it was stopped during this audit) |
| Evidence and reports | Working |
| Dashboard: 25 routes | Working after defect 1 was fixed |
| Scripts: setup, start/stop, health, backup with restore check | `dev`, `stop`, `health` now executed for the first time |

## Incomplete or missing for Phase 2 (before this phase's work)

| Area | Gap |
|---|---|
| Architecture | Engines were dispatched from a dictionary in the run service; no common contract, no capability or readiness concept |
| Readiness | A check that could not run was only discovered while running |
| Orchestration | No cancellation, no progress reporting, no cleanup hook |
| Ingestion | No git, no APK |
| Web | Chromium only; 2 viewports; no visual comparison; no route-level testing; no form inspection; no PWA check; no Lighthouse, k6, gitleaks or ZAP |
| Node projects | Sandbox ran `npm test` per component only: no pnpm, no monorepo, no type-check / lint / build |
| Android | Nothing: no JDK, SDK, adb, emulator or Appium on the machine |
| Findings | No distinction between confirmed defects and scanner output |
| API | No CORS or cookie checks; no GraphQL or WebSocket |
| Database | Static check only |

## Security weaknesses noted

| Weakness | Status |
|---|---|
| Dependency installation in the sandbox has network egress | Unchanged, documented residual risk |
| Browser engine runs on the host | Unchanged, documented |
| Scanner images referenced by `latest` tag | Unchanged; versions recorded in the installation plan |
| No disk quota for sandbox volumes | Unchanged; volumes are removed by adapter cleanup, including after failures |
| Lighthouse launches its own Chromium, outside the request guard | New in Phase 2; it only runs against an authorised target |

## Reusable modules (kept, not rewritten)

`security/ssrf.py`, `security/archive.py`, `engines/sandbox.py`, `engines/api.py`, `engines/browser.py`,
`engines/security_scan.py`, `jobs/queue.py`, `repair/*`, `ai/*`, `reports/service.py`, the data model and
the whole dashboard. Phase 2 wraps the engines in adapters; no engine was replaced.

## Hardware limits that shape Phase 2

| Fact | Consequence |
|---|---|
| 15.9 GB RAM, typically 2-3.5 GB free with the owner's IDE and browsers open | The Android emulator (about 2-3 GB) is the binding constraint: it is started only when enough memory is free |
| Docker VM capped at 4 GB (`.wslconfig`, unchanged) | Node monorepo builds get 2.5 GB; OWASP ZAP (2 GB) cannot share the VM with another heavy job |
| GTX 1660 SUPER, 6 GB VRAM, driver 616.92 | Enough for one small quantised model; not used by browsers, scanners or the emulator (software rendering) |
| WHPX available | Emulator hardware acceleration works |
| 57.8 GB free at the start of Phase 2, 39.3 GB after tool installation | Below the 40 GB threshold: no further heavyweight installation |

## Dependencies already present / missing at audit time

Present: Git 2.50.1, Python 3.13.17, Node 26.8.1, pnpm 12.4.2, Docker 29.8.2, WSL 2, Ollama 0.40.16,
Playwright Chromium, images for PostgreSQL, Python and Node sandboxes, Trivy, Semgrep.

Missing: Java, Android SDK, adb, emulator, Appium, Firefox and WebKit builds, Lighthouse, k6, gitleaks,
OWASP ZAP, GitHub CLI. See [PHASE_2_INSTALLATION_PLAN.md](PHASE_2_INSTALLATION_PLAN.md).
