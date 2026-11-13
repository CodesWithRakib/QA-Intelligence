<#
.SYNOPSIS
  Restores a backup created by scripts\backup.ps1. DESTRUCTIVE: replaces the current database.
.DESCRIPTION
  Safety steps: the application must be stopped (scripts\stop.ps1), the dump checksum is verified,
  a fresh safety backup of the current state is taken first, and you must type the confirmation.
  The previous data directory is kept as data\<folder>.before-restore-<timestamp>.
.EXAMPLE
  scripts\restore.ps1 -Backup backups\20261113-190000
#>
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Backup, [switch]$SkipFiles)
. "$PSScriptRoot\_common.ps1"
$env = Get-DotEnv
$user, $database = $env['POSTGRES_USER'], $env['POSTGRES_DB']
$source = Resolve-Path $Backup
$dump = Join-Path $source 'database.dump'
if (-not (Test-Path $dump)) { throw "No database.dump in $source" }

$expected = (Get-Content (Join-Path $source 'database.dump.sha256') -ErrorAction SilentlyContinue)
if ($expected -and (Get-FileHash $dump -Algorithm SHA256).Hash -ne $expected.Trim()) { throw 'Checksum mismatch: the backup file is corrupted or was modified.' }

foreach ($name in 'api', 'worker') {
    $pidFile = Join-Path $RepoRoot "data\logs\$name.pid"
    if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
        throw "The $name process is running. Run scripts\stop.ps1 first."
    }
}

Write-Warning "This REPLACES database '$database' with the backup from $source."
if ((Read-Host "Type RESTORE to continue") -cne 'RESTORE') { Write-Host 'Aborted. Nothing was changed.'; exit 1 }

Write-Step 'Safety backup of the current state'
& "$PSScriptRoot\backup.ps1"

Push-Location $RepoRoot
try {
    Write-Step 'Restoring database'
    Invoke-Checked 'copy dump in' { docker compose cp $dump postgres:/tmp/restore.dump }
    # --clean --if-exists drops and recreates the objects contained in the dump, in one transaction.
    Invoke-Checked 'pg_restore' { docker compose exec -T postgres pg_restore -U $user -d $database --clean --if-exists --no-owner --single-transaction --exit-on-error /tmp/restore.dump }
    docker compose exec -T postgres rm -f /tmp/restore.dump | Out-Null
    Write-Ok 'database restored'

    $archive = Join-Path $source 'data.tar.gz'
    if (-not $SkipFiles -and (Test-Path $archive)) {
        Write-Step 'Restoring data directory'
        $dataDir = Join-Path $RepoRoot 'data'
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        foreach ($folder in 'workspaces', 'evidence', 'reports', 'repairs') {
            $path = Join-Path $dataDir $folder
            if (Test-Path $path) { Rename-Item $path "$folder.before-restore-$stamp" }
        }
        Invoke-Checked 'extract data' { tar -xzf $archive -C $dataDir }
        Write-Ok "data restored (previous folders kept as *.before-restore-$stamp)"
    }
} finally { Pop-Location }
& "$PSScriptRoot\migrate.ps1"
Write-Host 'Restore complete. Start the application with scripts\dev.ps1.' -ForegroundColor Green
