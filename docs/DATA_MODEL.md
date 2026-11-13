# Data model

PostgreSQL 17, 26 tables, one migration (`backend/alembic/versions/0001_initial_schema.py`).
Models live in `backend/app/models/`. All primary keys are UUIDs; timestamps are `timestamptz`.
Enumerated columns are strings with CHECK constraints (`ck_<table>_<column>_valid`).

## Relationships

```
projects ─┬─ project_sources
          ├─ project_versions ── architecture_snapshots ─┬─ components
          │                                              ├─ routes
          │                                              └─ api_endpoints
          ├─ test_plans ── test_cases
          ├─ test_runs ── test_results ── evidence
          ├─ findings ─┬─ evidence
          │            └─ repair_proposals ── repair_executions ── regression_results
          └─ reports
jobs · agent_executions · audit_events · settings
ai_configurations · ai_usage · model_evaluations · integrations · users
```

## Tables

| Table | Purpose | Notable constraints |
|---|---|---|
| `users` | Reserved for multi-user support (unused in the MVP) | unique `username` |
| `projects` | A thing under test | unique `slug` |
| `project_sources` | Where content came from (`local_folder`, `zip`, `url`, `openapi`, `git`) | CHECK on `kind` |
| `project_versions` | Immutable import snapshot: content hash, workspace path, file count | unique (`project_id`, `number`) |
| `architecture_snapshots` | Deterministic inventory (JSONB) + optional AI summary in separate columns | unique `version_id` |
| `components`, `routes`, `api_endpoints` | Normalised parts of the inventory | index on `snapshot_id` |
| `test_plans` | Generated plan, target URL, coverage notes, `allow_mutations` | - |
| `test_cases` | One check: key, category, engine, origin (`deterministic`/`ai_suggested`), spec | unique (`plan_id`, `key`); CHECKs |
| `test_runs` | One execution of a plan; summary counts | CHECK on `status` |
| `test_results` | Outcome of one case in one run | unique (`run_id`, `case_id`); CHECK on the six result states |
| `findings` | A defect | unique (`project_id`, `fingerprint`); CHECKs on category, severity, status, verification |
| `evidence` | Screenshot, HTTP exchange, tool output, log or diff, with SHA-256 | - |
| `repair_proposals` | Diff, full replacement content, rationale, strategy, decision | CHECK on `status` |
| `repair_executions` | Isolated workspace, branch, review record, outcome | CHECK on `status` |
| `regression_results` | A check measured `before` or `after` a patch | CHECK on `status` |
| `reports` | Generated Markdown and counts | - |
| `ai_configurations` | One row per provider: enabled, approval timestamp, budgets, prices | unique `provider`; **no API keys** |
| `ai_usage` | One row per AI call: tokens, cost, latency, success | index on `provider` |
| `model_evaluations` | One row per benchmark task per model | index on `batch_id` |
| `agent_executions` | One row per agent invocation | index on `agent` |
| `jobs` | Work queue | CHECK on `status`; index on `status` |
| `integrations` | Reserved (no integration implemented) | - |
| `settings` | Key/value: execution settings, worker heartbeat | primary key `key` |
| `audit_events` | Append-only audit trail | index on `action` |

## Findings

Every finding has: id, project and version, category, severity, description, reproduction steps,
evidence, affected component, confidence (0-1), status, remediation, verification status, timestamps,
source tool, and machine-readable `facts` used by repair strategies.

Identity: `fingerprint = sha256(category | source_tool | key)`, where `key` is chosen by the engine
(for example `requirements.txt:jinja2`). Detecting the same defect again updates the existing row
(`last_seen_at`, latest run) instead of creating a duplicate. A finding whose fix was verified is
reopened if a newer version still shows the defect.

Status values: `open`, `fix_proposed`, `fixed`, `wont_fix`, `false_positive`.
`fixed` means "a fix was verified in an isolated workspace"; the user's own repository is unchanged.

## Deletion and retention

Foreign keys use `ON DELETE CASCADE` from project downwards, `SET NULL` for run/result references on
findings. There is no delete API in the MVP and no automatic retention: `data/` grows until cleaned
manually (see [OPERATIONS.md](OPERATIONS.md)).

## Migrations

`scripts/migrate.ps1` runs `alembic upgrade head` followed by `alembic check` (fails on drift).
The initial migration was verified upgrade → downgrade → upgrade. Future migrations must be additive
or come with a backup step; destructive changes need explicit approval.
