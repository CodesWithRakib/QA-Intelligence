# Architecture decisions

Each record: context, decision, consequences. Add new records at the end; do not rewrite old ones.

## ADR-001 Modular monolith
**Context**: single owner, one PC, 16 GB RAM. **Decision**: one backend package with enforced module
boundaries instead of services. **Consequences**: one deployment unit and one database; modules can be
extracted later because engines are already database-free and communicate through plain data classes.

## ADR-002 PostgreSQL as the only store
**Decision**: PostgreSQL 17 for relational data, JSONB for flexible payloads (inventory, facts,
details). Evidence files stay on the local filesystem under `data/`, referenced by relative path and
SHA-256. **Consequences**: backups are one dump plus one archive. `pgvector` and MinIO are not used:
nothing in the MVP needs embeddings or object storage.

## ADR-003 API and worker run on the host; only infrastructure and untrusted work run in containers
**Context**: the worker must start sandbox containers. Running it in a container would require mounting
the Docker socket, which is equivalent to root on the host. **Decision**: host processes call the
`docker` CLI. **Consequences**: no container has the socket; Windows-specific start scripts are
needed; the host Python environment is part of the trusted computing base.

## ADR-004 PostgreSQL job queue instead of Redis
**Context**: the brief recommends Redis. The workload is a handful of long jobs, executed one at a
time. **Decision**: a `jobs` table claimed with `SELECT ... FOR UPDATE SKIP LOCKED`. **Consequences**:
one less service and about 100 MB less memory; jobs are transactional with the data they refer to
(a run and its job are created atomically). Not suitable for high job throughput; revisit if several
workers or sub-second scheduling are needed.

## ADR-005 Deterministic first, AI advisory
**Decision**: discovery, planning, all verdicts and the repair policy review are deterministic. AI
output is stored in separate fields (`ai_summary`, `ai_explanation`, proposal `generated_by`) and can
never set a result state or mark a fix verified. **Consequences**: results are reproducible and
auditable; AI quality only affects optional text and whether an AI patch survives verification.

## ADR-006 Browser engine runs on the host
**Context**: the Playwright image is about 2 GB and reaching `localhost` targets from a container
needs extra networking. **Decision**: headless Chromium runs as a host process with every request
checked by the outbound policy. **Consequences**: saves disk and memory; the Chromium sandbox is the
isolation boundary for page content. Listed as a residual risk; moving it into a container is planned.

## ADR-007 Hand-written UI primitives instead of the shadcn/ui CLI
**Decision**: about 15 small Tailwind components in `components/ui.tsx`, following the shadcn
conventions (variants, tokens). **Consequences**: no extra dependencies (Radix) for an MVP that needs
no complex overlays; switching to generated shadcn components later is mechanical.

## ADR-008 Single local owner; token + localhost instead of user accounts
**Decision**: the API requires a generated bearer token and only accepts `Host: 127.0.0.1/localhost`.
The dashboard proxy injects the token and blocks cross-origin state changes. **Consequences**: no login
screen; **must not be exposed to a network**. A `users` table exists for future multi-user support.

## ADR-009 Repairs never touch the user's source
**Decision**: patches are applied to a copy of the imported version on a new git branch. The owner
gets a verified `.patch`. **Consequences**: a wrong patch cannot damage anything; applying a verified
fix to the real repository is a manual step.

## ADR-010 Paid AI needs owner-entered prices
**Decision**: the platform does not ship a price table. Approval of a paid provider requires the
owner to enter input/output prices and budgets; without them calls are refused. **Consequences**:
cost estimates cannot silently go stale; the owner must copy prices from the provider's page.
Server-side model fallback is not enabled for the Anthropic adapter because a fallback model is billed
at different rates, which would break the hard budget calculation.

## ADR-011 TypeScript 6 in the frontend
**Context**: pnpm resolved TypeScript 7.0, which `typescript-eslint` 8.72 rejects. **Decision**: pin
`typescript@^6`. **Consequences**: linting works; move to 7.x when the linter supports it.
