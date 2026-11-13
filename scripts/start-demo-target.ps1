<#
.SYNOPSIS
  Builds and runs the intentionally defective demo application (examples\demo-shop) in a container.
.DESCRIPTION
  Bound to 127.0.0.1:8100 only, non-privileged, memory and CPU limited. Use -Stop to remove it.
  To test it, add 127.0.0.1:8100 under Dashboard -> Execution -> Authorised targets.
#>
[CmdletBinding()]
param([switch]$Stop)
. "$PSScriptRoot\_common.ps1"
$name = 'qai-demo-shop'
if (-not (Test-DockerRunning)) { throw 'Docker is not running. Run scripts\start-infra.ps1.' }

docker rm --force $name 2>$null | Out-Null
if ($Stop) { Write-Ok 'demo target removed'; exit 0 }

Write-Step 'Building demo image'
Invoke-Checked 'docker build' { docker build --quiet --tag $name (Join-Path $RepoRoot 'examples\demo-shop') }
Invoke-Checked 'docker run' {
    docker run --detach --name $name --publish 127.0.0.1:8100:8100 --memory 256m --cpus 0.5 `
        --cap-drop ALL --security-opt no-new-privileges $name
}
$deadline = (Get-Date).AddSeconds(40)
do {
    Start-Sleep -Seconds 2
    try { $up = (Invoke-RestMethod 'http://127.0.0.1:8100/health' -TimeoutSec 3).status -eq 'ok' } catch { $up = $false }
} until ($up -or (Get-Date) -gt $deadline)
if (-not $up) { throw "The demo target did not start. Inspect it with: docker logs $name" }
Write-Ok 'demo target running at http://127.0.0.1:8100 (intentionally defective - never expose it)'
