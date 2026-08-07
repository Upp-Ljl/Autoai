param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^[a-p]{32}$')]
  [string]$ExtensionId
)

$ErrorActionPreference = "Stop"
$programRoot = Join-Path $env:LOCALAPPDATA "SAT2Relay"
$nativeRoot = Join-Path $programRoot "native-host"
$nativeExe = Join-Path $nativeRoot "sat2-relay-native-host.exe"
$manifestPath = Join-Path $nativeRoot "com.sat2.relay.host.json"
$hostName = "com.sat2.relay.host"

if (-not (Test-Path $nativeExe)) {
  throw "Native host executable is missing: $nativeExe. Re-run INSTALL_ON_DEMAND.ps1 first."
}

New-Item -ItemType Directory -Force -Path $nativeRoot | Out-Null
$manifest = [ordered]@{
  name = $hostName
  description = "SAT2 Relay bounded local start helper"
  path = $nativeExe
  type = "stdio"
  allowed_origins = @("chrome-extension://$ExtensionId/")
}
$json = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($manifestPath, $json, (New-Object System.Text.UTF8Encoding($false)))

$registryPaths = @(
  "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName",
  "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$hostName"
)
foreach ($path in $registryPaths) {
  New-Item -Path $path -Force | Out-Null
  Set-Item -Path $path -Value $manifestPath
}

Write-Host "SAT2 Relay Native Messaging host registered."
Write-Host "Extension ID: $ExtensionId"
Write-Host "Manifest: $manifestPath"
Write-Host "Chrome/Edge registry entries updated under HKCU."
