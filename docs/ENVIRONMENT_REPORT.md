# Environment report

Inspected on 2026-11-13 by running `scripts/check-prereqs.ps1`, `scripts/check-ollama.ps1` and direct
version queries. Re-run those scripts to refresh this information.

## Differences from the stated setup

| Stated | Actually found | Consequence |
|---|---|---|
| Workspace `D:\QA-Intelligence` | There is no `D:` drive. The repository is `C:\Sofof_tech_2026\QA-Intelligence` | Everything was built in the existing repository |
| Integrated graphics | NVIDIA GeForce GTX 1660 SUPER (dedicated GPU) | Ollama uses the GPU: 66.6 tokens/s measured, local AI is practical |
| Docker / WSL / Ollama "may be missing" | All installed | No installation was necessary; nothing needed administrator rights or a restart |

## Hardware and OS

| Item | Value |
|---|---|
| OS | Windows 11 Pro, build 26200 |
| Shell | PowerShell 7.6.9 |
| CPU | AMD Ryzen 5 3400G, 4 cores / 8 threads, virtualization enabled |
| RAM | 15.9 GB total (4-8 GB free during the session) |
| GPU | NVIDIA GeForce GTX 1660 SUPER |
| Disk | `C:` 237.5 GB, 54 GB free after setup (58 GB before) |

## Tools

| Tool | Version | Status |
|---|---|---|
| Git | 2.50.1 | already installed |
| Python | 3.13.17 | already installed |
| Node.js | 26.8.1 | already installed |
| pnpm | 12.4.2 | already installed |
| Docker Desktop (engine) | 29.8.2, Compose v5.5.1 | installed; daemon was stopped and was started |
| WSL | 2.6.3, distributions `Ubuntu` and `docker-desktop` (both WSL 2) | already installed |
| Ollama | 0.40.16 | already installed and running |
| VS Code | 1.146.0 | already installed |
| winget | 1.29.380 | available |
| GitHub CLI | - | not installed (optional, not needed) |

## Installed by this setup

| What | Where | Size |
|---|---|---|
| Backend virtualenv, 55 pinned packages (`backend/requirements.lock`) | `backend/.venv` | - |
| Playwright Chromium headless shell 158.0.8127.18 | `%LOCALAPPDATA%\ms-playwright` | 123 MB download |
| Frontend packages (`frontend/pnpm-lock.yaml`): Next.js 16.4.6, React 19.3.1, Tailwind 4.3.5, TypeScript 6 | `frontend/node_modules` | - |
| `postgres:17-alpine` | Docker | 424 MB |
| `python:3.13-slim` (sandbox runtime) | Docker | 178 MB |
| `node:22-slim` (sandbox runtime, optional) | Docker | 329 MB |
| `aquasec/trivy:latest` (0.76.2) | Docker | 264 MB |
| `semgrep/semgrep:latest` (1.184.0) | Docker | 1.59 GB |
| axe-core 4.10.3 (vendored, sha256 `880970c0…68bb`) | `backend/app/engines/vendor/axe.min.js` | 553 KB |

Docker totals after setup: images 3.3 GB, volumes 2.0 GB (PostgreSQL data and the Trivy vulnerability DB).

No global package was upgraded or downgraded. TypeScript was pinned to 6.x inside the frontend only,
because `typescript-eslint` 8.72 refuses to run on TypeScript 7.

## Local AI

| Item | Value |
|---|---|
| API | `http://127.0.0.1:11434`, reachable |
| Installed model | `qwen3.5:4b` (4.2B parameters, Q4_K_M, 3.1 GB) |
| Measured speed | 66.6 tokens/s; 18 s for the first reply including model load |
| Extra models downloaded | none (the installed model passed the end-to-end repair task, so `qwen2.5-coder:3b` was not pulled) |

## Resource limits in effect

| Limit | Value | Set in |
|---|---|---|
| WSL / Docker VM | 4 GB RAM, 4 CPUs, 2 GB swap, `autoMemoryReclaim=gradual` | `%USERPROFILE%\.wslconfig` (pre-existing, unchanged) |
| PostgreSQL container | 1 CPU, 512 MB | `docker-compose.yml` |
| Sandbox container | 1 CPU, 768 MB, 256 PIDs, 256 MB tmpfs, install 420 s, tests 300 s | Dashboard → Execution |
| Scanner container | 1 CPU, 1536 MB, 600 s | `backend/app/engines/security_scan.py` |
| Demo target | 0.5 CPU, 256 MB | `scripts/start-demo-target.ps1` |

## Ports

| Port | Use |
|---|---|
| 3000 | Dashboard (127.0.0.1 only) |
| 8000 | Backend API (127.0.0.1 only) |
| 55432 | Project PostgreSQL (127.0.0.1 only) |
| 8100 | Demo target (127.0.0.1 only) |
| 5432 | **Pre-existing** Windows service `postgresql-x64-18`. Not used and not touched |
| 11434 | Ollama |

## Known environment constraints

- 16 GB RAM is tight when Docker (4 GB), Ollama with a loaded model (about 3-4 GB of GPU/system memory),
  the Next.js dev server and an IDE run together. During this session the tooling stopped the background
  API and worker once because the machine was critically low on memory. Use `scripts/dev.ps1 -Production`
  to run the lighter production dashboard build.
- One test run is executed at a time (single worker) to stay within the Docker memory cap.
- A pre-existing Docker container/volume belonging to another project (`postgres:18.6-alpine`) was left untouched.
