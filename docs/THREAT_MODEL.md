# Threat model

Scope: the self-hosted MVP on a single Windows PC. Method: assets, actors, then threats per entry
point with the implemented control and its verification. Residual risks are listed in
[SECURITY_MODEL.md](SECURITY_MODEL.md).

## Assets

1. The host: personal files, credentials, other software.
2. Platform secrets: API token, database password, optional provider API keys.
3. Integrity of results: a wrong "passed" or "verified" is the worst product failure.
4. Imported source code, findings and evidence (confidential).
5. Money: paid AI spend.
6. Third parties: systems the platform could be pointed at.

## Actors

| Actor | Capability |
|---|---|
| Malicious imported project | Controls archive structure, file names, file contents, dependency manifests, test code |
| Malicious tested target | Controls HTTP responses, redirects, page scripts |
| Malicious web page in the owner's browser | Can send requests to localhost ports (CSRF, DNS rebinding) |
| Local AI model output | Untrusted text: may be wrong or manipulated by prompt injection in project files |
| Other local process / LAN host | Can reach listening ports |

## Threats and controls

| # | Threat | Control | Verified by |
|---|---|---|---|
| T1 | Zip-slip / absolute path writes outside the workspace | Path normalisation + containment check | `test_zip_with_unsafe_paths_is_rejected` |
| T2 | Symlink in archive or folder exposes host files | Symlinks rejected (ZIP) / skipped (folder) | `test_zip_symlink_is_rejected` |
| T3 | Decompression bomb exhausts disk | Size, count and ratio limits on real bytes | `test_zip_bomb_is_stopped_by_ratio_and_size_limits`, `test_zip_file_count_limit` |
| T4 | Imported secrets end up in workspace, evidence or reports | Secret files not copied; secret values masked in evidence | `test_zip_extracts_sources_but_never_secret_files`, `test_run_reports_only_real_outcomes` |
| T5 | Imported code executes on the host | Import and discovery only parse; tests run only in the sandbox | Code review of `ingestion/`, `discovery/`; E2E "sandbox isolation recorded" |
| T6 | Sandbox code attacks host or network | No capabilities, non-root, read-only rootfs, limits, tests without network | Manual check in `ENVIRONMENT_REPORT`; residual: kernel escape, install-phase egress |
| T7 | Fork bomb / memory or time exhaustion | PID, memory, CPU limits and timeouts with `docker kill` | Limits visible in `sandbox.run_container`; not fault-injected |
| T8 | SSRF: target or redirect points at internal services or metadata | Allowlist, address checks, per-hop revalidation | `tests/test_security.py` (8 tests), `test_unauthorised_target_and_redirect_escape_are_blocked` |
| T9 | Platform used to attack third parties | Allowlist required for every target; no load testing; bounded request counts | `test_target_must_be_authorised_before_planning` |
| T10 | Web page triggers actions on the local API (CSRF / rebinding) | Bearer token, Host allowlist, same-origin check in proxy | `test_api_rejects_missing_or_wrong_token`, `test_unknown_host_header_is_rejected`; proxy checks reviewed, not unit-tested |
| T11 | Path traversal through evidence download | Path resolved and contained in the data directory | Code review (`api/qa.py`); not unit-tested |
| T12 | AI patch edits tests, deletes files or touches unrelated files | Schema restricts paths; binding policy review | `test_policy_review_rejects_test_tampering_deletions_and_undeclared_files` |
| T13 | Unverified fix reported as successful | Verdict needs reproduce-before, pass-after, policy approval and regression evidence | `test_fix_is_verified_only_with_full_before_after_evidence` (9 cases) |
| T14 | Failed or crashed check reported as passed | `blocked` state; counts taken from stored rows | `test_run_reports_only_real_outcomes` |
| T15 | Silent paid AI spend | Approval, prices, budgets, double check, usage ledger | `tests/test_ai.py` (4 tests) |
| T16 | Secrets committed to git | `.gitignore`, `validate-env.ps1` | `git ls-files` contains no `.env`, `data/` or `backups/` |
| T17 | SQL injection in the platform | SQLAlchemy bound parameters only; `LIKE` wildcards stripped from user input | Code review, Ruff `S` rules |
| T18 | Prompt injection through project files | AI output is advisory; patches still pass policy review and sandbox verification | By design (ADR-005) |
| T19 | Malicious git content in repair workspace (hooks, config) | Fresh `git init`; hooks path, global and system config disabled | Code review (`repair/review.py`) |

## Not covered

- A hostile local user or malware already running as the owner.
- Supply-chain compromise of the platform's own dependencies or container images (images are pulled by tag, not pinned by digest).
- Denial of service against the platform by its own owner.
