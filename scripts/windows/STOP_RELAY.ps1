$ErrorActionPreference = "Stop"
$root = Join-Path $env:LOCALAPPDATA "SAT2Relay"

$processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.ProcessId -ne $PID -and (
      ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) -or
      ($_.CommandLine -and $_.CommandLine.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
    )
  }

foreach ($process in $processes) {
  Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 500
$listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
  Write-Warning "Port 8765 is still listening. The owner may not be a SAT2 Relay process."
} else {
  Write-Host "SAT2 Relay stopped. No background daemon remains."
}
