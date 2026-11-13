<#
.SYNOPSIS
  Applies database migrations (alembic upgrade head) and verifies there is no schema drift.
.DESCRIPTION
  Idempotent. Run scripts\backup.ps1 first when the database holds data you care about.
#>
[CmdletBinding()]
param()
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path $VenvPython)) { throw 'Backend virtualenv missing. Run scripts\setup.ps1.' }

Push-Location $BackendDir
try {
    Write-Step 'Applying migrations'
    Invoke-Checked 'alembic upgrade' { & $VenvPython -m alembic upgrade head }
    Write-Step 'Checking that models and schema agree'
    Invoke-Checked 'alembic check' { & $VenvPython -m alembic check }
    Write-Ok ('Database is at revision ' + ((& $VenvPython -m alembic current 2>$null) -join ' '))
} finally { Pop-Location }
