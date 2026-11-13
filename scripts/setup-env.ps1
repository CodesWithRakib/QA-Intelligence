<#
.SYNOPSIS
  Creates .env from .env.example with securely generated local secrets.
.DESCRIPTION
  Idempotent: an existing .env is never overwritten (use -Force to regenerate; this
  invalidates the database password of an existing volume, so it asks first).
  Secrets are written to .env only and are never printed.
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
$example = Join-Path $root '.env.example'

if ((Test-Path $envFile) -and -not $Force) {
    Write-Host '.env already exists - leaving it untouched.'
    exit 0
}
if ((Test-Path $envFile) -and $Force) {
    Write-Warning 'Regenerating .env changes the DB password; an existing postgres volume will reject it.'
    $answer = Read-Host 'Type YES to continue'
    if ($answer -ne 'YES') { Write-Host 'Aborted.'; exit 1 }
}

function New-Secret([int]$Bytes = 32) {
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    # URL-safe so the value can be embedded in a connection string without escaping.
    return [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

$dbPassword = New-Secret 24
$apiToken = New-Secret 32
$content = Get-Content $example -Raw
$content = $content.Replace('change-me-generated-by-setup-env@', "$dbPassword@")
$content = $content.Replace('POSTGRES_PASSWORD=change-me-generated-by-setup-env', "POSTGRES_PASSWORD=$dbPassword")
$content = $content.Replace('QAI_API_TOKEN=change-me-generated-by-setup-env', "QAI_API_TOKEN=$apiToken")
Set-Content -Path $envFile -Value $content -NoNewline -Encoding utf8
Write-Host '.env created with generated secrets (values not shown).'
