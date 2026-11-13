<#
.SYNOPSIS
  Ollama health check: API reachable, installed models, and a short real generation.
.DESCRIPTION
  Read-only apart from loading the model into memory. Exit code 1 when local AI is not usable.
  The platform works without it: AI features then report "unavailable".
#>
[CmdletBinding()]
param([switch]$SkipGeneration)
. "$PSScriptRoot\_common.ps1"
$base = (Get-DotEnv)['QAI_OLLAMA_BASE_URL']
if (-not $base) { $base = 'http://127.0.0.1:11434' }

try { $version = (Invoke-RestMethod "$base/api/version" -TimeoutSec 5).version }
catch { Write-Bad "Ollama API not reachable at $base. Start the Ollama app or run 'ollama serve'."; exit 1 }
Write-Ok "Ollama $version at $base"

$models = @((Invoke-RestMethod "$base/api/tags" -TimeoutSec 10).models)
if ($models.Count -eq 0) { Write-Bad 'No model installed. Run scripts\setup-models.ps1.'; exit 1 }
foreach ($model in $models) {
    Write-Ok ("model {0}: {1:N1} GB, {2} {3}" -f $model.name, ($model.size / 1GB), $model.details.parameter_size, $model.details.quantization_level)
}
if ($SkipGeneration) { exit 0 }

$name = $models[0].name
Write-Step "Timing a short generation with $name (the first call also loads the model)"
$body = @{ model = $name; stream = $false; think = $false; options = @{ num_predict = 24; temperature = 0 }
    messages = @(@{ role = 'user'; content = 'Reply with the single word: ready' }) } | ConvertTo-Json -Depth 5
$timer = [Diagnostics.Stopwatch]::StartNew()
try { $reply = Invoke-RestMethod "$base/api/chat" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 300 }
catch { Write-Bad "Generation failed: $($_.Exception.Message)"; exit 1 }
$timer.Stop()
$speed = if ($reply.eval_duration) { [math]::Round($reply.eval_count / ($reply.eval_duration / 1e9), 1) } else { 0 }
Write-Ok ("replied in {0:N1} s, {1} tokens/s generation speed" -f $timer.Elapsed.TotalSeconds, $speed)
if ($speed -gt 0 -and $speed -lt 5) { Write-Note 'Under 5 tokens/s: AI repair and benchmarks will be slow. Consider a smaller model.' }
