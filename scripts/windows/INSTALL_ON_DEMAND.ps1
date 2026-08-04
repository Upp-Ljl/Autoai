param(
  [string]$DataRoot = (Join-Path $env:LOCALAPPDATA "SAT2RelayData"),
  [switch]$SkipTokenPrompt,
  [switch]$NoDesktopShortcut
)

$ErrorActionPreference = "Stop"
$programRoot = Join-Path $env:LOCALAPPDATA "SAT2Relay"
$venv = Join-Path $programRoot "venv"
$config = Join-Path $programRoot "config.yml"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Resolve-Path (Join-Path $scriptRoot "..\..")
$daemonSource = Join-Path $repositoryRoot "daemon"
$extensionSource = Join-Path $repositoryRoot "extension"
$tools = Join-Path $programRoot "on-demand"

if (-not (Test-Path $daemonSource) -or -not (Test-Path $extensionSource)) {
  throw "Run this script from a complete autoAI checkout. daemon/ or extension/ is missing."
}

# On-demand mode must never retain a legacy logon task.
foreach ($taskName in @("SAT2 Relay", "SAT2 Relay 2")) {
  Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.ProcessId -ne $PID -and (
      ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($programRoot, [StringComparison]::OrdinalIgnoreCase)) -or
      ($_.CommandLine -and $_.CommandLine.IndexOf($programRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    )
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Force -Path $programRoot, $DataRoot, $tools | Out-Null
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "Python launcher 'py' was not found. Install Python 3.11 or later first."
}
& py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or later is required." }

if (-not (Test-Path $venv)) { & py -3 -m venv $venv }
$python = Join-Path $venv "Scripts\python.exe"
$relay = Join-Path $venv "Scripts\sat2-relay.exe"
& $python -m pip install --disable-pip-version-check --upgrade $daemonSource
if ($LASTEXITCODE -ne 0) { throw "Relay package installation failed." }
if (-not (Test-Path $config)) { & $relay --config $config init }
if ($LASTEXITCODE -ne 0) { throw "Relay configuration initialization failed." }

# Keep mutable data outside the program directory. This only changes generated
# storage entries, preserving existing repository and credential settings.
$escapedRoot = $DataRoot.Replace("'", "''")
$configText = Get-Content -Raw $config
$configText = [regex]::Replace($configText, '(?m)^  database:.*$', "  database: '$escapedRoot/state.sqlite3'")
$configText = [regex]::Replace($configText, '(?m)^  log:.*$', "  log: '$escapedRoot/logs/sat2-relay.jsonl'")
$configText = [regex]::Replace($configText, '(?m)^  lock:.*$', "  lock: '$escapedRoot/state/sat2-relay.lock'")
Set-Content -Path $config -Value $configText -Encoding utf8

$extensionTarget = Join-Path $programRoot "extension"
if (Test-Path $extensionTarget) { Remove-Item -Recurse -Force $extensionTarget }
Copy-Item -Recurse -Force $extensionSource $extensionTarget
Copy-Item -Force (Join-Path $scriptRoot "START_OR_REPAIR.ps1"), (Join-Path $scriptRoot "STOP_RELAY.ps1"), (Join-Path $scriptRoot "START_OR_REPAIR.cmd"), (Join-Path $scriptRoot "STOP_RELAY.cmd") -Destination $tools

if (-not $SkipTokenPrompt) {
  $answer = Read-Host "Store a GitHub fine-grained PAT now? [Y/n]"
  if ($answer -notmatch '^[Nn]') {
    $secure = Read-Host "GitHub PAT (Contents read; Issues and Pull requests write when enabled)" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
      $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
      $env:SAT2_INSTALL_GITHUB_TOKEN = $plain
      $env:SAT2_INSTALL_CONFIG = $config
      & $python -c "import os; from pathlib import Path; from sat2_relay.config import load_local_config; c=load_local_config(Path(os.environ['SAT2_INSTALL_CONFIG'])); c.credential_store.set('github_token', os.environ['SAT2_INSTALL_GITHUB_TOKEN'])"
    } finally {
      if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
      Remove-Item Env:SAT2_INSTALL_GITHUB_TOKEN -ErrorAction SilentlyContinue
      Remove-Item Env:SAT2_INSTALL_CONFIG -ErrorAction SilentlyContinue
    }
  }
}

if (-not $NoDesktopShortcut) {
  $desktop = [Environment]::GetFolderPath("Desktop")
  $shell = New-Object -ComObject WScript.Shell
  foreach ($shortcut in @(
    @{ Name = "SAT2 Relay - Start or Repair.lnk"; Target = "START_OR_REPAIR.cmd"; Description = "Start or repair SAT2 Relay on demand" },
    @{ Name = "SAT2 Relay - Stop.lnk"; Target = "STOP_RELAY.cmd"; Description = "Stop SAT2 Relay" }
  )) {
    $link = $shell.CreateShortcut((Join-Path $desktop $shortcut.Name))
    $link.TargetPath = Join-Path $tools $shortcut.Target
    $link.WorkingDirectory = $tools
    $link.Description = $shortcut.Description
    $link.Save()
  }
}

& (Join-Path $tools "START_OR_REPAIR.ps1")
$apiToken = (& $python -c "from pathlib import Path; from sat2_relay.config import load_local_config; print(load_local_config(Path(r'$config')).api_token)").Trim()
Write-Host ""
Write-Host "On-demand installation complete. No login scheduled task is registered."
Write-Host "Extension: $(Join-Path $programRoot 'extension')"
Write-Host "Local API token (paste once into extension settings): $apiToken"
