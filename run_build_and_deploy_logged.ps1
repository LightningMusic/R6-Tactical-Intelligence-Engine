$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "build_and_deploy_$timestamp.log"

Write-Host "Writing log to: $logFile"
Write-Host ""

$cmdText = 'cd /d "{0}" && call build_and_deploy.bat > "{1}" 2>&1' -f $projectRoot, $logFile
$process = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmdText -PassThru

while (-not (Test-Path $logFile)) {
    if ($process.HasExited) { break }
    Start-Sleep -Milliseconds 200
}

if (Test-Path $logFile) {
    Get-Content -Path $logFile -Wait
} else {
    $process.WaitForExit()
}

$process.WaitForExit()
$exitCode = $process.ExitCode

Write-Host ""
Write-Host "Log saved to: $logFile"
Write-Host "Exit code: $exitCode"
Write-Host ""
Read-Host "Press Enter to close"
exit $exitCode
