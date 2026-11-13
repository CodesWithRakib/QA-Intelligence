# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versions follow semantic versioning.

## [0.1.0] - 2026-11-13

First MVP. Not production-ready; see KNOWN_LIMITATIONS.md.

### Added

- **Ingestion**: local folder, ZIP upload, OpenAPI file and URL-only projects; immutable versions with content hashes; secret files excluded from workspaces.
- **Discovery**: deterministic architecture inventory (languages, frameworks, package managers, dependencies, test commands, routes, endpoints, data models, migrations, environment variable names, monorepo and container detection, sensitive files) with an explicit list of what is unsupported.
- **Planning**: rule-based test plans with coverage notes; manual cases for unautomated areas; optional AI-suggested ideas.
- **Engines**: sandboxed project tests (pytest; npm), Trivy (dependencies, secrets, configuration), Semgrep, built-in credential check, OpenAPI contract and negative API tests, security headers, latency sampling, Playwright page health / crawl / responsive checks, axe-core accessibility, page load timing, static migration check.
- **Results**: six explicit states; evidence with SHA-256; findings deduplicated by fingerprint.
- **Repair**: dependency upgrade strategy, AI patch strategy for failing pytest suites, owner approval with extra confirmation for sensitive paths, isolated git branch, binding policy review, advisory AI review, before/after verification, downloadable patch.
- **AI**: Ollama provider, optional Anthropic provider (disabled, unexercised), local-first routing, hard monthly and per-project budgets, usage ledger, six-task benchmark with deterministic graders.
- **Agents**: sixteen contracted agents with recorded executions; orchestrator-only invocation.
- **Platform**: FastAPI API with bearer token and host allowlist, PostgreSQL job queue, single worker with heartbeat and orphan recovery, JSON logging, audit trail, Markdown reports.
- **Dashboard**: Overview, Projects, Import, Project details, Architecture, Test Plans, Test Runs, Test Results, Findings, Finding details, Fix Review, Security, Performance, Accessibility & UX, Reports, AI Models, Execution, Integrations, Agents, Audit Log, System Health.
- **Operations**: PowerShell scripts for prerequisites, setup, environment validation, infrastructure, migrations, Ollama, models, start/stop, tests, health, backup with restore verification, restore; end-to-end acceptance script.
- **Demo**: `examples/demo-shop`, an intentionally defective application with ten documented defects.

### Fixed

- Trivy vulnerability database download failed inside the scanner container because it was unpacked into the 256 MB in-memory `/tmp`; `TMPDIR` now points at the cache volume.

### Known gaps

- Dashboard not yet exercised against the running API; several scripts not executed; no clean-machine setup run. Details in ACCEPTANCE_CRITERIA.md.
