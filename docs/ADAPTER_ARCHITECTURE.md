# Adapter architecture

One adapter per testing discipline, all behind the same contract. The planner and the run service
talk only to the registry; they do not know what Playwright, Trivy or adb are.

```
planner ──► registry.ADAPTERS ──► adapter.capabilities() / adapter.plan()        (what applies, which cases)
run service ─► adapter.readiness() ─► adapter.execute() ─► CaseOutcome            (per case)
run service ─► adapter.cleanup()                                                  (once, at the end, reverse order)
```

Code: `backend/app/adapters/` (`base.py`, `web.py`, `android.py`, `registry.py`). Engines stay in
`backend/app/engines/` and are database-free; adapters are thin and own planning and readiness.

## The contract (`adapters/base.py`)

| Operation | Purpose | Returns |
|---|---|---|
| `capabilities(plan_ctx)` | What the adapter can do for **this** project. Empty list = not applicable | `list[str]` |
| `environment(settings)` | Are the tools it needs installed and running | `ReadinessReport` |
| `plan(plan_ctx)` | Concrete cases derived from the project inventory, plus coverage notes | `PlanResult(cases, notes)` |
| `readiness(ctx, spec)` | Can this one case run right now; if not, the exact reason | `ReadinessReport` |
| `execute(ctx, spec)` | Run the case | `CaseOutcome(status, message, details, evidence, findings)` |
| `cleanup(ctx)` | Release what the run acquired | `dict` (stored in `run.progress.cleanup`) |

Shared types: `PlanContext` (snapshot, workspace, target, settings, source kind), `EngineContext`
(the same for execution, plus `run_cache` shared by the cases of one run), `ReadinessReport`,
`PlannedCase`. Normalised results: one of six states, evidence drafts, finding drafts with a
classification (confirmed / potential / environment).

## Registered adapters (15)

| Adapter | Engine(s) | What it plans | Needs |
|---|---|---|---|
| `web_detection` | - | Nothing; reports detected frameworks with evidence | - |
| `android_detection` | - | Nothing; reports APKs, Android source projects, TWA configs | - |
| `web_security` | `dependency_scan`, `static_analysis`, `api`, `dast` | Trivy, Semgrep, gitleaks (tree + git history), built-in credentials, config, headers, cookies, CORS, ZAP baseline | Docker; target for the HTTP checks |
| `project_pipeline` | `project_tests` | Type-check, lint, tests, build from the project's own scripts; pytest | Docker sandbox |
| `database` | `database_static` | Models have versioned migrations | Source |
| `apk_static` | `android_static` | Metadata, manifest + signing review, content scan | aapt2, JDK |
| `environment` | `environment` | Starts the managed local copy of the project | Docker, profile |
| `web_functional` | `browser` | Page health, cross-browser smoke, crawl, route sweep, forms, responsive, PWA | Target |
| `api` | `api` | OpenAPI contract and negative tests, latency | Target |
| `visual` | `browser` | Screenshot comparison against baselines | Target |
| `accessibility` | `browser` | axe-core | Target |
| `web_performance` | `browser`, `lighthouse`, `load_test` | Timing/LCP/CLS, Lighthouse, bounded k6 | Target |
| `android_dynamic` | `android_dynamic` | Install/launch, Appium exploration, lifecycle, offline | Device |
| `android_accessibility` | `android_dynamic` | Labels and touch targets from the UI hierarchy | Device |
| `android_performance` | `android_dynamic` | Start time, memory, frames | Device |

Each planned case stores its adapter name in `spec.adapter`; plans created before adapters existed are
routed by engine (`registry._LEGACY`).

## Adding an adapter

1. Write the engine function(s) in `app/engines/` (no database access; return `CaseOutcome`).
2. Subclass `Adapter` in `app/adapters/`: set `name`, `title`, `agent`; implement `capabilities`, `plan`,
   `execute`; override `environment` / `readiness` / `cleanup` when it uses tools or acquires resources.
3. Append an instance to `ADAPTERS` in `registry.py` (the position is the planning and execution order).
4. If it introduces a new engine or category value, add it to `domain.py` and to a migration (the columns
   are guarded by CHECK constraints).

Nothing in the planner, run service, API or dashboard needs to change: the dashboard reads adapters,
readiness and results generically. This was exercised in Phase 2 itself: the `environment` adapter was
added after the other fourteen without touching the orchestration code (only the cleanup order was made
explicit).

## Orchestration rules

- **One job at a time** in one worker: one browser process, one device session, one sandbox job. Cases run
  sequentially, ordered by priority, then adapter order, then key.
- **Readiness before execution**, for every case, immediately before it runs (not only at plan time).
- **Every outcome is committed immediately**, so a crash or cancellation loses nothing already measured.
- **An adapter exception becomes a `blocked` result** with the error; it never aborts the run and is never a pass.
- **Cancellation** is checked between cases; remaining cases are recorded as `skipped`.
- **Cleanup always runs**, in reverse adapter order (things that depend on a resource stop before it is
  removed). A failing cleanup is recorded in the run and does not prevent the others.
- **Progress is real**: the run stores the case currently executing, its adapter and index; no estimates.

## What the adapters do not do

- Retries: a failed or blocked case is not retried automatically (a second identical run is one click).
- Parallelism: deliberately absent on this hardware.
- Per-case wall-clock enforcement by the orchestrator: every engine enforces its own timeouts
  (container, browser, HTTP, adb); there is no outer kill switch around `execute`.
