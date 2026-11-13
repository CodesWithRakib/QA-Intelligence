# MVP acceptance criteria

Status on 2026-11-13. "Verified" means observed in this environment; the evidence column says how.
E2E = `scripts/e2e_verify.py` (51/51 checks passed, log in `data/logs/e2e.log`).

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Starts locally using documented commands | **Partial** | API, worker, PostgreSQL and demo target were started and used. They were started by hand; `scripts/dev.ps1` itself was not executed |
| 2 | Functioning frontend and backend | **Partial** | Backend: verified by E2E. Frontend: type-checks, lints, builds 25 routes; **not opened in a browser against the running API** |
| 3 | Database migrations run successfully | Verified | `scripts/migrate.ps1`; upgrade → downgrade → upgrade; `alembic check` reports no drift |
| 4 | Connects to local Ollama | Verified | Health check; `scripts/check-ollama.ps1` (66.6 tokens/s); AI repair and AI review ran in E2E |
| 5 | Imports a local project | Verified | E2E: 12 files imported from `examples/demo-shop` |
| 6 | Uploads a ZIP project safely | Verified (tests) | `test_zip_upload_safe_and_unsafe` and five archive safety tests. Not part of E2E |
| 7 | Detects language and framework | Verified | E2E: Python / FastAPI; `tests/test_discovery.py` |
| 8 | Generates a real architecture inventory | Verified | E2E: 11 endpoints, 1 component, 1 OpenAPI spec |
| 9 | Generates a QA test plan | Verified | E2E: 25 cases |
| 10 | Runs at least one real Playwright browser test | Verified | E2E: 5 browser checks executed (4 failed on seeded defects, 1 passed), screenshots stored |
| 11 | Runs real API tests against an authorised local target | Verified | E2E: 4 contract + 3 negative checks against 127.0.0.1:8100 |
| 12 | Runs static security or dependency analysis | Verified | E2E: Trivy (2 vulnerable packages), Semgrep (5 findings), built-in credential check |
| 13 | Persists test results | Verified | E2E: 25 stored results = 25 planned cases |
| 14 | Stores evidence | Verified | E2E: screenshot retrieved through the API (PNG, 17 785 bytes); HTTP exchanges and tool output stored |
| 15 | Creates actionable findings | Verified | E2E: 29 findings in 5 categories; all ten seeded defects detected |
| 16 | Proposes a repair | Verified | E2E: deterministic `jinja2 3.1.2 → 3.1.6`; AI patch for `apply_discount` |
| 17 | Applies an approved isolated repair | Verified | E2E: nothing executed before approval; applied on `qai/repair-*` branches under `data/repairs/` |
| 18 | Runs regression tests | Verified | E2E: project tests before and after each patch; "no newly failing tests" |
| 19 | Produces an honest report | Verified | E2E: report states counts per state and that unverified checks "must not be read as passing" |
| 20 | Keeps secrets out of Git | Verified | `.env`, `data/`, `backups/` ignored; `git ls-files` shows none; `scripts/validate-env.ps1` |
| 21 | Isolates imported project execution | Verified (functional) | Project tests ran only in containers (non-root, no capabilities, tests without network). Isolation strength itself was not attacked |
| 22 | Enforces resource and timeout limits | **Partial** | Limits are passed to every container and were confirmed manually for one container (memory cgroup, no network, read-only root). No test proves a runaway process is actually stopped |
| 23 | Passes core end-to-end tests | Verified | 51/51 |
| 24 | Reproducible setup on a fresh environment | **Not verified** | Lockfiles and scripts exist; no clean-machine run was done |

## Summary

20 verified, 3 partial (1, 2, 22), 1 not verified (24).

## To close the open items

1. Run `scripts\dev.ps1`, open http://127.0.0.1:3000 and walk through the flow in LOCAL_SETUP.md §4 (criteria 1 and 2).
2. Add sandbox fault-injection tests: memory hog, fork bomb, sleep past the timeout (criterion 22).
3. Run `scripts\setup.ps1` on a clean Windows VM (criterion 24).
