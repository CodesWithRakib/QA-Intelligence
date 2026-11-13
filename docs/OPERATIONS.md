# Operations

## Daily commands

| Task | Command |
|---|---|
| Start everything | `scripts\dev.ps1` (or `-Production`) |
| Stop application processes | `scripts\stop.ps1` (`-Infra` also stops PostgreSQL) |
| Health | `scripts\health.ps1` or Dashboard → System Health |
| Apply migrations | `scripts\migrate.ps1` |
| All static checks and tests | `scripts\test.ps1` (`-Build` adds the dashboard build) |
| End-to-end acceptance | `backend\.venv\Scripts\python scripts\e2e_verify.py` |
| Demo target | `scripts\start-demo-target.ps1` / `-Stop` |

## Logs and monitoring

- `data\logs\api.log`, `worker.log`, `frontend.log`: JSON lines (`ts`, `level`, `logger`, `msg`, extra fields). Secrets are not logged.
- Dashboard → **System Health**: database, worker heartbeat (stale after 45 s), Docker, images, Ollama, disk, recent jobs with errors.
- Dashboard → **Audit Log**: who did what (imports, approvals, settings and AI configuration changes).
- Dashboard → **Agents**: every agent invocation with duration and outcome.

## Backup and recovery

```powershell
scripts\backup.ps1 -Verify                      # dump + data archive, then test-restore into a scratch database
scripts\stop.ps1
scripts\restore.ps1 -Backup backups\<timestamp>  # destructive, asks for confirmation
scripts\dev.ps1
```

- A backup is `database.dump` (pg_dump custom format, with SHA-256) and `data.tar.gz` (workspaces, evidence, reports, repairs).
- `-Verify` restores the dump into `<db>_restore_check`, compares row counts with the live database and drops the scratch database. Verified on 2026-11-13.
- `restore.ps1` refuses to run while the API or worker is running, checks the checksum, takes a safety backup first, and keeps the previous data folders as `*.before-restore-<timestamp>`.
- `.env` is not part of a backup. Keep a copy of it separately: without the database password the volume cannot be opened.
- Backups contain imported source code and findings. Store them like source code.

## Recovery situations

| Situation | What happens / what to do |
|---|---|
| Worker crashes or the PC restarts during a run | On the next worker start, jobs left "running" are failed, the run is marked failed with a message, results already stored are kept. Start a new run |
| Docker stops during a run | Remaining Docker-based checks are "blocked"; others continue |
| Left-over sandbox containers/volumes after a crash | `docker ps -a --filter name=qai-` and `docker volume ls --filter name=qai-work-`; remove them manually |
| Database password lost | Restore `.env` from your copy. Otherwise recreate the volume and restore a backup |
| Migration fails | Nothing is half-applied (transactional DDL). Fix, then re-run `scripts\migrate.ps1` |

## Housekeeping

Nothing is cleaned automatically.

| Location | Content | Safe to delete |
|---|---|---|
| `data\repairs\<id>` | Isolated repair workspaces | Yes, once the patch is downloaded |
| `data\evidence\<run id>` | Screenshots | Only for runs you no longer need (evidence links break) |
| `data\cache` | Uploads in progress, benchmark scratch | Yes, when idle |
| `data\logs` | Logs | Yes, when stopped |
| Docker volume `qai-trivy-cache` | Vulnerability database | Yes; it is downloaded again |
| `backups\` | Backups | Your decision |

## Changing limits

Dashboard → **Execution**: sandbox CPU/memory/PIDs/timeouts, crawl and request limits, import limits,
authorised targets. Values are validated server-side and changes are audited. They apply to the next run.

## Updating

1. `scripts\backup.ps1 -Verify`
2. `git pull`
3. `scripts\setup.ps1` (installs pinned dependencies, applies migrations)
4. `scripts\test.ps1`

Scanner images use the `latest` tag: `docker pull aquasec/trivy:latest semgrep/semgrep:latest` updates
them. Results can change when rules or databases change.

## Paid AI

Stays off until approved under Dashboard → AI Models with a model id, budgets and token prices.
Revoking approval there stops paid calls immediately. Spend is visible on the same page and in `ai_usage`.
