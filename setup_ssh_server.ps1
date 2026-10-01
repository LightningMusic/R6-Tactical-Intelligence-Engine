<#
R6 Tactical Intelligence Engine -- one-time remote-access setup (main PC side)
================================================================================
Run this ONCE, as Administrator, on the main PC (the one this project lives
on). It is not part of build_and_deploy.bat and does not run automatically --
it changes system-level settings (installs a Windows feature, adds a firewall
rule), which is the kind of thing that should only happen when you explicitly
ask for it, not as a side effect of a build.

What it does:
  1. Installs Windows' built-in OpenSSH Server feature, if not already there.
  2. Starts sshd and sets it to start automatically on boot.
  3. Locks sshd down to key-only login -- no passwords accepted over SSH.
  4. Adds your laptop's SSH public key to the right authorized_keys file,
     with the exact file permissions Windows OpenSSH requires (it silently
     refuses to use an authorized_keys file with loose permissions).
  5. Replaces the default "allow from anywhere" firewall rule for SSH with
     one scoped to Tailscale's own address range only (100.64.0.0/10) --
     so port 22 is not reachable from your regular LAN or the internet at
     all, only from other devices on your tailnet.

What it does NOT do: touch anything on your laptop, or anything about the
project itself. Your laptop's setup (generating the key, sharing the USB
folder) is a separate, laptop-side step -- see the instructions delivered
alongside this file.

Usage (from an elevated PowerShell prompt, in this folder):
    .\setup_ssh_server.ps1 -LaptopPublicKey "ssh-ed25519 AAAA...your key here"

Get that key by running, on the laptop:
    ssh-keygen -t ed25519 -f $HOME\.ssh\r6_deploy_key
    Get-Content $HOME\.ssh\r6_deploy_key.pub
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LaptopPublicKey
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "This script must be run as Administrator. Right-click PowerShell -> Run as administrator, then run it again."
        exit 1
    }
}

Assert-Admin

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " R6 Tactical Intelligence Engine -- Remote Access Setup" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

# -- 1. Install OpenSSH Server if missing -------------------------------
Write-Host "`n[1/5] Checking for OpenSSH Server..."
$sshCapability = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Server*" }
if ($sshCapability.State -ne "Installed") {
    Write-Host "      Installing OpenSSH Server (this can take a minute)..."
    Add-WindowsCapability -Online -Name $sshCapability.Name | Out-Null
    Write-Host "      [OK] Installed."
} else {
    Write-Host "      [OK] Already installed."
}

# -- 2. Start and enable sshd --------------------------------------------
Write-Host "`n[2/5] Starting sshd and setting it to auto-start..."
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Write-Host "      [OK] sshd is running and will start automatically on boot."

# -- 3. Key-only auth: disable password login ----------------------------
Write-Host "`n[3/5] Restricting sshd to key-only login (no passwords)..."
$sshdConfigPath = "$env:ProgramData\ssh\sshd_config"
$sshdConfig = Get-Content -LiteralPath $sshdConfigPath

function Set-SshdOption {
    param([string[]]$Lines, [string]$Key, [string]$Value)
    $pattern = "^\s*#?\s*$Key\s+"
    $newLine = "$Key $Value"
    $found = $false
    $result = foreach ($line in $Lines) {
        if ($line -match $pattern) {
            $found = $true
            $newLine
        } else {
            $line
        }
    }
    if (-not $found) { $result += $newLine }
    return $result
}

$sshdConfig = Set-SshdOption -Lines $sshdConfig -Key "PasswordAuthentication" -Value "no"
$sshdConfig = Set-SshdOption -Lines $sshdConfig -Key "PubkeyAuthentication" -Value "yes"
$sshdConfig = Set-SshdOption -Lines $sshdConfig -Key "KbdInteractiveAuthentication" -Value "no"

# A remote build over this connection includes long, quiet stretches --
# PyInstaller compiling with nothing printed for a while, a large
# server_data folder being preserved/copied -- with no traffic on the SSH
# session at all during them. An idle TCP connection like that is exactly
# what a NAT gateway, a stateful firewall, or a flaky Wi-Fi link is most
# likely to silently drop, which shows up on the client side as
# "client_loop: send disconnect: Connection reset" and exit code 255,
# mid-build, for no reason visible in this project's own code at all.
# ClientAliveInterval makes sshd itself send a keepalive probe every 30
# seconds during any quiet period; ClientAliveCountMax allows 6 missed
# probes (3 minutes) before actually giving up. This keeps the underlying
# connection visibly "alive" to anything in between that might otherwise
# time it out, without weakening any of the auth settings above.
$sshdConfig = Set-SshdOption -Lines $sshdConfig -Key "ClientAliveInterval" -Value "30"
$sshdConfig = Set-SshdOption -Lines $sshdConfig -Key "ClientAliveCountMax" -Value "6"
Set-Content -LiteralPath $sshdConfigPath -Value $sshdConfig -Encoding ASCII
Write-Host "      [OK] Password login disabled; key-only from here on."
Write-Host "      [OK] Keepalive enabled (30s interval, up to 3min grace) so a long,"
Write-Host "           quiet build step is less likely to get the connection dropped."

# -- 4. Install the laptop's public key -----------------------------------
Write-Host "`n[4/5] Installing your laptop's SSH key..."

# Windows OpenSSH treats accounts in the Administrators group specially:
# their key must go in administrators_authorized_keys (a single shared
# file under ProgramData), not the per-user .ssh\authorized_keys -- the
# latter is silently ignored for admin accounts. Handle both cases.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$runningAsAdminAccount = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# Re-running this script (e.g. just to pick up the keepalive setting above,
# or after re-imaging the laptop) used to blindly Add-Content the key again
# every time, piling up duplicate identical lines in the file. Harmless to
# SSH itself (it just tries each line), but messy, and easy to avoid: skip
# the add if that exact key is already present.
function Add-KeyIfMissing {
    param([string]$Path, [string]$Key)
    $existing = @()
    if (Test-Path -LiteralPath $Path) {
        $existing = Get-Content -LiteralPath $Path
    }
    if ($existing -contains $Key) {
        return $false
    }
    Add-Content -Path $Path -Value $Key
    return $true
}

if ($runningAsAdminAccount) {
    $authKeysPath = "$env:ProgramData\ssh\administrators_authorized_keys"
    $added = Add-KeyIfMissing -Path $authKeysPath -Key $LaptopPublicKey
    # Windows OpenSSH requires this file to be readable only by
    # Administrators and SYSTEM -- it refuses to use it otherwise.
    icacls $authKeysPath /inheritance:r | Out-Null
    icacls $authKeysPath /grant "Administrators:F" | Out-Null
    icacls $authKeysPath /grant "SYSTEM:F" | Out-Null
    if ($added) {
        Write-Host "      [OK] Key added to administrators_authorized_keys."
    } else {
        Write-Host "      [OK] Key already present in administrators_authorized_keys -- skipped."
    }
} else {
    $sshDir = "$env:USERPROFILE\.ssh"
    if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null }
    $authKeysPath = "$sshDir\authorized_keys"
    $added = Add-KeyIfMissing -Path $authKeysPath -Key $LaptopPublicKey
    # Same idea for a normal user account: OpenSSH requires this file to
    # not be readable/writable by anyone but the owner and SYSTEM.
    icacls $authKeysPath /inheritance:r | Out-Null
    icacls $authKeysPath /grant "${env:USERNAME}:F" | Out-Null
    icacls $authKeysPath /grant "SYSTEM:F" | Out-Null
    if ($added) {
        Write-Host "      [OK] Key added to $authKeysPath."
    } else {
        Write-Host "      [OK] Key already present in $authKeysPath -- skipped."
    }
    Write-Host "      (If SSH still asks for a password after this, Windows"
    Write-Host "       OpenSSH is picky about the .ssh folder's own permissions"
    Write-Host "       too, not just the file's -- run:"
    Write-Host "         icacls `"$sshDir`" /inheritance:r"
    Write-Host "         icacls `"$sshDir`" /grant `"${env:USERNAME}:F`""
    Write-Host "       and try again.)"
}

# -- 5. Firewall: scope SSH to the Tailscale range only --------------------
Write-Host "`n[5/5] Restricting the SSH firewall rule to Tailscale only..."
# Tailscale assigns every device an IP in 100.64.0.0/10 (the shared CGNAT
# range it uses for tailnets). Scoping the firewall rule to that range
# means port 22 simply doesn't exist as far as your regular LAN or the
# internet are concerned -- only other devices on your tailnet can even
# attempt a connection, before SSH's own key-only auth is ever reached.
$tailscaleRange = "100.64.0.0/10"
$existingRule = Get-NetFirewallRule -DisplayName "OpenSSH SSH Server (sshd)" -ErrorAction SilentlyContinue
if ($existingRule) {
    Set-NetFirewallRule -DisplayName "OpenSSH SSH Server (sshd)" -RemoteAddress $tailscaleRange
    Write-Host "      [OK] Existing SSH firewall rule scoped to $tailscaleRange."
} else {
    New-NetFirewallRule -Name "sshd-tailscale-only" -DisplayName "OpenSSH SSH Server (Tailscale only)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 `
        -RemoteAddress $tailscaleRange | Out-Null
    Write-Host "      [OK] Created a new SSH firewall rule scoped to $tailscaleRange."
}

Write-Host "`nRestarting sshd to pick up the config changes..."
Restart-Service sshd
Write-Host "[OK] sshd restarted."

Write-Host "`n==============================================================" -ForegroundColor Green
Write-Host " Done. From your laptop, this should now work:" -ForegroundColor Green
Write-Host "   ssh `"$env:USERNAME@<this-pc-tailscale-name>`"" -ForegroundColor Green
if ($env:USERNAME -match '\s') {
    Write-Host " Your Windows username has a space in it ('$env:USERNAME') -- the" -ForegroundColor Yellow
    Write-Host " quotes above around the whole user@host part are REQUIRED, not" -ForegroundColor Yellow
    Write-Host " optional, or SSH will misread it as two separate arguments." -ForegroundColor Yellow
}
Write-Host " Find <this-pc-tailscale-name> by running 'tailscale status' on" -ForegroundColor Green
Write-Host " either machine." -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
