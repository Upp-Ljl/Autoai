param([switch]$KeepData)
$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "SAT2Relay"
foreach ($name in @("SAT2 Relay", "SAT2 Relay 2")) {
  Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
}
if (-not $KeepData -and (Test-Path $Root)) { Remove-Item -Recurse -Force $Root }
Write-Host "SAT2 Relay scheduled task removed."
if ($KeepData) { Write-Host "Data retained at $Root" }
