<#
.SYNOPSIS
  One-time project setup on a machine that already passes scripts\check-prereqs.ps1.
.DESCRIPTION
  Safe to re-run. Steps: .env with generated secrets, backend virtualenv + pinned dependencies,
  Playwright Chromium, frontend dependencies, Docker images, database + migrations.
  Use -SkipImages to avoid the large scanner image downloads (about 2.5 GB).
#>
[CmdletBinding()]
param([switch]$SkipImages)
. "$PSScriptRoot\_common.ps1"

Write-Step 'Environment file'
& "$PSScriptRoot\setup-env.ps1"

Write-Step 'Backend virtual environment'
if (-not (Test-Path $VenvPython)) { Invoke-Checked 'venv creation' { python -m venv (Join-Path $BackendDir '.venv') } }
Push-Location $BackendDir
try {
    Invoke-Checked 'pip upgrade' { & $VenvPython -m pip install --quiet --upgrade pip }
    # requirements.lock pins every transitive dependency; the editable install adds the app itself.
    Invoke-Checked 'dependency install' { & $VenvPython -m pip install --quiet -r requirements.lock }
    Invoke-Checked 'app install' { & $VenvPython -m pip install --quiet --no-deps -e . }
    Write-Step 'Playwright Chromium (headless shell)'
    Invoke-Checked 'playwright install' { & $VenvPython -m playwright install chromium --only-shell }
} finally { Pop-Location }

Write-Step 'Frontend dependencies'
Push-Location $FrontendDir
try { Invoke-Checked 'pnpm install' { pnpm install --frozen-lockfile } } finally { Pop-Location }

Write-Step 'Infrastructure and database'
& "$PSScriptRoot\start-infra.ps1"
& "$PSScriptRoot\migrate.ps1"

if ($SkipImages) {
    Write-Note 'Skipped image downloads. Scanner and sandbox checks will report "blocked" until the images are pulled.'
} else {
    Write-Step 'Sandbox and scanner images'
    foreach ($image in 'python:3.13-slim', 'aquasec/trivy:latest', 'semgrep/semgrep:latest') {
        docker image inspect $image --format '{{.Id}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Ok "$image already present" }
        else { Invoke-Checked "pull $image" { docker pull --quiet $image }; Write-Ok "$image pulled" }
    }
}
Write-Host "`nSetup complete. Next: scripts\setup-models.ps1 (local AI, optional) and scripts\dev.ps1." -ForegroundColor Green
