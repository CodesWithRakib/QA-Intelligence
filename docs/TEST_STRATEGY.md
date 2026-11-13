# Test strategy

The platform's own correctness matters more than usual: a bug here becomes a false verdict about
someone else's software. Tests concentrate on the rules that protect result integrity and safety.

## Levels

| Level | Where | Needs | What it covers |
|---|---|---|---|
| Unit / component | `backend/tests` (76 tests) | PostgreSQL test database | Security rules, discovery, planning, run state handling, API engine, repair rules, AI gates |
| End to end | `scripts/e2e_verify.py` (51 checks) | Whole stack, Docker, Ollama, demo target | The acceptance criteria through the public API with real tools |
| Static | Ruff (incl. bandit `S` rules), mypy (`disallow_untyped_defs`), `tsc --strict`, ESLint | - | Types, lint, common security mistakes |
| Build | `next build` | - | All 25 dashboard routes compile |

Run: `scripts\test.ps1` (static + unit), then the end-to-end script.

## Principles

- **Real dependencies where the behaviour lives.** Tests use a real PostgreSQL database (migrated with
  Alembic, tables truncated between tests) and a real local HTTP server as the API target. Mocks are
  used only at true external boundaries: AI providers (a fake provider object) and Docker-backed
  engines in run-lifecycle tests.
- **The test database is disposable.** `conftest.py` refuses to run unless the database name ends in `_test`.
- **Seeded-defect target.** `examples/demo-shop` contains ten documented defects; the end-to-end run
  asserts that each one is detected by the expected engine.
- **Negative paths are first-class**: unauthorised target, redirect escape, unreachable target,
  crashed engine, invalid model output, over-budget call, rejected and sensitive repairs.

## What the unit tests assert (by risk)

| Risk | Tests |
|---|---|
| Unsafe archive content reaches disk | `test_security.py`: traversal, symlink, bomb, file count, secrets excluded |
| SSRF | `test_security.py`: schemes, credentials, private ranges, metadata (incl. IPv4-mapped), subresources, allowlist parsing |
| Unauthenticated access | `test_api_rejects_missing_or_wrong_token`, `test_unknown_host_header_is_rejected` |
| A non-result reported as passed | `test_run_reports_only_real_outcomes`: crashed engine → blocked, disabled → skipped, manual → not tested |
| Duplicate findings | `test_rerun_does_not_duplicate_findings` |
| Framework guessed from names | `test_framework_is_not_guessed_from_folder_names` |
| Contract checks wrong | `test_engines.py`: pass, schema violation, wrong status, invalid body, headers, latency |
| Testing an unauthorised system | `test_target_must_be_authorised_before_planning`, `test_unauthorised_target_and_redirect_escape_are_blocked` |
| Unwanted state changes on a target | `test_mutating_call_is_skipped_unless_owner_opted_in` |
| Unsafe or unverified repair | `test_repair.py`: refusal cases, 9 verdict cases, policy review, approval and sensitive confirmation |
| Unapproved AI spend | `test_ai.py`: default disabled, incomplete approval refused, budget hard stop, unknown prices |
| Bad model output accepted | `test_invalid_model_output_is_an_error_and_still_accounted` |

## Latest results (2026-11-13)

| Suite | Result |
|---|---|
| `pytest` | 76 passed |
| Ruff lint + format, mypy (63 source files) | clean |
| `tsc --noEmit`, ESLint (0 warnings), `next build` | clean, 25 routes |
| `scripts/e2e_verify.py` | 51/51 checks passed, including a deterministic and a local-AI repair, both verified |
| `scripts/backup.ps1 -Verify` | restore verified (row counts equal) |

## Gaps (see KNOWN_LIMITATIONS.md)

- No automated tests for the dashboard (no component or browser tests of the UI itself) and none for the dashboard proxy route.
- Sandbox, Trivy, Semgrep, Playwright and repair execution are covered only by the end-to-end script, not by `pytest`.
- No fault-injection tests for resource limits or worker crash recovery.
- No coverage measurement.
- The PowerShell scripts have no automated tests; several were run manually (listed in PROGRESS.md).

## Next tests to add

1. Playwright tests of the dashboard's main flow (import → run → finding → approve).
2. `pytest -m docker` suite: sandbox denies network, enforces memory and timeout.
3. Tests for the proxy route (host, origin, path checks).
4. Coverage reporting with a floor for `security/`, `repair/` and `runs/`.
