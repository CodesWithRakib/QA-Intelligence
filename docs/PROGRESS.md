# Progress log

Newest first. Update after each meaningful milestone.

## 2026-11-13 - MVP built and verified end to end

**State**: backend complete and verified; dashboard built but not yet exercised in a browser;
operations scripts written, about half of them executed.

### Milestones (in order)

1. Machine inspected. Git, Python, Node, pnpm, Docker, WSL 2, Ollama and a local model were already
   present; only the Docker daemon had to be started. Workspace is on `C:` (there is no `D:` drive).
2. Docker verified: container start, networking, volume persistence, resource limits, non-root, no-network.
3. Scaffold: `.gitignore`, `.env.example`, generated `.env`, compose with PostgreSQL on 55432.
4. Data model (26 tables) and initial migration; upgrade/downgrade/upgrade verified.
5. Security foundations: outbound policy, safe archive handling.
6. Discovery, ingestion, planner.
7. Engines: sandbox, project tests, Trivy/Semgrep/built-in, API, browser + axe, static DB check.
8. Job queue, worker, run service, agent registry.
9. AI layer: providers, routing, budgets, benchmark, advisory tasks.
10. Repair: strategies, policy review, isolated execution, verdict.
11. Reports, HTTP API (4 routers), 76 backend tests passing.
12. Demo target with ten seeded defects.
13. First end-to-end run: one real defect in the platform found (Trivy database download did not fit
    the container's in-memory `/tmp`; correctly reported as "blocked"). Fixed by pointing `TMPDIR` at the cache volume.
14. Second end-to-end run: **51/51 checks passed**, including both repair strategies.
15. Dashboard: 25 routes; `tsc`, ESLint and `next build` clean.
16. Scripts and documentation.

### Scripts executed successfully

`setup-env`, `check-prereqs`, `validate-env`, `migrate`, `check-ollama`, `setup-models` (no-op path),
`install-prereqs` (dry run), `backup -Verify`, `test`.

### Scripts written but not executed

`dev`, `stop`, `health`, `setup`, `restore`, `start-demo-target` (the same Docker commands were run by
hand), `install-prereqs -Apply`.

### Open items

See ACCEPTANCE_CRITERIA.md (criteria 1, 2, 22, 24) and KNOWN_LIMITATIONS.md.

### Notes for the next session

- The background API and worker were stopped by the tooling when the machine ran critically low on
  memory. Nothing is running except the PostgreSQL container and the demo target container
  (`qai-demo-shop`). Start with `scripts\dev.ps1 -Production`.
- The development database contains two "Demo Shop (e2e)" projects from the verification runs.
- The repository owner committed the first part of the work at 18:02 (`9229d7f`); everything after
  that is uncommitted in the working tree.
