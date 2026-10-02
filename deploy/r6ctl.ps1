# r6ctl -- controls the R6 server stack: Docker inside the sealed "R6Host" WSL
# distro. Run it through r6ctl.bat:   r6ctl help
param(
    [Parameter(Position = 0)][string]$Command = 'help',
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest
)

# Native stderr must not turn into terminating errors on Windows PowerShell 5.1;
# every external call is checked through its exit code instead.
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Deploy   = $PSScriptRoot
$Root     = Split-Path -Parent $Deploy
$Distro   = 'R6Host'
$Src      = '/opt/r6/src'
$EnvFile  = Join-Path $Deploy '.env'
$TaskName = 'R6 Server (WSL keep-alive)'
$Volume   = 'r6_data'

function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }
function Fail([string]$Text) { Write-Host "ERROR: $Text" -ForegroundColor Red; exit 1 }

# wsl.exe writes its own messages as UTF-16; strip the NULs that leaves behind.
function Strip-Nul($Line) { ([string]$Line -replace "`0", '').TrimEnd() }

# Runs a command in the distro and returns its output lines.
function Wsl { & wsl.exe -d $Distro -u root @args 2>&1 | ForEach-Object { Strip-Nul $_ } }
# Same, but prints as it goes (long-running commands).
function Run { & wsl.exe -d $Distro -u root @args 2>&1 | ForEach-Object { Write-Host (Strip-Nul $_) } }
function Compose { Run --cd "$Src/deploy" --exec docker compose @args }
function ComposeCapture { Wsl --cd "$Src/deploy" --exec docker compose @args }

function Quote([string]$Arg) { '"' + ($Arg -replace '"', '\"') + '"' }

# tar | wsl, piped by cmd.exe: a PowerShell pipeline (or a .NET stdin writer,
# which prepends a BOM on Windows PowerShell 5.1) would corrupt the bytes.
function Send-Tar([string[]]$ArchiveArgs, [string]$RemoteCommand) {
    $tar = Quote (Join-Path $env:SystemRoot 'System32\tar.exe')
    $line = "$tar " + (($ArchiveArgs | ForEach-Object { Quote $_ }) -join ' ') +
            " | wsl.exe -d $Distro -u root --exec sh -c " + (Quote $RemoteCommand)
    $script = Join-Path ([IO.Path]::GetTempPath()) ('r6ctl-' + [guid]::NewGuid().ToString('N') + '.cmd')
    [IO.File]::WriteAllText($script, "@echo off`r`n$line`r`nexit /b %errorlevel%`r`n", [Text.Encoding]::ASCII)
    try { & cmd.exe /d /c $script } finally { Remove-Item $script -Force -ErrorAction SilentlyContinue }
    if ($LASTEXITCODE -ne 0) { Fail "Copying into WSL failed (exit $LASTEXITCODE)." }
}

function Get-EnvValue([string]$Key) {
    if (-not (Test-Path $EnvFile)) { return '' }
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match "^\s*$Key\s*=\s*(.*)$") { return $Matches[1].Trim() }
    }
    return ''
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path $EnvFile) { $lines = @(Get-Content $EnvFile) }
    $found = $false
    $out = @()
    foreach ($line in $lines) {
        if ($line -match "^\s*$Key\s*=") { $out += "$Key=$Value"; $found = $true } else { $out += $line }
    }
    if (-not $found) { $out += "$Key=$Value" }
    [IO.File]::WriteAllText($EnvFile, (($out -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
}

function Ensure-Env {
    if (-not (Test-Path $EnvFile)) {
        Copy-Item (Join-Path $Deploy '.env.example') $EnvFile
        Say "Created $EnvFile from the template." 'Yellow'
    }
}

# Boots the distro if needed and waits for Docker.
function Ensure-Docker {
    $null = Wsl --exec true
    if ($LASTEXITCODE -ne 0) { Fail "Could not start the '$Distro' WSL distro. Run:  wsl -l -v" }
    for ($i = 0; $i -lt 60; $i++) {
        $null = Wsl --exec docker info
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    Fail 'Docker did not come up inside WSL. Try:  r6ctl host'
}

function Cmd-Sync {
    Ensure-Env
    Say 'Copying the project into the sealed WSL environment...'
    $items = 'server', 'app', 'analysis', 'database', 'models', 'integration', 'server_main.py', 'r6-dissect', 'deploy'
    $excludes = '__pycache__', '*.pyc', '*.db', '*.bak*', '*.exe', '*.log', '.git', 'server_credentials.py',
                'credentials.py', 'r6-dissect/dissect/test', 'integration/bin'
    $archive = @('-cf', '-')
    foreach ($e in $excludes) { $archive += "--exclude=$e" }
    $archive += @('-C', $Root)
    $archive += $items

    # Extract next to the old copy, then swap, so a half-finished copy never
    # replaces a working one.
    $remote = 'set -e; rm -rf /opt/r6/src.new /opt/r6/src.old; mkdir -p /opt/r6/src.new; ' +
              'tar -xf - -C /opt/r6/src.new; ' +
              'if [ -d /opt/r6/src ]; then mv /opt/r6/src /opt/r6/src.old; fi; ' +
              'mv /opt/r6/src.new /opt/r6/src; rm -rf /opt/r6/src.old; ' +
              'chmod 600 /opt/r6/src/deploy/.env 2>/dev/null || true'
    Send-Tar $archive $remote
}

function Cmd-Host {
    Cmd-Sync
    $null = Wsl --exec true
    Say 'Installing / refreshing Docker Engine and the egress firewall inside WSL...'
    Run --exec sh "$Src/deploy/host/install_docker.sh"
    if ($LASTEXITCODE -ne 0) { Fail 'Docker setup failed.' }

    $changed = $false
    $current = (Wsl --exec cat /etc/wsl.conf) -join "`n"
    $wanted  = (Get-Content (Join-Path $Deploy 'host\wsl.conf') -Raw).Replace("`r`n", "`n").Trim()
    if ($current.Trim() -ne $wanted) {
        Run --exec cp "$Src/deploy/host/wsl.conf" /etc/wsl.conf
        $changed = $true
    }
    $wslconfig = Join-Path $env:USERPROFILE '.wslconfig'
    $wantedCfg = (Get-Content (Join-Path $Deploy 'host\wslconfig') -Raw).Replace("`r`n", "`n").Trim()
    $haveCfg = ''
    if (Test-Path $wslconfig) { $haveCfg = (Get-Content $wslconfig -Raw).Replace("`r`n", "`n").Trim() }
    if ($haveCfg -ne $wantedCfg) {
        Copy-Item (Join-Path $Deploy 'host\wslconfig') $wslconfig -Force
        $changed = $true
    }
    if ($changed) {
        Say 'WSL settings changed. Apply them with:  wsl --shutdown   (then r6ctl up)' 'Yellow'
    } else {
        Say 'Host is up to date.' 'Green'
    }
}

function Cmd-Build {
    Ensure-Docker
    Cmd-Sync
    Say 'Building the server image (the first build downloads a few GB and takes a while)...'
    Compose --progress plain build
    if ($LASTEXITCODE -ne 0) { Fail 'Build failed.' }
}

function Cmd-Up {
    Ensure-Docker
    Cmd-Sync
    Compose --progress plain up -d --build --remove-orphans
    if ($LASTEXITCODE -ne 0) { Fail 'Could not start the stack.' }
    Say ''
    Cmd-Status
}

function Cmd-Down {
    Ensure-Docker
    Compose down --remove-orphans
}

function Get-FunnelUrl {
    $json = (Wsl --cd "$Src/deploy" --exec docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock status --json) -join "`n"
    try { $s = $json | ConvertFrom-Json } catch { return $null }
    if (-not $s -or $s.BackendState -ne 'Running' -or -not $s.Self.DNSName) { return $null }
    return 'https://' + $s.Self.DNSName.TrimEnd('.')
}

function Cmd-Status {
    Ensure-Docker
    Compose ps -a
    Say ''
    Run --exec docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}'
    Say ''
    $port = Get-EnvValue 'R6_LOCAL_PORT'; if (-not $port) { $port = '8000' }
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/api/v1/health" -UseBasicParsing -TimeoutSec 5
        Say "Local health check (127.0.0.1:$port): HTTP $($r.StatusCode)" 'Green'
    } catch {
        Say "Local health check (127.0.0.1:$port): not answering yet" 'Yellow'
    }
    $url = Get-FunnelUrl
    if ($url) { Say "Public address (Tailscale Funnel): $url" 'Green' }
    else { Say 'Public address: Tailscale is not logged in yet. Run:  r6ctl login' 'Yellow' }
}

function Cmd-Logs {
    Ensure-Docker
    $follow = $false; $services = @()
    foreach ($a in $Rest) { if ($a -eq '-f' -or $a -eq '--follow') { $follow = $true } else { $services += $a } }
    $a = @('logs', '--tail', '120')
    if ($follow) { $a += '-f' }
    Compose @a @services
}

function Cmd-Model {
    Ensure-Docker
    Say 'Downloading the AI model into the isolated Ollama volume (about 5 GB)...'
    Compose --profile setup run --rm ollama-pull
    if ($LASTEXITCODE -ne 0) { Fail 'Model download failed.' }
}

function Cmd-Login {
    Ensure-Docker
    $url = Get-FunnelUrl
    if ($url) {
        Say "Already logged in. Public address: $url" 'Green'
        Finish-Funnel $url
        return
    }
    if (-not (Get-EnvValue 'TS_AUTHKEY')) {
        Say 'Waiting for Tailscale to print its sign-in link...'
        for ($i = 0; $i -lt 30; $i++) {
            $log = (ComposeCapture logs --no-color --tail 50 tailscale) -join "`n"
            if ($log -match '(https://login\.tailscale\.com/\S+)') {
                Say ''
                Say 'Open this link in a browser that is signed in to the SECOND Tailscale account:' 'Cyan'
                Say "  $($Matches[1])" 'Cyan'
                Say ''
                Say 'Waiting for you to approve it...'
                break
            }
            Start-Sleep -Seconds 2
        }
    }
    for ($i = 0; $i -lt 90; $i++) {
        $url = Get-FunnelUrl
        if ($url) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $url) { Fail 'Tailscale did not log in. See:  r6ctl logs tailscale' }
    Finish-Funnel $url
}

function Finish-Funnel([string]$Url) {
    Set-EnvValue 'R6_SERVER_PUBLIC_URL' $Url
    if (Get-EnvValue 'TS_AUTHKEY') {
        Set-EnvValue 'TS_AUTHKEY' ''
        Say 'The one-time auth key was used up and has been removed from .env.' 'Gray'
    }
    Say "Public address: $Url" 'Green'
    Say 'Applying it to the server (invite links will use it)...'
    Cmd-Sync
    Compose up -d --no-build server tailscale
    Save-VolumePublicUrl $Url
    Say 'Checking that Funnel is publishing it...'
    Run --cd "$Src/deploy" --exec docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock funnel status
}

function Save-VolumePublicUrl([string]$Url) {
    # server_config.json in the volume is what client builds read (scripts\sync_deployed_server_config.py).
    $py = "import json,sys`np='/data/server_config.json'`nd=json.load(open(p))`nd['public_url']=sys.argv[1]`njson.dump(d,open(p,'w'),indent=2)`n"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
    Run --cd "$Src/deploy" --exec sh -c "echo $b64 | base64 -d | docker compose exec -T server python - '$Url'"
    if ($LASTEXITCODE -ne 0) { Fail 'Could not record the public address in the volume.' }
}

function Cmd-Pause {
    Ensure-Docker
    Compose exec -T server touch /data/analysis_paused.flag
    Say 'Analysis paused: uploads are still accepted, but Whisper/Ollama stay idle. r6ctl resume to undo.' 'Yellow'
}

function Cmd-Resume {
    Ensure-Docker
    Compose exec -T server rm -f /data/analysis_paused.flag
    Say 'Analysis resumed.' 'Green'
}

function Cmd-Migrate {
    $source = Join-Path $Root 'dist\R6Server\server_data'
    $yes = $false
    for ($i = 0; $i -lt $Rest.Count; $i++) {
        if ($Rest[$i] -eq '--source') { $source = $Rest[$i + 1]; $i++ }
        elseif ($Rest[$i] -eq '--yes') { $yes = $true }
    }
    if (-not (Test-Path $source)) { Fail "Source folder not found: $source" }
    $source = (Resolve-Path $source).Path

    if (Get-Process R6Server -ErrorAction SilentlyContinue) {
        Say 'The old R6Server.exe is still running. Anything it receives after this copy will not be' 'Yellow'
        Say 'included, so run this once more after stopping it for the final copy.' 'Yellow'
    }
    Ensure-Docker
    Say "Creating the stack's volume (the image must already be built: r6ctl build)..."
    Cmd-Sync
    Compose up --no-start --no-build server
    if ($LASTEXITCODE -ne 0) { Fail 'The server image/volume is not ready. Run:  r6ctl build' }
    $null = Wsl --exec test -d "/var/lib/docker/volumes/$Volume/_data"
    if ($LASTEXITCODE -ne 0) { Fail "Volume $Volume not found." }

    if (-not $yes) {
        Say ''
        Say 'This REPLACES the databases, uploads, voice recordings, models and tokens in the Docker' 'Yellow'
        Say "volume with a copy of:  $source" 'Yellow'
        Say 'Your original folder is not changed.' 'Yellow'
        if ((Read-Host 'Continue? (y/N)') -notmatch '^[yY]') { Say 'Cancelled.'; return }
    }
    Compose stop server

    $python = $null
    foreach ($candidate in 'python', 'py') {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found -and $found.Source -notmatch 'WindowsApps') { $python = $found.Source; break }
    }
    if (-not $python) {
        $local = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (Test-Path $local) { $python = $local }
    }
    if (-not $python) { Fail 'Python is needed to prepare the databases (python.org).' }
    $stage = Join-Path ([IO.Path]::GetTempPath()) 'r6_migrate_stage'
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    & $python (Join-Path $Deploy 'migrate_data.py') $source $stage
    if ($LASTEXITCODE -ne 0) { Fail 'Could not prepare the databases.' }

    $archive = @('-cf', '-', '--format', 'ustar', '-C', $stage)
    $archive += (Get-ChildItem $stage -File | ForEach-Object { $_.Name })
    $archive += @('-C', $source)
    foreach ($name in 'comms', 'logs', 'models', 'reports', 'uploads', 'voice', 'work', 'server_config.json', 'analysis_paused.flag') {
        if (Test-Path (Join-Path $source $name)) { $archive += $name }
    }
    $mp = "/var/lib/docker/volumes/$Volume/_data"
    Say 'Copying into the volume (about a gigabyte)...'
    Send-Tar $archive "set -e; tar -xf - -C $mp; chown -R 10001:10001 $mp"
    Remove-Item $stage -Recurse -Force
    Say 'Done. Start the server with:  r6ctl up' 'Green'
}

function Cmd-Cutover {
    $url = Get-EnvValue 'R6_SERVER_PUBLIC_URL'
    if (-not $url) { Fail 'No public address yet. Run  r6ctl login  first.' }
    Say ''
    Say "CUTOVER: make the Docker server the live one at $url" 'Yellow'
    $retire = $Rest -contains '--retire-old-address'
    Say 'This will:' 'Yellow'
    Say '  1. stop the old Windows R6Server.exe and disable its scheduled tasks' 'Yellow'
    Say '     ("R6Analyzer Remote Server", "R6Analyzer Server Watchdog")' 'Yellow'
    Say '  2. move the Docker server onto port 8000 (the old exe''s port)' 'Yellow'
    if ($retire) {
        Say '  3. turn OFF the old Tailscale Funnel on this PC (the old address stops working)' 'Yellow'
    } else {
        Say '  3. leave the old Tailscale Funnel on: it now forwards to the Docker server, so apps and' 'Yellow'
        Say '     R6Companion/R6Voice sticks already handed out keep working until they are rebuilt.' 'Yellow'
        Say '     Turn it off later with:  r6ctl retire-old-address' 'Yellow'
    }
    Say '  4. copy the old server data into Docker one last time (replaces the data in the volume)' 'Yellow'
    Say 'Rebuild the clients with the new address afterwards (build_and_deploy.bat); the old' 'Yellow'
    Say 'Windows server files are left in place.' 'Yellow'
    if ($Rest -notcontains '--yes') {
        if ((Read-Host 'Continue? (y/N)') -notmatch '^[yY]') { Say 'Cancelled.'; return }
    }

    foreach ($name in 'R6Analyzer Remote Server', 'R6Analyzer Server Watchdog') {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $name | Out-Null
            Say "Disabled task: $name" 'Gray'
        }
    }
    $old = Get-Process R6Server -ErrorAction SilentlyContinue
    if ($old) {
        $old | Stop-Process -Force
        $old | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue
        Say 'Stopped the old R6Server.exe.' 'Gray'
    }
    if ($retire) { Cmd-RetireOldAddress }
    Set-EnvValue 'R6_LOCAL_PORT' '8000'

    $script:Rest = @('--yes')
    Cmd-Migrate
    Cmd-Up
    Save-VolumePublicUrl $url
    Say ''
    Say "Cutover done. Live address: $url" 'Green'
    if (-not $retire) { Say 'The old address still works too (bridge) until you run:  r6ctl retire-old-address' 'Green' }
    Say 'Next: run build_and_deploy.bat so the app, R6Companion and R6Voice carry the new address.' 'Green'
}

function Cmd-RetireOldAddress {
    $ts = 'C:\Program Files\Tailscale\tailscale.exe'
    if (-not (Test-Path $ts)) { Say 'Tailscale is not installed on this PC; nothing to turn off.' 'Gray'; return }
    & $ts funnel reset
    Say 'Old Tailscale Funnel turned off: the old address no longer reaches anything.' 'Gray'
}

function Cmd-CopyKey {
    Ensure-Docker
    $py = "import json; print(json.load(open('/data/server_config.json'))['api_token'])"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
    $out = @(Wsl --cd "$Src/deploy" --exec sh -c "echo $b64 | base64 -d | docker compose exec -T server python -")
    $key = $out | Where-Object { $_ -match '^[A-Za-z0-9_\-]{30,}$' } | Select-Object -First 1
    if (-not $key) { Fail 'Could not read the API key from the server volume (is the stack up?  r6ctl status).' }
    Set-Clipboard -Value $key
    Say 'The main API key is on your clipboard (it is not printed). Paste it into the dashboard''s API Token box.' 'Green'
}

function Cmd-RotateTokens {
    # r6ctl rotate-tokens [api|voice|both] [--yes]
    Ensure-Docker
    if ($Rest -contains 'done') {
        Compose exec -T server rm -f /data/server_config.json.pre-rotation
        Say 'Rollback copy of the old keys deleted.' 'Green'
        return
    }
    $which = 'both'
    foreach ($a in $Rest) { if ($a -in 'api', 'voice', 'both') { $which = $a } }
    Say ''
    Say "ROTATE TOKENS ($which): the server gets new keys; the old ones stop working at once." 'Yellow'
    Say '  - R6Analyzer.exe (api), R6Companion.exe and R6Voice.exe (voice) must be rebuilt with the new' 'Yellow'
    Say '    keys (build_and_deploy.bat); copies already handed out will be refused until then.' 'Yellow'
    Say '  - Browser invite links are NOT affected. Dashboard sign-in needs the new api key.' 'Yellow'
    Say '  - The old keys are kept in /data/server_config.json.pre-rotation (inside the volume) so this' 'Yellow'
    Say '    can be undone; delete it once every client works:  r6ctl rotate-tokens done' 'Yellow'
    if ($Rest -notcontains '--yes') {
        if ((Read-Host 'Continue? (y/N)') -notmatch '^[yY]') { Say 'Cancelled.'; return }
    }
    $py = @'
import datetime, json, os, secrets, shutil, sys
p = '/data/server_config.json'
which = sys.argv[1]
with open(p, encoding='utf-8-sig') as f:
    d = json.load(f)
backup = p + '.pre-rotation'
shutil.copyfile(p, backup)
os.chmod(backup, 0o600)
if which in ('both', 'api'):
    d['api_token'] = secrets.token_urlsafe(32)
if which in ('both', 'voice'):
    d['voice_token'] = secrets.token_urlsafe(24)
d['rotated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
tmp = p + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2)
os.replace(tmp, p)
print('server_config.json updated (' + which + '); other settings kept')
'@
    $py = $py -replace "`r`n", "`n"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
    Run --cd "$Src/deploy" --exec sh -c "echo $b64 | base64 -d | docker compose exec -T server python - '$which'"
    if ($LASTEXITCODE -ne 0) { Fail 'Could not rewrite server_config.json; nothing was changed.' }
    Say 'Restarting the server so it loads the new keys...'
    Compose restart server
    Say 'Done. Next: run build_and_deploy.bat, then update data\settings.json api_key on the USB if one is pinned.' 'Green'
}

function Cmd-Keepalive {
    # Holds the WSL VM open and lowers its CPU priority so games always win.
    $p = Start-Process wsl.exe -ArgumentList '-d', $Distro, '-u', 'root', '--exec', '/usr/bin/sleep', 'infinity' `
        -WindowStyle Hidden -PassThru
    for ($i = 0; $i -lt 60; $i++) {
        $vm = Get-Process vmmemWSL -ErrorAction SilentlyContinue
        if ($vm) {
            foreach ($proc in $vm) { try { $proc.PriorityClass = 'BelowNormal' } catch { } }
            break
        }
        Start-Sleep -Seconds 2
    }
    $p.WaitForExit()
}

function Cmd-Autostart {
    $mode = 'on'; if ($Rest -and $Rest[0]) { $mode = $Rest[0] }
    if ($mode -eq 'off') {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Say 'Auto-start removed.' 'Green'
        } else { Say 'Auto-start was not installed.' }
        return
    }
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument ("--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File " + (Quote $PSCommandPath) + " keepalive")
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force `
        -Description 'Keeps the R6Host WSL distro running so the R6 server containers start with Windows.' | Out-Null
    Say "Auto-start installed: the server stack now starts when you log in to Windows." 'Green'
    Say 'Undo with:  r6ctl autostart off'
}

function Cmd-Help {
    @'
r6ctl -- the R6 server, running in Docker inside an isolated WSL environment

  first time
    setup          create deploy\.env from the template
    host           install/refresh Docker + the container firewall inside WSL
    build          build the server image
    model          download the AI model (isolated Ollama volume)
    up             start everything (server, ollama, tailscale)
    login          sign the Tailscale container in and publish the Funnel address
    migrate        copy the old server's data into Docker   [--source DIR] [--yes]
    cutover        retire the old Windows server, final data copy; the old address keeps
                   working as a bridge   [--yes] [--retire-old-address]
    retire-old-address   turn off the old Funnel (after every client has the new address)
    rotate-tokens  new api/voice keys   [api|voice|both] [--yes]  (then: done, to drop the rollback copy)
    copy-key       put the main API key on the clipboard (never printed), to sign in to the dashboard
    autostart     start the stack automatically at Windows logon   [off]

  every day
    status         containers, CPU/RAM use, health, public address
    logs [svc] [-f]   server | ollama | tailscale
    pause / resume hold or allow Whisper/Ollama analysis (uploads always accepted)
    up             also applies code changes (rebuilds what changed)
    down           stop the containers (data is kept)
    sync           copy the project into WSL without building
'@ | Write-Host
}

switch ($Command.ToLowerInvariant()) {
    'setup'     { Ensure-Env; Say "Edit $EnvFile if you need to, then:  r6ctl build" }
    'host'      { Cmd-Host }
    'sync'      { Ensure-Docker; Cmd-Sync }
    'build'     { Cmd-Build }
    'up'        { Cmd-Up }
    'down'      { Cmd-Down }
    'status'    { Cmd-Status }
    'logs'      { Cmd-Logs }
    'model'     { Cmd-Model }
    'login'     { Cmd-Login }
    'pause'     { Cmd-Pause }
    'resume'    { Cmd-Resume }
    'migrate'   { Cmd-Migrate }
    'cutover'   { Cmd-Cutover }
    'retire-old-address' { Cmd-RetireOldAddress }
    'rotate-tokens' { Cmd-RotateTokens }
    'copy-key'  { Cmd-CopyKey }
    'keepalive' { Cmd-Keepalive }
    'autostart' { Cmd-Autostart }
    default     { Cmd-Help }
}
