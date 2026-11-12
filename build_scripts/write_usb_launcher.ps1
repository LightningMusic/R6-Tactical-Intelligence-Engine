<#
.SYNOPSIS
    Writes a one-click launcher .cmd at the root of a USB stick so the user
    doesn't have to open the app's folder to start it.

.DESCRIPTION
    The launcher uses %~dp0 (its own folder = the USB root), so it works on
    any PC and any drive letter -- the stick mounts as F: here and something
    else on the practice PC, and the same file just works. It is written with
    CRLF line endings; a .cmd with LF-only endings gets mangled by cmd.exe.

    Note: Windows deliberately disables true autorun-on-insert for USB sticks
    (security), so the closest we can get is this one-double-click launcher
    sitting right at the root.

.EXAMPLE
    write_usb_launcher.ps1 -Root "F:\" -ExeRel "R6Analyzer\R6Analyzer.exe" -Name "Start R6Analyzer.cmd"
#>
param(
    [Parameter(Mandatory = $true)][string] $Root,
    [Parameter(Mandatory = $true)][string] $ExeRel,
    [Parameter(Mandatory = $true)][string] $Name
)
$ErrorActionPreference = 'Stop'
# Build the path by explicit concatenation rather than Join-Path, and strip any
# trailing backslash/space off $Root first. A bare drive ("G:") and a drive with
# a trailing slash ("G:\") both resolve to "G:\<Name>" this way. (Join-Path "G:"
# is drive-relative and ambiguous; and the caller must pass the drive WITHOUT a
# trailing backslash -- "G:\" inside quotes becomes an escaped quote on the
# Windows command line and eats the next argument.)
$path = ($Root.TrimEnd('\', ' ')) + '\' + $Name
$lines = @(
    '@echo off',
    'rem One-click launcher at the USB root -- no need to open the app folder first.',
    'rem Works on any PC / drive letter: %~dp0 is this file''s own folder (the USB root).',
    ('start "" "%~dp0' + $ExeRel + '"')
)
# CRLF + ASCII: a .cmd with LF-only endings is run mangled by cmd.exe.
[System.IO.File]::WriteAllText($path, (($lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)
Write-Host "[OK] Wrote root launcher: $path"
