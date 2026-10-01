param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,

    [Parameter(Mandatory = $true)]
    [string]$LogFile,

    [Parameter(Mandatory = $true)]
    [string]$StepName
)

$ErrorActionPreference = "Stop"

$scriptFullPath = [System.IO.Path]::GetFullPath($ScriptPath)
$logFullPath = [System.IO.Path]::GetFullPath($LogFile)
$tempName = "$($StepName.ToLowerInvariant())_$([guid]::NewGuid().ToString('N')).tmp.log"
$tempOutput = [System.IO.Path]::Combine(
    [System.IO.Path]::GetDirectoryName($logFullPath),
    $tempName
)

Add-Content -Path $logFullPath -Value ""
Add-Content -Path $logFullPath -Value "============================================================"
Add-Content -Path $logFullPath -Value ("[{0}] Starting {1}" -f $StepName, [System.IO.Path]::GetFileName($scriptFullPath))
Add-Content -Path $logFullPath -Value "============================================================"

# Deliberately NOT "call \"<path>\" 2>&1" here. This project's own path
# has a space in it (Python Projects), and cmd.exe's /C argument parsing
# has a well-known gotcha with a quoted path followed by trailing
# arguments: without wrapping the *entire* remainder of the command line
# in one more outer pair of quotes, cmd's handling of it is unreliable.
# This surfaced as build_and_deploy.bat's own internal `call :subroutine`
# calls (e.g. :require_file) working the first time and then failing
# with "The system cannot find the batch label specified" on the very
# next call in the same run -- the signature of this exact quoting
# problem corrupting cmd's tracking of the running batch file once
# nested this way. `call` is also unnecessary here in the first place:
# this is a brand-new cmd.exe instance whose only job is to run this one
# command line and exit, not a call from within an already-running batch
# script, so plain /c is the correct, idiomatic form.
#
# The fix is the standard idiom for "cmd /c a quoted path with spaces
# plus extra arguments": wrap the whole thing (path + args) in one more
# pair of quotes, and pass it as a single string (not an array) so
# PowerShell doesn't re-quote it a second time itself.
$cmdArgs = '/d /c ""{0}" 2>&1"' -f $scriptFullPath

$proc = Start-Process -FilePath "cmd.exe" `
    -WorkingDirectory ([System.IO.Path]::GetDirectoryName($scriptFullPath)) `
    -ArgumentList $cmdArgs `
    -RedirectStandardOutput $tempOutput `
    -NoNewWindow `
    -PassThru

$position = 0L

try {
    while (-not $proc.HasExited -or ((Test-Path -LiteralPath $tempOutput) -and ((Get-Item -LiteralPath $tempOutput).Length -gt $position))) {
        if (Test-Path -LiteralPath $tempOutput) {
            $fs = $null
            $sr = $null
            try {
                $fs = [System.IO.File]::Open($tempOutput, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                $fs.Seek($position, [System.IO.SeekOrigin]::Begin) | Out-Null
                $sr = New-Object System.IO.StreamReader($fs)
                $chunk = $sr.ReadToEnd()
                $position = $fs.Position
            }
            finally {
                if ($sr) { $sr.Dispose() }
                elseif ($fs) { $fs.Dispose() }
            }

            if ($chunk) {
                [Console]::Write($chunk)
                Add-Content -Path $logFullPath -Value $chunk
            }
        }

        Start-Sleep -Milliseconds 250
    }

    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
}
finally {
    if (Test-Path -LiteralPath $tempOutput) {
        Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    }
}

Add-Content -Path $logFullPath -Value ("[{0}] Exit code: {1}" -f $StepName, $exitCode)
exit $exitCode
