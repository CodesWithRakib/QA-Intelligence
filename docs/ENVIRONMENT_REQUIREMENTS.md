# Environment requirements

## Required

| Component | Minimum | Verified with | Notes |
|---|---|---|---|
| Windows | 10 21H2 / 11 with WSL 2 | Windows 11 Pro build 26200 | Scripts are PowerShell-only |
| PowerShell | 7.2 | 7.6.9 | `pwsh`; Windows PowerShell 5.1 is not supported |
| Git | 2.30 | 2.50.1 | Used for isolated repair branches |
| Python | 3.12 | 3.13.17 | Backend and worker |
| Node.js | 20.9 | 26.8.1 | Dashboard |
| pnpm | 9 | 12.4.2 | |
| Docker Desktop (WSL 2 backend) | engine 24 | 29.8.2 | Sandbox, scanners, PostgreSQL |
| CPU virtualization | enabled in firmware | yes | Required by WSL 2 |

## Optional

| Component | Purpose | Without it |
|---|---|---|
| Ollama ≥ 0.5 with one model | AI explanations, summaries, AI patches, advisory review | AI features report "unavailable"; everything else works |
| GPU | Faster local inference | CPU inference works but AI repair becomes slow |
| `anthropic` Python package + API key | Paid premium tasks | Stays disabled (default) |

## Hardware

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 16 GB | 32 GB if Docker, Ollama and an IDE run together |
| Free disk | 15 GB | 30 GB (images 3.3 GB, scanner DB 1-2 GB, model 2-4 GB, workspaces grow) |
| CPU | 4 cores | 6+ |

## Recommended WSL limits (`%USERPROFILE%\.wslconfig`)

For a 16 GB machine. Not changed by the setup scripts because the file is global to all WSL use.

```ini
[wsl2]
memory=4GB
processors=4
swap=2GB
autoMemoryReclaim=gradual
```

Apply with `wsl --shutdown`, then restart Docker Desktop. Do not give Docker all available RAM.

## Network access needed

| When | Destination | Why |
|---|---|---|
| Setup | winget sources, PyPI, npm registry, Docker Hub, Playwright CDN, Ollama registry | Installation |
| Dependency scan | Trivy DB mirror (`mirror.gcr.io`) | Vulnerability database, cached in the `qai-trivy-cache` volume |
| Static analysis | `semgrep.dev` | Rule pack `p/default` (metrics disabled) |
| Sandbox dependency install | The project's package registries | Installing the imported project's dependencies |

Nothing is sent to a paid service unless explicitly approved.

## Ports

3000 (dashboard), 8000 (API), 55432 (PostgreSQL), 8100 (demo target), 11434 (Ollama). All bound to
127.0.0.1. Change `POSTGRES_PORT` / `QAI_API_PORT` in `.env` if needed.

## Environment variables

Defined in `.env.example`; `scripts\setup-env.ps1` generates the secrets and `scripts\validate-env.ps1`
checks them without printing values.

| Variable | Purpose |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | Database container |
| `QAI_DATABASE_URL`, `QAI_TEST_DATABASE_URL` | Application and test databases (the test name must end in `_test`) |
| `QAI_API_TOKEN` | Bearer token for the API (generated) |
| `QAI_DATA_DIR` | Runtime data directory (default `./data`) |
| `QAI_API_HOST`, `QAI_API_PORT`, `QAI_LOG_LEVEL` | API binding and logging |
| `QAI_OLLAMA_BASE_URL` | Local AI endpoint |
| `QAI_BACKEND_URL` | Where the dashboard proxy finds the API |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Optional; empty by default |
