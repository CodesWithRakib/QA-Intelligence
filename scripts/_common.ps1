# Shared helpers for the scripts in this folder. Dot-source it: . "$PSScriptRoot\_common.ps1"
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:BackendDir = Join-Path $RepoRoot 'backend'
$script:FrontendDir = Join-Path $RepoRoot 'frontend'
$script:VenvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "  [ok]   $Message" -ForegroundColor Green }
function Write-Bad([string]$Message) { Write-Host "  [FAIL] $Message" -ForegroundColor Red }
function Write-Note([string]$Message) { Write-Host "  [note] $Message" -ForegroundColor Yellow }

function Get-DotEnv {
    <# Returns the repository .env as a hashtable. Values are never printed by these scripts. #>
    $path = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $path)) { throw '.env not found. Run scripts\setup-env.ps1 first.' }
    $values = @{}
    foreach ($line in Get-Content $path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
    }
    return $values
}

function Test-Command([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Test-DockerRunning {
    if (-not (Test-Command 'docker')) { return $false }
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Invoke-Checked([string]$Description, [scriptblock]$Command) {
    <# Runs a native command and fails loudly on a non-zero exit code. #>
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)" }
}
