param(
    [Parameter(Mandatory = $true)]
    [string]$UsbDest,

    [Parameter(Mandatory = $true)]
    [string]$ModelSrc
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
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

Copy-OptionalFile -Source "data\settings.json" -Destination (Join-Path $UsbDest "data\settings.json")
Copy-OptionalFile -Source "data\matches.db" -Destination (Join-Path $UsbDest "data\matches.db")

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
