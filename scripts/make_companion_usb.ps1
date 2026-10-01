<#
.SYNOPSIS
    Puts R6 Companion on a USB stick for a teammate: the companion app, a
    portable OBS for it to drive, and ffmpeg -- all in <Drive>\R6Companion.

.DESCRIPTION
    Safe to re-run to update a stick: the app and OBS program files are
    refreshed, but the companion's own settings (data\), its OBS settings
    (OBS-Studio\config) and any recordings are left alone. Refuses to touch
    the main R6_PROJ stick.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\make_companion_usb.ps1 -Drive H:
#>
param(
    [Parameter(Mandatory = $true)][string] $Drive,
    # The teammate's in-game name, set up now so they never see a setup screen.
    [string] $Username = "",
    [string] $ObsSource = "",
    [string] $RepoRoot = ""
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5 doesn't have $PSScriptRoot yet while evaluating
# parameter defaults, so the repo root is worked out here instead.
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$Drive = $Drive.TrimEnd('\').TrimEnd(':') + ':'

$vol = Get-Volume -DriveLetter $Drive.TrimEnd(':') -ErrorAction Stop
if ($vol.FileSystemLabel -eq 'R6_PROJ') { throw "$Drive is the main R6_PROJ stick -- refusing to make it a companion stick." }

$exe = Join-Path $RepoRoot 'dist\R6Companion.exe'
if (-not (Test-Path $exe)) { throw "Build R6Companion.exe first (pyinstaller R6Companion.spec)." }

if (-not $ObsSource) {
    foreach ($c in @('F:\OBS-Studio', (Join-Path $env:ProgramFiles 'obs-studio'))) {
        if (Test-Path (Join-Path $c 'bin\64bit\obs64.exe')) { $ObsSource = $c; break }
    }
}
if (-not $ObsSource) { throw "No OBS to copy -- pass -ObsSource <folder containing bin\64bit\obs64.exe>." }

$dest = Join-Path $Drive 'R6Companion'
foreach ($d in @($dest, "$dest\recordings", "$dest\data")) { New-Item -ItemType Directory -Force $d | Out-Null }

Write-Host "Copying OBS from $ObsSource (program files only; its settings on the stick are kept)..."
robocopy $ObsSource "$dest\OBS-Studio" /E /XD config /XF portable_mode.txt /NFL /NDL /NJH /NJS /NP /R:2 /W:2 | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying OBS failed (robocopy $LASTEXITCODE)." }

Copy-Item $exe "$dest\R6Companion.exe" -Force
if (-not (Test-Path "$dest\ffmpeg.exe")) { Copy-Item (Join-Path $RepoRoot 'ffmpeg.exe') "$dest\ffmpeg.exe" }
Copy-Item (Join-Path $RepoRoot 'companion\START_HERE.txt') "$dest\START_HERE.txt" -Force

if ($Username) {
    $settingsPath = "$dest\data\settings.json"
    $s = @{}
    if (Test-Path $settingsPath) {
        (Get-Content $settingsPath -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $s[$_.Name] = $_.Value }
    }
    $s['username'] = $Username
    $s | ConvertTo-Json | Set-Content $settingsPath -Encoding utf8
    Write-Host "[OK] Set up for $Username."
}

$free = [math]::Round((Get-Volume -DriveLetter $Drive.TrimEnd(':')).SizeRemaining / 1GB, 1)
Write-Host "[OK] R6 Companion ready on $Drive (R6Companion\R6Companion.exe) -- $free GB free."
