<#
.SYNOPSIS
  Read-only prerequisite check. Reports what is installed, which version, and what is missing.
.DESCRIPTION
  Changes nothing. Exit code 0 when every required tool meets its minimum version, otherwise 1.
  Use scripts\install-prereqs.ps1 to install what is missing.
#>
[CmdletBinding()]
param()
. "$PSScriptRoot\_common.ps1"

# name, command, version arguments, minimum version, required, winget id
$tools = @(
    @('Git', 'git', '--version', '2.30', $true, 'Git.Git'),
    @('Python', 'python', '--version', '3.12', $true, 'Python.Python.3.13'),
    @('Node.js', 'node', '--version', '20.9', $true, 'OpenJS.NodeJS.LTS'),
    @('pnpm', 'pnpm', '--version', '9.0', $true, 'pnpm.pnpm'),
    @('Docker', 'docker', '--version', '24.0', $true, 'Docker.DockerDesktop'),
    @('Ollama', 'ollama', '--version', '0.5', $false, 'Ollama.Ollama'),
    @('GitHub CLI', 'gh', '--version', '2.0', $false, 'GitHub.cli')
)

$failed = $false
Write-Step 'Tools'
foreach ($tool in $tools) {
    $name, $command, $versionArgs, $minimum, $required, $wingetId = $tool
    if (-not (Test-Command $command)) {
        if ($required) { Write-Bad "$name is missing (winget id: $wingetId)"; $failed = $true }
        else { Write-Note "$name is not installed (optional, winget id: $wingetId)" }
        continue
    }
    $output = (& $command $versionArgs 2>&1 | Out-String)
    if ($output -match '(\d+\.\d+(\.\d+)?)') {
        $found = [version]$Matches[1]
        if ($found -lt [version]$minimum) { Write-Bad "$name $found is older than the required $minimum"; $failed = $true }
        else { Write-Ok "$name $found" }
    } else {
        Write-Note "$name is installed but its version could not be read"
    }
}

Write-Step 'Docker and WSL'
if (Test-DockerRunning) {
    $info = docker info --format '{{.ServerVersion}}|{{.MemTotal}}|{{.NCPU}}'
    $server, $memory, $cpus = $info -split '\|'
    Write-Ok ("Docker daemon {0}: {1:N1} GB RAM, {2} CPUs available to containers" -f $server, ([double]$memory / 1GB), $cpus)
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok 'Docker Compose plugin available' } else { Write-Bad 'Docker Compose plugin missing'; $failed = $true }
} else {
    Write-Bad 'Docker daemon is not running (start Docker Desktop, or run scripts\start-infra.ps1)'
    $failed = $true
}
$wslConfig = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $wslConfig) { Write-Ok ".wslconfig present (WSL/Docker resource limits are set there)" }
else { Write-Note 'No .wslconfig: WSL may use up to half of the RAM. See docs\ENVIRONMENT_REQUIREMENTS.md' }

Write-Step 'Machine'
$os = Get-CimInstance Win32_OperatingSystem
Write-Ok ("{0} build {1}; RAM {2:N1} GB total, {3:N1} GB free" -f $os.Caption, $os.BuildNumber, ($os.TotalVisibleMemorySize / 1MB), ($os.FreePhysicalMemory / 1MB))
$drive = Get-PSDrive -Name (Split-Path -Qualifier $RepoRoot).TrimEnd(':')
$freeGb = [math]::Round($drive.Free / 1GB, 1)
if ($freeGb -lt 15) { Write-Bad "Only $freeGb GB free on the workspace drive (15 GB recommended)"; $failed = $true }
else { Write-Ok "$freeGb GB free on the workspace drive" }

Write-Step 'Ports'
foreach ($port in 3000, 8000, 8100, 55432) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $process = (Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Write-Note "Port $port is in use by $process (fine if that is this project)"
    } else { Write-Ok "Port $port is free" }
}

if ($failed) { Write-Host "`nPrerequisites are NOT satisfied." -ForegroundColor Red; exit 1 }
Write-Host "`nAll required prerequisites are satisfied." -ForegroundColor Green
