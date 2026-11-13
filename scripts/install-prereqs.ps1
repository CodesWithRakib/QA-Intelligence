<#
.SYNOPSIS
  Installs missing prerequisites with winget (official packages only).
.DESCRIPTION
  Idempotent: tools that are already installed are skipped and never downgraded or upgraded.
  Shows the plan first; nothing is installed without -Apply.
  Docker Desktop and WSL need administrator approval (UAC) and possibly a Windows restart:
  the script tells you and stops instead of forcing either.
.EXAMPLE
  scripts\install-prereqs.ps1            # show what would be installed
  scripts\install-prereqs.ps1 -Apply     # install it
#>
[CmdletBinding()]
param([switch]$Apply, [switch]$IncludeOptional)
. "$PSScriptRoot\_common.ps1"

if (-not (Test-Command 'winget')) { throw 'winget is not available. Install "App Installer" from the Microsoft Store.' }

# command, winget id, needs admin, optional
$packages = @(
    @('git', 'Git.Git', $false, $false),
    @('python', 'Python.Python.3.13', $false, $false),
    @('node', 'OpenJS.NodeJS.LTS', $false, $false),
    @('docker', 'Docker.DockerDesktop', $true, $false),
    @('ollama', 'Ollama.Ollama', $false, $true)
)

$plan = @()
foreach ($package in $packages) {
    $command, $id, $needsAdmin, $optional = $package
    if (Test-Command $command) { Write-Ok "$command already installed - skipping $id"; continue }
    if ($optional -and -not $IncludeOptional) { Write-Note "$id is optional (use -IncludeOptional to install it)"; continue }
    $plan += , @($id, $needsAdmin)
}
if (-not (Test-Command 'pnpm') -and (Test-Command 'node' )) { Write-Note 'pnpm missing: it will be enabled with "corepack enable pnpm" (no admin needed)' }

$drive = Get-PSDrive -Name (Split-Path -Qualifier $RepoRoot).TrimEnd(':')
if ($drive.Free / 1GB -lt 10) { throw 'Less than 10 GB free on the workspace drive: free up space before installing.' }

if ($plan.Count -eq 0 -and (Test-Command 'pnpm')) { Write-Host 'Nothing to install.' -ForegroundColor Green; exit 0 }
Write-Step 'Planned installations'
foreach ($item in $plan) { Write-Host ("  {0}{1}" -f $item[0], $(if ($item[1]) { '  (administrator approval required)' } else { '' })) }
if (-not $Apply) { Write-Host "`nDry run. Re-run with -Apply to install." -ForegroundColor Yellow; exit 0 }

foreach ($item in $plan) {
    $id, $needsAdmin = $item
    Write-Step "Installing $id"
    if ($needsAdmin) { Write-Note 'Windows will ask for administrator approval (UAC). A restart may be required afterwards.' }
    winget install --id $id --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "winget could not install $id (exit code $LASTEXITCODE). Nothing else was changed for this package."
        Write-Note 'Check network access and permissions, then re-run this script. It is safe to re-run.'
        exit 1
    }
    Write-Ok "$id installed"
}
if (-not (Test-Command 'pnpm')) {
    Write-Step 'Enabling pnpm through corepack'
    corepack enable pnpm
}
Write-Host "`nDone. Open a NEW terminal so PATH changes apply, then run scripts\check-prereqs.ps1." -ForegroundColor Green
Write-Note 'After installing Docker Desktop: start it once, accept the terms, and enable the WSL 2 backend.'
