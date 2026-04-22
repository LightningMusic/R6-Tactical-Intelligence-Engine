$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "setup_$timestamp.log"

Write-Host "Writing log to: $logFile"
Write-Host ""

Push-Location $projectRoot
try {
    Start-Transcript -Path $logFile -Force | Out-Null
    & cmd /c "setup.bat"
    $exitCode = if ($LASTEXITCODE -ne $null) { $LASTEXITCODE } else { 0 }
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
    }
    Pop-Location
}

Write-Host ""
Write-Host "Log saved to: $logFile"
Write-Host "Exit code: $exitCode"
Write-Host ""
Read-Host "Press Enter to close"
exit $exitCode
