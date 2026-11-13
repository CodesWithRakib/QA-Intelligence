# Roadmap

## Delivered (MVP, 2026-11-13)

| Phase | Scope | State |
|---|---|---|
| 0 | Machine inspection, environment verification | Done (nothing had to be installed system-wide) |
| 1 | Architecture, database, migrations, compose, health, logging, dashboard shell | Done |
| 2 | Local / ZIP / OpenAPI / URL import, discovery, snapshots | Done |
| 3 | Plans, cases, runs, job lifecycle, evidence | Done |
| 4 | Browser, API, static analysis, dependency scan, accessibility, basic performance, static DB check | Done |
| 5 | Ollama, provider abstraction, routing, budgets, explanations, suggestions, benchmark | Done; benchmark and suggestions not run end to end |
| 6 | Proposals, approval, isolated patches, policy review, regression verification | Done for two strategies |
| 7 | Dashboard screens, reports, audit log | Built; not yet exercised in a browser |
| 8 | Hardening: tests, backup/restore verification, limitations, readiness | Partly: see ACCEPTANCE_CRITERIA.md |

## Next: close the verification gaps (recommended first)

1. Click through the dashboard against the running stack; fix what surfaces; add Playwright tests for the main flow.
2. Sandbox fault-injection tests (memory, PIDs, timeout, network denial).
3. Clean-machine setup run; pin scanner images by digest.
4. Run the model benchmark and record results; evaluate one alternative small model.
5. Data retention: delete/archive projects, clean repair workspaces.

## Then: depth

| Theme | Items |
|---|---|
| Ingestion | Git clone inside a sandbox container; GitHub/GitLab with explicit approval and tokens |
| Functional | Authenticated sessions (stored login state), generated form and journey tests |
| API | Auth schemes, pagination/filtering checks, stateful sequences |
| Security | OWASP ZAP baseline against authorised targets; egress allowlist for the install phase |
| Performance | k6 with hard caps and explicit authorisation; bundle size analysis |
| Database | Read-only live checks: constraints, missing indexes, slow queries |
| Repair | npm/pnpm lockfile upgrades via the package manager in the sandbox; more deterministic fixers (headers, simple a11y) |
| Discovery | AST-based extraction; resolve router prefixes across files; coverage measurement |
| Isolation | Browser engine in a container; optional VM-backed execution for high-risk projects |
| Product | Product-intelligence agent with facts and AI suggestions clearly separated |
| Platform | Multi-user accounts and roles; report export (HTML/PDF); scheduled runs; Linux/macOS scripts |

## Explicitly out of scope for now

Kubernetes, microservices, hosted multi-tenant operation, automatic pushing of fixes to remotes.
