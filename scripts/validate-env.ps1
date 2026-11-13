<#
.SYNOPSIS
  Validates .env without printing any secret value.
#>
[CmdletBinding()]
param()
. "$PSScriptRoot\_common.ps1"
$values = Get-DotEnv
$failed = $false

foreach ($key in 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB', 'QAI_DATABASE_URL', 'QAI_TEST_DATABASE_URL', 'QAI_API_TOKEN', 'QAI_BACKEND_URL') {
    if (-not $values[$key]) { Write-Bad "$key is missing or empty"; $failed = $true } else { Write-Ok "$key is set" }
}
foreach ($key in 'POSTGRES_PASSWORD', 'QAI_API_TOKEN') {
    $value = [string]$values[$key]
    if ($value -like 'change-me*' -or $value.Length -lt 24) { Write-Bad "$key is a placeholder or too short: run scripts\setup-env.ps1 -Force"; $failed = $true }
}
if ($values['QAI_DATABASE_URL'] -like '*change-me*') { Write-Bad 'QAI_DATABASE_URL still contains the placeholder password'; $failed = $true }
if ($values['QAI_TEST_DATABASE_URL'] -and $values['QAI_TEST_DATABASE_URL'] -notmatch '_test$') {
    Write-Bad 'QAI_TEST_DATABASE_URL must point at a database whose name ends with _test'; $failed = $true
}
if ($values['QAI_API_HOST'] -and $values['QAI_API_HOST'] -notin '127.0.0.1', 'localhost') {
    Write-Bad 'QAI_API_HOST must stay on 127.0.0.1: the API has no multi-user authentication'; $failed = $true
}
foreach ($key in 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY') {
    if ($values[$key]) { Write-Note "$key is set. Paid usage still needs explicit approval in the dashboard." }
    else { Write-Ok "$key not set (paid AI disabled)" }
}

Push-Location $RepoRoot
try {
    git check-ignore --quiet .env
    if ($LASTEXITCODE -eq 0) { Write-Ok '.env is ignored by git' } else { Write-Bad '.env is NOT ignored by git'; $failed = $true }
    if (git ls-files .env) { Write-Bad '.env is tracked by git: remove it from the index and rotate the secrets'; $failed = $true }
} finally { Pop-Location }

if ($failed) { exit 1 }
Write-Host 'Environment configuration is valid.' -ForegroundColor Green
