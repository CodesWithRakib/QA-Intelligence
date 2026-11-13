<#
.SYNOPSIS
  Starts the whole stack for local use: PostgreSQL, API, worker and dashboard.
.DESCRIPTION
  Each process runs in its own minimised PowerShell window and logs to data\logs.
  Stop everything with scripts\stop.ps1. Processes are bound to 127.0.0.1 only.
.PARAMETER Production
  Serve the optimised dashboard build (next build + next start) instead of the dev server.
  Uses noticeably less memory, recommended on a 16 GB machine.
#>
[CmdletBinding()]
param([switch]$Production)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path $VenvPython)) { throw 'Backend virtualenv missing. Run scripts\setup.ps1 first.' }

& "$PSScriptRoot\validate-env.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Fix the environment configuration first.' }
& "$PSScriptRoot\start-infra.ps1"
& "$PSScriptRoot\migrate.ps1"

$logs = Join-Path $RepoRoot 'data\logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$env = Get-DotEnv
$apiPort = if ($env['QAI_API_PORT']) { $env['QAI_API_PORT'] } else { '8000' }

function Start-Component([string]$Name, [string]$WorkDir, [string]$Command) {
    $log = Join-Path $logs "$Name.log"
    $pidFile = Join-Path $logs "$Name.pid"
    if (Test-Path $pidFile) {
        $existing = Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
        if ($existing) { Write-Ok "$Name already running (pid $($existing.Id))"; return }
    }
    $process = Start-Process pwsh -WindowStyle Minimized -PassThru -WorkingDirectory $WorkDir -ArgumentList @(
        '-NoProfile', '-Command', "`$host.UI.RawUI.WindowTitle = 'QAI $Name'; $Command *>> '$log'")
    Set-Content -Path $pidFile -Value $process.Id
    Write-Ok "$Name started (pid $($process.Id), log data\logs\$Name.log)"
}

Write-Step 'Starting application processes'
Start-Component 'api' $BackendDir "& '$VenvPython' -m uvicorn app.main:app --host 127.0.0.1 --port $apiPort"
Start-Component 'worker' $BackendDir "& '$VenvPython' -m app.worker"
if ($Production) {
    Push-Location $FrontendDir
    try { Invoke-Checked 'frontend build' { pnpm build } } finally { Pop-Location }
    Start-Component 'frontend' $FrontendDir 'pnpm start'
} else {
    Start-Component 'frontend' $FrontendDir 'pnpm dev'
}

Write-Step 'Waiting for the API'
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 2
    try { $up = (Invoke-RestMethod "http://127.0.0.1:$apiPort/health" -TimeoutSec 3).status -eq 'ok' } catch { $up = $false }
} until ($up -or (Get-Date) -gt $deadline)
if (-not $up) { throw 'The API did not start. See data\logs\api.log.' }
Write-Host "`nDashboard: http://127.0.0.1:3000   (API: http://127.0.0.1:$apiPort)" -ForegroundColor Green
Write-Host 'Check everything with scripts\health.ps1. Stop with scripts\stop.ps1.'
