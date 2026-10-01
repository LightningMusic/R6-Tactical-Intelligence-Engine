<#
.SYNOPSIS
    Restarts R6Server.exe if it is not running. Run every few minutes by the
    "R6Analyzer Server Watchdog" scheduled task (see setup_server_autostart.ps1).

.DESCRIPTION
    Task Scheduler's own restart-on-failure only covers the process the task
    itself launched, and not reliably a crash of a running process. A server
    started by build_and_deploy.bat crashed on 2026-09-24 and stayed down for
    55 minutes with nothing noticing. This fills that gap.

    It stays out of the way when:
      - server_data\server_stopped.flag exists (you stopped it on purpose;
        toggle_server.bat creates/removes it), or
      - build_and_deploy.bat / PyInstaller is running (the build stops the
        server on purpose and restarts it itself).

    It only logs when it acts, to server_data\logs\watchdog.log.
#>
param([string] $ServerDir = (Join-Path $PSScriptRoot 'dist\R6Server'))

$ErrorActionPreference = 'SilentlyContinue'
$dataDir = Join-Path $ServerDir 'server_data'
$logFile = Join-Path $dataDir 'logs\watchdog.log'

function Write-Log([string] $msg) {
    New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null
    Add-Content -Path $logFile -Value ('{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $msg)
}

if (Test-Path -LiteralPath (Join-Path $dataDir 'server_stopped.flag')) { exit 0 }
if (Get-Process -Name 'R6Server' -ErrorAction SilentlyContinue) { exit 0 }

$building = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'build_and_deploy|pyinstaller' }
if ($building) {
    Write-Log 'Server is down but a build is running -- leaving it to the build script.'
    exit 0
}

$exe = Join-Path $ServerDir 'R6Server.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Log "Server is down and $exe is missing -- nothing to start."
    exit 1
}

# Working directory is load-bearing: server_data is resolved relative to it.
Start-Process -FilePath $exe -WorkingDirectory $ServerDir
Write-Log 'R6Server.exe was not running -- started it.'
