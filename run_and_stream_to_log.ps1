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

$cmdArgs = @(
    "/d",
    "/c",
    ('call "{0}" 2>&1' -f $scriptFullPath)
)

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
