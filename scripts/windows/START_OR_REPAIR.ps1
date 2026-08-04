param([switch]$SkipPoll)

$ErrorActionPreference = "Stop"
$programRoot = Join-Path $env:LOCALAPPDATA "SAT2Relay"
$config = Join-Path $programRoot "config.yml"
$relay = Join-Path $programRoot "venv\Scripts\sat2-relay.exe"
$python = Join-Path $programRoot "venv\Scripts\python.exe"

foreach ($path in @($config, $relay, $python)) {
  if (-not (Test-Path $path)) { throw "SAT2 Relay is not installed correctly. Missing: $path" }
}

$apiToken = (& $python -c "from pathlib import Path; from sat2_relay.config import load_local_config; print(load_local_config(Path(r'$config')).api_token)").Trim()
$logDir = (& $python -c "from pathlib import Path; from sat2_relay.config import load_local_config; print(load_local_config(Path(r'$config')).log_path.parent)").Trim()
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$bootstrapLog = Join-Path $logDir "on-demand-bootstrap.log"
$headers = @{ "X-SAT2-Relay-Token" = $apiToken }
$healthUrl = "http://127.0.0.1:8765/api/v2/health"

function Write-BootstrapLog([string]$message) {
  Add-Content -Path $bootstrapLog -Value "$(Get-Date -Format o) $message" -Encoding utf8
  Write-Host $message
}
function Test-RelayHealth {
  try { return $null -ne (Invoke-RestMethod -Headers $headers -Uri $healthUrl -TimeoutSec 2) }
  catch { return $false }
}

if (-not (Test-RelayHealth)) {
  $listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $ownedByRelay = $owner -and (($owner.ExecutablePath -and $owner.ExecutablePath.StartsWith($programRoot, [StringComparison]::OrdinalIgnoreCase)) -or ($owner.CommandLine -and $owner.CommandLine.IndexOf($programRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0))
    if (-not $ownedByRelay) { throw "Port 127.0.0.1:8765 is occupied by another process (PID $($listener.OwningProcess))." }
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
  }
  Write-BootstrapLog "Starting SAT2 Relay supervisor on demand."
  $process = Start-Process -FilePath $relay -ArgumentList @("--config", "`"$config`"", "supervise") -WindowStyle Hidden -PassThru
  for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-RelayHealth) { break }
    if ($process.HasExited) { throw "SAT2 Relay supervisor exited with code $($process.ExitCode)." }
  }
  if (-not (Test-RelayHealth)) { throw "SAT2 Relay did not become healthy within 15 seconds." }
} else { Write-BootstrapLog "SAT2 Relay is already healthy; no duplicate process was started." }

Write-BootstrapLog "Health check passed."
if (-not $SkipPoll) {
  Invoke-RestMethod -Method Post -Headers $headers -Uri "http://127.0.0.1:8765/api/v2/control/poll" -TimeoutSec 60 | Out-Null
  Write-BootstrapLog "Immediate poll completed."
}
Write-Host "Dashboard: http://127.0.0.1:8765/"
