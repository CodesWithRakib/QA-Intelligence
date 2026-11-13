# Product requirements

## Purpose

A self-hosted platform that imports a software project, works out what it is, plans and executes
quality checks, records defects with evidence, and proposes and verifies fixes under the owner's
control. It is an engineering tool, not a chatbot: verdicts come from deterministic tools, AI is advisory.

## User

One technical owner running the platform on their own PC, testing projects they own or are authorised to test.

## Principles

1. **Evidence or no claim.** Every result and finding links to stored evidence.
2. **Unknown is not passed.** Six explicit result states; unexecuted checks are reported as such.
3. **Untrusted by default.** Imported code runs only in sandboxes; targets must be authorised.
4. **Owner in control.** No patch, paid call or state-changing request without an explicit decision.
5. **Free first.** Fully functional without paid services.
6. **Honest scope.** Unsupported technology is named in the product, not hidden.

## Functional requirements and MVP status

| Area | Requirement | Status |
|---|---|---|
| Ingestion | Local folder, ZIP, OpenAPI file, running URL | Implemented, verified |
| Ingestion | Git / GitHub / GitLab | Not implemented |
| Discovery | Languages, frameworks, package managers, dependencies, scripts, tests, routes, endpoints, models, migrations, env names, monorepo, containers, sensitive files | Implemented; deep extraction for Python and JS/TS only |
| Planning | Deterministic plan with coverage notes; manual cases for unautomated areas | Implemented, verified |
| Planning | AI-suggested additional cases (non-executable ideas) | Implemented, not verified end to end |
| Functional | Run the project's own tests in a sandbox (pytest; npm test) | Python verified; Node implemented, not verified |
| Functional | Generated journey / form / CRUD / auth tests | Not implemented (manual cases) |
| Browser | Page health, bounded crawl, responsive overflow, screenshots | Implemented, verified |
| Browser | Visual regression, cross-browser | Not implemented |
| API | Contract (status + schema), unknown resource, invalid body, unknown route | Implemented, verified |
| API | Authenticated endpoints, pagination, filtering, rate limits | Not implemented |
| Database | Static check: models without migrations | Implemented |
| Database | Live schema / constraint / query checks | Not implemented |
| Security | Trivy (dependencies, secrets, config), Semgrep, built-in credential check, headers | Implemented, verified |
| Security | OWASP ZAP dynamic scanning | Not implemented |
| Performance | Sequential latency sample; page load timing, LCP | Implemented, verified |
| Performance | k6 load tests, bundle analysis | Not implemented |
| Accessibility | axe-core on rendered pages | Implemented, verified |
| Product intelligence | Missing workflows, UX review | Not implemented |
| Agents | Contracted, recorded, orchestrator-only invocation | Implemented (16 registered) |
| Repair | Dependency bump (pinned `requirements.txt`) | Implemented, verified |
| Repair | AI patch for failing pytest suites | Implemented, verified once with the local model |
| Repair | Approval, sensitive-path confirmation, isolated branch, policy review, before/after verification | Implemented, verified |
| AI | Ollama provider, routing, budgets, usage ledger, benchmark | Implemented; benchmark job not run end to end |
| AI | Anthropic provider | Implemented, **never executed** (no key, no approval) |
| AI | OpenAI provider | Not implemented (declared only) |
| Reporting | Markdown report from stored data | Implemented, verified |
| Dashboard | 20 required screens plus Agents | Implemented; builds and type-checks; **not yet exercised in a browser against the running API** |
| Operations | Setup, start/stop, health, backup with restore verification | Implemented; `dev.ps1`, `stop.ps1`, `restore.ps1`, `setup.ps1` not executed in this session |

## Non-functional requirements

| Requirement | Target | Status |
|---|---|---|
| Runs on 16 GB RAM with Docker capped at 4 GB | yes | Met with one job at a time; memory is tight (see environment report) |
| Works offline after setup | partly | Scanners need their databases/rules; dependency installs need the network |
| No paid dependency | yes | Met |
| Reproducible setup | scripts + lockfiles | Scripts written; a clean-machine run has not been performed |
| Accessibility of the dashboard | semantic HTML, labels, keyboard, colour not sole signal | Implemented by construction; not audited with axe or a screen reader |

## Compatibility matrix

| Technology | Inventory | Routes / endpoints | Dependency scan | Project tests | Repair |
|---|---|---|---|---|---|
| Python (pip, pyproject, poetry) | yes | FastAPI/Flask decorators (file-local prefixes only) | yes (Trivy) | pytest, verified | pinned requirements bump; AI patch |
| JavaScript / TypeScript (npm, pnpm, yarn) | yes | Next.js file routes, Express-style calls | yes (lockfile needed) | `npm test`, not verified | no |
| OpenAPI 3.x / Swagger 2 | yes | yes | n/a | contract tests (local `$ref` only) | no |
| Go, Rust, Java/Kotlin, PHP, Ruby, C# | manifest and framework only | no | Trivy where it supports the lockfile (not verified) | no | no |
| Other languages | file counts only | no | no | no | no |
| Monorepos | packages listed | per package | yes | per component with a test command | per component |
| Multi-service architectures | compose services listed | no cross-service analysis | yes | no orchestration of services | no |
