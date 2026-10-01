param(
    [Parameter(Mandatory = $true)]
    [string]$UsbDest,

    [Parameter(Mandatory = $true)]
    [string]$ModelSrc
)

$ErrorActionPreference = "Stop"

# NOT $MyInvocation.MyCommand.Path here (that would resolve to wherever this
# script FILE itself lives, e.g. build_scripts\, once it was moved out of
# the project root). The project root is what this script actually needs to
# find $ModelSrc relative to, and build_and_deploy.bat already guarantees
# its own current directory IS the project root (it does `cd /d "%~dp0"` as
# one of its first lines, and PowerShell's -File invocation inherits the
# caller's current directory), so (Get-Location).Path is the reliable way
# to get it regardless of which folder this script file happens to live in.
$projectRoot = (Get-Location).Path
$warnings = $false

function Copy-OptionalFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $label = Split-Path -Leaf $Source
    $sourcePath = Join-Path $projectRoot $Source

    if (-not (Test-Path -LiteralPath $sourcePath)) {
        Write-Output "[WARN] $label not found at $sourcePath"
        $script:warnings = $true
        return
    }

    try {
        Copy-Item -LiteralPath $sourcePath -Destination $Destination -Force
        Write-Output "[OK] $label synced."
    }
    catch {
        Write-Output "[WARN] Failed to copy $label to $Destination"
        Write-Output "[WARN] $($_.Exception.Message)"
        $script:warnings = $true
    }
}

# settings.json and matches.db are NEVER synced here, on purpose. They
# are the user's live, saved settings and match history -- created and
# owned entirely by whichever copy of the app is actually running (the
# USB build), not by this machine's own dev/source checkout. This script
# used to copy this project's own data\settings.json and data\matches.db
# onto the USB with -Force on every single deploy, which meant every
# successful build_and_deploy run silently overwrote the USB's real,
# accumulated match history and settings with whatever stale/local data
# happened to be sitting in this checkout's own data\ folder (e.g. from
# running the app from source here for local testing). That is exactly
# what wiped a USB's saved matches -- do not add these back. The app
# itself creates a fresh settings.json (from built-in defaults) and a
# fresh matches.db (from schema.sql) the moment it runs somewhere that
# doesn't have them yet, so nothing here needs to "seed" them, and
# build_and_deploy.bat's own /XD data exclusion on the client robocopy
# above already exists specifically to keep this folder untouched.

$modelSourceDir = Join-Path $projectRoot $ModelSrc
$modelDestDir = Join-Path $UsbDest "data\models"

if (Test-Path -LiteralPath $modelSourceDir) {
    Get-ChildItem -LiteralPath $modelSourceDir -File | Where-Object {
        $_.Extension -in @(".gguf", ".pt")
    } | ForEach-Object {
        Copy-OptionalFile -Source (Join-Path $ModelSrc $_.Name) -Destination (Join-Path $modelDestDir $_.Name)
    }
} else {
    Write-Output "[WARN] Model source directory not found at $modelSourceDir"
    $warnings = $true
}

if ($warnings) {
    exit 2
}

exit 0
