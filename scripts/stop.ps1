<#
.SYNOPSIS
  Stops the processes started by scripts\dev.ps1. PostgreSQL keeps running unless -Infra is given.
.DESCRIPTION
  Only processes recorded in data\logs\*.pid (and their children) are stopped. Data is never deleted.
#>
[CmdletBinding()]
param([switch]$Infra)
. "$PSScriptRoot\_common.ps1"
$logs = Join-Path $RepoRoot 'data\logs'

foreach ($name in 'frontend', 'worker', 'api') {
    $pidFile = Join-Path $logs "$name.pid"
    if (-not (Test-Path $pidFile)) { Write-Note "$name is not running (no pid file)"; continue }
    $processId = [int](Get-Content $pidFile)
    if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
        # /T also stops the child processes (python, node) of the wrapper shell.
        taskkill /PID $processId /T /F 2>&1 | Out-Null
        Write-Ok "$name stopped"
    } else { Write-Note "$name was not running" }
    Remove-Item $pidFile -Confirm:$false
}
if ($Infra) {
    Push-Location $RepoRoot
    try { docker compose stop | Out-Null; Write-Ok 'PostgreSQL container stopped (its data volume is kept)' } finally { Pop-Location }
}
