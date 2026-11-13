<#
.SYNOPSIS
  Backs up the database (pg_dump, custom format) and the data directory into backups\<timestamp>.
.DESCRIPTION
  Read-only for the live system. With -Verify the dump is restored into a throw-away database,
  row counts are compared with the live database, and the throw-away database is dropped.
  Backups contain imported project sources and findings: keep them private. .env is NOT included.
#>
[CmdletBinding()]
param([switch]$Verify, [switch]$SkipFiles)
. "$PSScriptRoot\_common.ps1"
$env = Get-DotEnv
$user, $database = $env['POSTGRES_USER'], $env['POSTGRES_DB']
if (-not (Test-DockerRunning)) { throw 'Docker is not running: cannot reach the database container.' }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$target = Join-Path $RepoRoot "backups\$stamp"
New-Item -ItemType Directory -Force $target | Out-Null
$dump = Join-Path $target 'database.dump'

Push-Location $RepoRoot
try {
    Write-Step 'Dumping database'
    # The dump is written inside the container, then copied out: avoids PowerShell re-encoding binary output.
    Invoke-Checked 'pg_dump' { docker compose exec -T postgres pg_dump -U $user -d $database --format=custom --file=/tmp/qai.dump }
    Invoke-Checked 'copy dump' { docker compose cp postgres:/tmp/qai.dump $dump }
    docker compose exec -T postgres rm -f /tmp/qai.dump | Out-Null
    Write-Ok ("database.dump ({0:N1} MB)" -f ((Get-Item $dump).Length / 1MB))

    if (-not $SkipFiles) {
        Write-Step 'Archiving data directory (workspaces, evidence, reports, repairs)'
        $dataDir = Join-Path $RepoRoot 'data'
        $folders = 'workspaces', 'evidence', 'reports', 'repairs' | Where-Object { Test-Path (Join-Path $dataDir $_) }
        if ($folders) {
            Invoke-Checked 'archive data' { tar -czf (Join-Path $target 'data.tar.gz') -C $dataDir @folders }
            Write-Ok ("data.tar.gz ({0:N1} MB)" -f ((Get-Item (Join-Path $target 'data.tar.gz')).Length / 1MB))
        } else { Write-Note 'No data folders yet.' }
    }
    (Get-FileHash $dump -Algorithm SHA256).Hash | Set-Content (Join-Path $target 'database.dump.sha256')

    if ($Verify) {
        Write-Step 'Verifying: restoring into a throw-away database'
        $scratch = "${database}_restore_check"
        $count = "SELECT (SELECT count(*) FROM projects) || '/' || (SELECT count(*) FROM findings) || '/' || (SELECT count(*) FROM test_results) || '/' || (SELECT count(*) FROM audit_events)"
        Invoke-Checked 'copy dump in' { docker compose cp $dump postgres:/tmp/verify.dump }
        docker compose exec -T postgres dropdb -U $user --if-exists $scratch | Out-Null
        Invoke-Checked 'createdb' { docker compose exec -T postgres createdb -U $user $scratch }
        try {
            Invoke-Checked 'pg_restore' { docker compose exec -T postgres pg_restore -U $user -d $scratch --no-owner --exit-on-error /tmp/verify.dump }
            $live = (docker compose exec -T postgres psql -U $user -d $database -Atc $count).Trim()
            $restored = (docker compose exec -T postgres psql -U $user -d $scratch -Atc $count).Trim()
            if ($live -ne $restored) { throw "row counts differ (live $live, restored $restored). Was the system writing during the backup?" }
            Write-Ok "restore verified: projects/findings/results/audit = $restored"
        } finally {
            docker compose exec -T postgres dropdb -U $user --if-exists $scratch | Out-Null
            docker compose exec -T postgres rm -f /tmp/verify.dump | Out-Null
        }
    }
} finally { Pop-Location }
Write-Host "Backup written to backups\$stamp" -ForegroundColor Green
