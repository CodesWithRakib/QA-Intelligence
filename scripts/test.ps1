<#
.SYNOPSIS
  Runs every static check and test suite. Exit code is non-zero if anything fails.
.DESCRIPTION
  Backend: ruff (lint + format check), mypy, pytest (needs PostgreSQL from start-infra.ps1; uses the
  disposable *_test database only). Frontend: tsc, eslint, and with -Build a production build.
  The end-to-end acceptance run is separate because it needs the whole stack:
    backend\.venv\Scripts\python scripts\e2e_verify.py
#>
[CmdletBinding()]
param([switch]$Build)
. "$PSScriptRoot\_common.ps1"
$results = [ordered]@{}

function Invoke-Check([string]$Name, [string]$WorkDir, [scriptblock]$Command) {
    Write-Step $Name
    Push-Location $WorkDir
    try { & $Command; $results[$Name] = ($LASTEXITCODE -eq 0) } finally { Pop-Location }
}

Invoke-Check 'backend: ruff lint' $BackendDir { & $VenvPython -m ruff check app tests }
Invoke-Check 'backend: ruff format' $BackendDir { & $VenvPython -m ruff format --check app tests }
Invoke-Check 'backend: mypy' $BackendDir { & $VenvPython -m mypy app }
Invoke-Check 'backend: pytest' $BackendDir { & $VenvPython -m pytest -q }
Invoke-Check 'frontend: tsc' $FrontendDir { pnpm exec tsc --noEmit }
Invoke-Check 'frontend: eslint' $FrontendDir { pnpm exec eslint . --max-warnings 0 }
if ($Build) { Invoke-Check 'frontend: build' $FrontendDir { pnpm build } }

Write-Host ''
foreach ($name in $results.Keys) { if ($results[$name]) { Write-Ok $name } else { Write-Bad $name } }
if ($results.Values -contains $false) { exit 1 }
Write-Host 'All checks passed.' -ForegroundColor Green
