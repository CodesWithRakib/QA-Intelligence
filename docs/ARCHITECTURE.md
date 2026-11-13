# Architecture

A modular monolith: one Python backend package with strict module boundaries, one worker process,
one Next.js dashboard, PostgreSQL, and throw-away Docker containers for anything untrusted.

```
 Browser ──► Next.js dashboard (127.0.0.1:3000)
               │  /api/* proxy route: adds the API token, enforces localhost + same-origin
               ▼
            FastAPI (127.0.0.1:8000) ──► PostgreSQL 17 (container, 127.0.0.1:55432)
               │ enqueue job (row in `jobs`)        ▲
               ▼                                    │
            Worker / orchestrator ──────────────────┘
               ├─ Playwright Chromium (host process, policy-guarded network)
               ├─ httpx policy client ──► authorised targets only
               ├─ docker run ──► sandbox containers (project tests)   [untrusted code]
               ├─ docker run ──► Trivy / Semgrep containers           [trusted tools, read-only source]
               └─ Ollama (127.0.0.1:11434)                            [optional, advisory]
```

## Processes

| Process | Start | Role |
|---|---|---|
| API | `uvicorn app.main:app` | Validation, authentication, persistence, enqueueing. Never runs long work |
| Worker | `python -m app.worker` | Claims jobs (`FOR UPDATE SKIP LOCKED`), runs the fixed workflows, one job at a time |
| Dashboard | `pnpm dev` / `pnpm start` | UI plus the server-side API proxy |
| PostgreSQL | `docker compose up` | The only stateful infrastructure component |

The API and worker run on the host, not in containers, so that no container ever needs the Docker
socket (ADR-003).

## Backend modules (`backend/app`)

| Module | Responsibility | May depend on |
|---|---|---|
| `domain.py` | State vocabularies (result states, severities, ...) | nothing |
| `models/` | SQLAlchemy models, 26 tables | `domain` |
| `security/` | Outbound policy (`ssrf.py`), safe import (`archive.py`) | nothing |
| `ingestion/` | Source → immutable project version | `security`, `discovery` |
| `discovery/` | Deterministic architecture inventory | `security.archive` constants |
| `planning/` | Inventory → test plan | `models` |
| `engines/` | QA engines. **Database-free**: context in, outcome + evidence out | `security`, `services.settings` |
| `runs/` | Run lifecycle, persistence of results / evidence / findings | `engines`, `agents`, `jobs` |
| `ai/` | Providers, routing, budgets, benchmark, advisory tasks | `models`, `engines.sandbox` (benchmark grading) |
| `repair/` | Strategies, policy review, isolated execution, verdict | `engines`, `ai` |
| `reports/` | Markdown report from stored rows | `models` |
| `agents/` | Agent contracts and execution records | `models` |
| `jobs/` | PostgreSQL job queue | `models` |
| `api/` | Thin HTTP layer and serializers | everything above |

Rules that keep the boundaries honest: engines never import the database session; API handlers
contain no business rules; only `engines/sandbox.py` builds `docker run` commands; only
`security/ssrf.py` decides whether a network destination is allowed.

## Main workflows

**Import**: validate source → copy/extract into `data/workspaces/<id>` (limits, no symlinks, secret
files excluded) → content hash → `ProjectVersion` → discovery → `ArchitectureSnapshot`.

**Test run**: `POST /plans/{id}/runs` validates the target against the allowlist and enqueues a job →
the worker executes each enabled case through its engine → each outcome is committed immediately
(result, evidence, findings upserted by fingerprint) → summary counted from stored rows.
An engine exception becomes a `blocked` result; it never aborts the run and is never reported as passed.

**Repair**: finding → proposal (deterministic strategy, else AI patch) → owner decision → copy of the
version in `data/repairs/<id>` with `git init` + branch → baseline checks → apply → policy review →
same checks again → verdict. See [SECURITY_MODEL.md](SECURITY_MODEL.md) and `app/repair/service.py`.

## Result states

`passed`, `failed`, `skipped`, `blocked`, `not_tested`, `not_applicable`. Enforced by a CHECK
constraint. `blocked` = the check could not be executed (tool missing, target unreachable, not
authorised, engine error). `not_tested` = no automated engine exists (manual cases). Neither is ever
converted into `passed`, and reports state their count explicitly.

## Frontend

Next.js 16 App Router. Every screen is a client component using TanStack Query through one `api()`
helper (`src/lib/api.ts`); loading and error states are rendered by one `QueryView` component.
Shared primitives live in `src/components/ui.tsx` (hand-written in the shadcn style, see ADR-007).
The browser never holds the API token: `src/app/api/[...path]/route.ts` attaches it server-side.

## Folder structure

```
backend/      app/ (modules above), alembic/ (migrations), tests/, pyproject.toml, requirements.lock
frontend/     src/app (25 routes), src/components, src/lib
examples/     demo-shop/ - intentionally defective target used for verification
scripts/      PowerShell setup / operations scripts, e2e_verify.py
infra/        postgres init script
docs/         this documentation
data/         runtime data: workspaces, evidence, repairs, reports, logs   (git-ignored)
backups/      output of scripts/backup.ps1                                 (git-ignored)
```
