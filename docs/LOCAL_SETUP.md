# Local setup

Windows 11, PowerShell 7. Run every command from the repository root.
If scripts are blocked, allow them for the session: `Set-ExecutionPolicy -Scope Process RemoteSigned`.

## 1. Prerequisites

```powershell
scripts\check-prereqs.ps1            # read-only report
scripts\install-prereqs.ps1          # shows what would be installed
scripts\install-prereqs.ps1 -Apply   # installs missing tools with winget
```

Docker Desktop needs administrator approval and may need a restart; the script says so and stops.
After installing it, start it once and enable the WSL 2 backend. Minimum versions and resource
recommendations: [ENVIRONMENT_REQUIREMENTS.md](ENVIRONMENT_REQUIREMENTS.md).

## 2. Project setup (once)

```powershell
scripts\setup.ps1            # add -SkipImages to postpone the 2.5 GB scanner image downloads
scripts\setup-models.ps1     # optional: makes sure one local model exists in Ollama
scripts\check-ollama.ps1     # optional: verifies and times the local model
```

`setup.ps1` creates `.env` with generated secrets, the backend virtualenv from `requirements.lock`,
Playwright Chromium, frontend packages from `pnpm-lock.yaml`, the PostgreSQL container, and applies
migrations. It is safe to re-run.

## 3. Start

```powershell
scripts\dev.ps1               # dev servers
scripts\dev.ps1 -Production   # optimised dashboard build, uses less memory
scripts\health.ps1
```

Dashboard: http://127.0.0.1:3000. Logs: `data\logs\{api,worker,frontend}.log`. Stop: `scripts\stop.ps1`
(add `-Infra` to also stop PostgreSQL).

## 4. Try it with the demo target

```powershell
scripts\start-demo-target.ps1     # intentionally defective app on http://127.0.0.1:8100
```

1. Dashboard → **Execution** → Authorised targets: add `127.0.0.1:8100`, save.
2. **Projects → Import project**: Local folder, path `<repo>\examples\demo-shop`, target URL `http://127.0.0.1:8100`.
3. Open the project → **Architecture** to see the inventory.
4. **Test Plans** → select the project → Generate plan → **Start test run**.
5. Watch **Test Runs**; then **Bugs & Findings**.
6. Open the `jinja2 3.1.2 …` finding → **Propose a repair** → **Fix Review** → Approve and verify.
7. Open the run → **Generate report**.

The same flow, automated: `backend\.venv\Scripts\python scripts\e2e_verify.py` (needs the stack and the demo target running).

## 5. Import your own project

- **Local folder**: absolute path. `node_modules`, `.git`, build output and secret files are not copied.
- **ZIP**: up to 100 MB compressed / 300 MB extracted by default (Execution settings).
- **OpenAPI file**: `.json`, `.yaml`, `.yml`.
- **URL only**: browser, header and generic API checks; no static analysis.

For browser and API tests the application must be running, and its `host:port` must be on the
authorised-targets list. Only add systems you own or are authorised to test. API contract tests
additionally need an OpenAPI file inside the imported project (file name containing `openapi`,
`swagger` or `api-spec`).

## 6. Review and approve repairs

1. Finding → **Propose a repair**. A proposal is only a diff.
2. **Fix Review** → open the proposal: read the rationale and the diff. AI-generated patches are labelled.
3. **Approve and verify** (sensitive paths need an extra confirmation) or **Reject**.
4. The execution shows before/after results for the targeted check and the project's tests, the policy review, and the advisory AI review.
5. Only "verified" means the defect was reproduced, fixed, and no test newly fails.
6. **Download .patch** and apply it to your own repository: `git apply repair-xxxx.patch`. The platform never changes your source.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Checks are "blocked: Docker is not running" | `scripts\start-infra.ps1` |
| "blocked: scanner image … is not pulled" | `docker pull <image>` or re-run `scripts\setup.ps1` |
| "not an authorised target" | Add `host:port` under Execution settings |
| System Health shows the worker as unavailable | Worker not running: `scripts\dev.ps1`; see `data\logs\worker.log` |
| Runs stay "queued" | Same as above |
| AI actions fail with "AI unavailable" | Start Ollama; `scripts\check-ollama.ps1` |
| Port 5432 conflict | Not applicable: the project uses 55432 (`POSTGRES_PORT` in `.env`) |
| Dashboard shows "backend API is not reachable" | API not running or `QAI_BACKEND_URL` wrong |
