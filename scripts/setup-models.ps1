<#
.SYNOPSIS
  Makes sure one suitable local model is installed in Ollama.
.DESCRIPTION
  Does nothing when a model already exists (no duplicate multi-GB downloads). Otherwise checks RAM
  and disk first and pulls ONE small coding model. Never touches paid providers.
.PARAMETER Model
  Model to pull when none is installed. Default: qwen2.5-coder:3b (about 2 GB).
#>
[CmdletBinding()]
param([string]$Model = 'qwen2.5-coder:3b', [switch]$Force)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Command 'ollama')) { throw 'Ollama is not installed. Run scripts\install-prereqs.ps1 -Apply -IncludeOptional.' }
$base = (Get-DotEnv)['QAI_OLLAMA_BASE_URL']
if (-not $base) { $base = 'http://127.0.0.1:11434' }

try { $installed = @((Invoke-RestMethod "$base/api/tags" -TimeoutSec 10).models | ForEach-Object name) }
catch { throw "Ollama API not reachable at $base. Start the Ollama app first." }

if ($installed.Count -gt 0 -and -not $Force) {
    Write-Ok ("Model(s) already installed: {0}. Nothing to download." -f ($installed -join ', '))
    Write-Note 'Compare models objectively under Dashboard -> AI Models -> Run benchmark.'
    exit 0
}
if ($installed -contains $Model) { Write-Ok "$Model is already installed."; exit 0 }

$os = Get-CimInstance Win32_OperatingSystem
$ramGb = $os.TotalVisibleMemorySize / 1MB
$freeGb = (Get-PSDrive -Name ($env:USERPROFILE.Substring(0, 1))).Free / 1GB
if ($ramGb -lt 8) { throw ("Only {0:N0} GB RAM: local models are not practical on this machine." -f $ramGb) }
if ($freeGb -lt 8) { throw ("Only {0:N1} GB free on the user drive: at least 8 GB is needed to pull a model safely." -f $freeGb) }

Write-Step "Pulling $Model"
ollama pull $Model
if ($LASTEXITCODE -ne 0) { throw "ollama pull failed (exit code $LASTEXITCODE)" }
Write-Ok "$Model installed. Verify with scripts\check-ollama.ps1."
