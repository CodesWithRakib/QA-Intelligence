<#
.SYNOPSIS
  Starts Docker Desktop if needed and brings up the PostgreSQL container. Safe to re-run.
#>
[CmdletBinding()]
param([int]$DockerTimeoutSeconds = 180)
. "$PSScriptRoot\_common.ps1"
Get-DotEnv | Out-Null  # fail early when .env is missing

if (-not (Test-DockerRunning)) {
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $desktop)) { throw 'Docker Desktop is not installed. Run scripts\install-prereqs.ps1 -Apply.' }
    Write-Step 'Starting Docker Desktop'
    Start-Process -FilePath $desktop
    $deadline = (Get-Date).AddSeconds($DockerTimeoutSeconds)
    while (-not (Test-DockerRunning)) {
        if ((Get-Date) -gt $deadline) { throw "Docker did not become ready within $DockerTimeoutSeconds seconds. Open Docker Desktop and check its status." }
        Start-Sleep -Seconds 4
    }
}
Write-Ok 'Docker daemon is running'

Write-Step 'Starting PostgreSQL (docker compose)'
Push-Location $RepoRoot
try { Invoke-Checked 'docker compose up' { docker compose up --detach --wait } } finally { Pop-Location }
Write-Ok 'PostgreSQL is healthy on 127.0.0.1 (see POSTGRES_PORT in .env)'
