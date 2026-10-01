# Hard identity check for build_and_deploy.bat before it lets anything
# write to a "USB drive" it thinks it found.
#
# This exists because of a real close call: the auto-detected/typed-in
# drive letter that build_and_deploy.bat resolves is just a letter --
# "D" means something completely different depending on context (a
# local internal/external disk, a different USB stick, or an RDP
# client-redirected drive shown in Explorer as "D on <laptop-name>").
# On a machine with a large local drive sharing a letter with the
# redirected USB (e.g. a multi-terabyte external backup drive), typing
# the wrong one and letting robocopy /PURGE loose on it would be a
# disaster, not just an inconvenience.
#
# Win32_LogicalDisk (NOT Get-Volume) on purpose: Get-Volume only sees
# genuine local volumes via the Storage Management API and is blind to
# RDP client-drive-redirected drives (verified earlier in this
# project -- they show up as network-type drives under the hood, e.g.
# \\tsclient\d, even though File Explorer displays them as "<letter> on
# <computer>"). Win32_LogicalDisk sees every drive type -- local,
# removable, and redirected/network -- so it's the one check here that
# works the same way whether the drive is plugged in locally or reached
# over RDP.
param(
    [Parameter(Mandatory = $true)]
    [string]$DriveLetter,   # single letter, no colon, e.g. "D"

    [Parameter(Mandatory = $true)]
    [string]$ExpectedLabel,

    [Parameter(Mandatory = $false)]
    [int]$MaxSizeGb = 512   # generous headroom for a growing project USB
                            # stick, while still safely excluding a
                            # multi-terabyte external/backup drive that
                            # happens to share a letter.
)

$ErrorActionPreference = "Stop"

$disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='${DriveLetter}:'" -ErrorAction SilentlyContinue

if (-not $disk) {
    Write-Output "MISSING|(none)|0"
    exit 1
}

# Output is pipe-delimited and parsed by cmd's FOR /F on the batch side,
# which collapses consecutive delimiters instead of preserving empty
# fields between them -- so an empty label has to become a visible
# placeholder here, not "", or the field count shifts and the wrong
# value lands in the wrong variable.
$label = [string]$disk.VolumeName
if ([string]::IsNullOrEmpty($label)) {
    $label = "(unlabeled)"
}
$sizeGb = 0
if ($disk.Size) {
    $sizeGb = [math]::Round([double]$disk.Size / 1GB, 1)
}

if ($label -ne $ExpectedLabel) {
    Write-Output "LABEL_MISMATCH|$label|$sizeGb"
    exit 1
}

if ($sizeGb -gt $MaxSizeGb) {
    Write-Output "TOO_LARGE|$label|$sizeGb"
    exit 1
}

Write-Output "OK|$label|$sizeGb"
exit 0
