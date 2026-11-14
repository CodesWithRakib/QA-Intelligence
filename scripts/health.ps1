<#
.SYNOPSIS
  Health check of the running stack (same data as Dashboard -> System Health).
#>
[CmdletBinding()]
param()
. "$PSScriptRoot\_common.ps1"
$env = Get-DotEnv
$apiPort = if ($env['QAI_API_PORT']) { $env['QAI_API_PORT'] } else { '8000' }
$headers = @{ Authorization = "Bearer $($env['QAI_API_TOKEN'])" }

try { $health = Invoke-RestMethod "http://127.0.0.1:$apiPort/api/system/health" -Headers $headers -TimeoutSec 60 }
catch { Write-Bad "API not reachable on port $apiPort ($($_.Exception.Message)). Start it with scripts\dev.ps1."; exit 1 }

foreach ($check in $health.checks.PSObject.Properties) {
    $label = "{0}: {1}" -f $check.Name, $check.Value.detail
    $optional = [bool]($check.Value.PSObject.Properties['optional'] -and $check.Value.optional)
    if ($check.Value.ok) { Write-Ok $label } elseif ($optional) { Write-Note "$label (optional)" } else { Write-Bad $label }
}
try {
    $page = Invoke-WebRequest 'http://127.0.0.1:3000/' -TimeoutSec 20 -UseBasicParsing
    Write-Ok "dashboard: HTTP $($page.StatusCode) on http://127.0.0.1:3000"
} catch { Write-Bad 'dashboard: not reachable on http://127.0.0.1:3000'; exit 1 }

Write-Host ("queued jobs: {0}" -f $health.queued_jobs)
if (-not $health.healthy) { Write-Host 'Stack is DEGRADED: affected checks will be reported as blocked.' -ForegroundColor Yellow; exit 1 }
Write-Host 'Stack is healthy.' -ForegroundColor Green
