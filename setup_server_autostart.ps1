<#
.SYNOPSIS
    Registers a Scheduled Task that starts R6Server.exe automatically at logon.

.DESCRIPTION
    Run this once. After that the remote server comes up on its own every time
    you log in, so a laptop out in the field always has something to upload to.

    An idle server is close to free -- the worker does a SQLite query every two
    seconds and sleeps. The expensive part is the analysis stage (Whisper +
    Ollama), which is gated separately by toggle_analysis.bat. Auto-start and
    "don't eat my machine while I game" are not in conflict: leave analysis
    paused while gaming and the server still quietly accepts uploads.

    build_and_deploy.bat stops the server on its own before rebuilding and
    starts it again afterwards, so this task does not need to be disabled to
    build.

.PARAMETER ServerDir
    Folder holding R6Server.exe. Defaults to dist\R6Server next to this script.

.PARAMETER StartNow
    Also start the task immediately. Left off by default -- registering the
    task is free, but starting the server is a thing you may want to time
    yourself.

.PARAMETER Remove
    Unregister the task instead of creating it.

.EXAMPLE
    .\setup_server_autostart.ps1
.EXAMPLE
    .\setup_server_autostart.ps1 -StartNow
.EXAMPLE
    .\setup_server_autostart.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string] $ServerDir,
    [switch] $StartNow,
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'
$TaskName = 'R6Analyzer Remote Server'
$WatchdogTaskName = 'R6Analyzer Server Watchdog'

if ($Remove) {
    foreach ($name in $TaskName, $WatchdogTaskName) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed scheduled task: $name" -ForegroundColor Green
        }
    }
    Write-Host "The server will no longer start at logon or be restarted. Any running instance is untouched."
    exit 0
}

if (-not $ServerDir) {
    $ServerDir = Join-Path $PSScriptRoot 'dist\R6Server'
}
$resolved = Resolve-Path -LiteralPath $ServerDir -ErrorAction SilentlyContinue
if ($resolved) { $ServerDir = $resolved.Path } else { $ServerDir = $null }
if (-not $ServerDir) {
    Write-Host "[ERROR] Server folder not found. Build it first with build_and_deploy.bat," -ForegroundColor Red
    Write-Host "        or pass the folder explicitly:  .\setup_server_autostart.ps1 -ServerDir H:\R6Server" -ForegroundColor Red
    exit 1
}

$Exe = Join-Path $ServerDir 'R6Server.exe'
if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Host "[ERROR] R6Server.exe not found in: $ServerDir" -ForegroundColor Red
    exit 1
}

# The working directory is load-bearing, not cosmetic. server/config.py
# resolves its data directory from the relative path "./server_data", so a
# task launched with the default working directory (C:\Windows\System32)
# would create a BRAND NEW server_data there, self-provision a DIFFERENT API
# token, and reject every client that was configured against the real one --
# which looks exactly like "invalid or expired API token" and is miserable to
# trace back to a scheduled task property.
$action = New-ScheduledTaskAction -Execute $Exe -WorkingDirectory $ServerDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

# Interactive, not SYSTEM: the console window is this app's entire UI (see
# R6Server.spec), and it has to land on the desktop where it can be read.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$registerMain = $true
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Replacing existing task '$TaskName'..." -ForegroundColor Yellow
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    } catch {
        # A task first created from an elevated prompt can only be replaced
        # from one; the existing task still works, so keep it.
        Write-Host "  Could not replace it without admin rights -- keeping the existing one." -ForegroundColor Yellow
        $registerMain = $false
    }
}

if ($registerMain) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description 'Starts the R6Analyzer headless remote server at logon so field clients always have an upload target. Analysis load is controlled separately by toggle_analysis.bat.' | Out-Null
}

# Watchdog: every 5 minutes, start the server if it isn't running (a crash
# of an already-running process is not something the task above recovers
# from). conhost --headless keeps the check from flashing a window.
$watchdogScript = Join-Path $PSScriptRoot 'server_watchdog.ps1'
$watchAction = New-ScheduledTaskAction -Execute 'conhost.exe' `
    -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`" -ServerDir `"$ServerDir`""
$watchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$watchSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
if (Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $WatchdogTaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $WatchdogTaskName -Action $watchAction -Trigger $watchTrigger `
    -Principal $principal -Settings $watchSettings `
    -Description 'Every 5 minutes: restarts R6Server.exe if it has stopped. Leaves it alone while a build runs or after toggle_server.bat off.' | Out-Null

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ("  {0}: $TaskName" -f $(if ($registerMain) { 'Registered' } else { 'Kept     ' })) -ForegroundColor Green
Write-Host "  Registered: $WatchdogTaskName (checks every 5 min)" -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host "  Runs      : $Exe"
Write-Host "  Working in: $ServerDir"
Write-Host "  Trigger   : at logon ($env:USERDOMAIN\$env:USERNAME), restarts up to 3x on failure"
Write-Host ''
Write-Host '  The server console window will appear at logon -- that window IS'
Write-Host '  the server UI (it prints the API token and request activity).'
Write-Host '  Minimize it; closing it stops the server.'
Write-Host ''
Write-Host '  To control the heavy analysis stage, use toggle_analysis.bat.'
Write-Host '  To undo this:  .\setup_server_autostart.ps1 -Remove'
Write-Host ''

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host '  Started now (as requested).' -ForegroundColor Green
} else {
    Write-Host '  Not started now. It will come up at your next logon, or run:' -ForegroundColor Yellow
    Write-Host "      Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
}
Write-Host ''
