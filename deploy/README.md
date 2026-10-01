# R6 server in Docker (isolated)

The server (Whisper + Ollama analysis, the teammate `/join` recorder, the
dashboard) runs in Docker containers inside a **sealed WSL2 distro** called
`R6Host`, instead of as a normal program on your Windows account.

```
internet --HTTPS--> Tailscale Funnel (2nd account) --> server container --> ollama container
                                                         |
                                                         +-- /data volume (databases, uploads, recordings)
```

What the isolation buys you, in order of importance:

- **No access to your PC.** The distro has no `C:`/`D:` mounted and cannot launch
  Windows programs. Even a fully compromised server only sees its own volume.
- **No access to your network.** Containers are firewalled off from your LAN,
  your PC and every private range (`r6-egress.sh`); they can only reach the internet.
- **Ollama has no internet at all** and no published port: only the server can talk to it.
- **Least privilege**: non-root, read-only root filesystem, all Linux capabilities
  dropped, CPU/RAM/process caps. WSL itself is capped at 10 of your 20 threads and
  18 GB RAM (`~\.wslconfig`) and runs at below-normal priority, so games win.
- **Teammates never join your Tailscale network.** The container is its own node on
  a *second* Tailscale account; Funnel only publishes one HTTPS address.

Your USB app and R6Companion/R6Voice are unchanged; they just talk to the new address.

## Commands (`deploy\r6ctl.bat`)

| command | what it does |
|---|---|
| `r6ctl status` | containers, CPU/RAM, health, public address |
| `r6ctl up` | copy code in, rebuild what changed, start everything |
| `r6ctl down` | stop the containers (data kept) |
| `r6ctl logs [server\|ollama\|tailscale] [-f]` | logs |
| `r6ctl pause` / `resume` | hold / allow Whisper + Ollama (uploads are always accepted) |
| `r6ctl model` | (re)download the AI model |
| `r6ctl login` | sign the Tailscale container in, publish the address |
| `r6ctl migrate` | copy the old Windows server's data in |
| `r6ctl cutover` | stop the old Windows server + its tasks, final data copy, Docker takes port 8000; the old address keeps working as a bridge (`--retire-old-address` turns it off at once) |
| `r6ctl retire-old-address` | turn off the old Funnel once every client has the new address |
| `r6ctl autostart [off]` | start the stack when you log in to Windows |
| `r6ctl host` | reinstall/refresh Docker + firewall inside the distro |

## First-time setup (already done on this PC except the Tailscale account)

1. `r6ctl setup`, then `r6ctl build` (a few GB of downloads, 10-20 minutes).
2. `r6ctl model` downloads llama3.1:8b into the isolated Ollama volume.
3. **Second Tailscale account** (free): sign up at https://login.tailscale.com with
   a *different* login than your personal tailnet (a new Google/Microsoft/GitHub
   account). Then in its admin console:
   - DNS: turn on **MagicDNS** and **HTTPS certificates**.
   - Access controls: the policy file needs the Funnel attribute, otherwise the node
     is online but the public address never resolves:
     `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`.
     After adding it, `r6ctl down` + `r6ctl up` (or restart the tailscale container);
     public DNS can take 5-15 minutes to appear.
4. `r6ctl up`, then `r6ctl login`: open the link it prints while signed in to the
   *second* account. In **Machines**, open `r6-server` > *Disable key expiry*.
   `r6ctl` writes the resulting `https://r6-server.<tailnet>.ts.net` into `.env`.
5. `r6ctl migrate` copies the old server's matches, uploads, voice data and tokens
   into the volume (your original folder is untouched), then `r6ctl up` again.
6. `r6ctl cutover` makes Docker the live server (stops the old Windows server and its
   scheduled tasks, copies the data one last time). The old Funnel address keeps
   working through Docker until you run `r6ctl retire-old-address`, so apps already
   handed out do not break the moment you cut over.
7. Switch the clients to the new address: `build_and_deploy.bat` (it reads the tokens and
   address from the Docker volume via `scripts\sync_deployed_server_config.py`). A
   `server_url` typed into an app's Settings overrides the built-in address, so clear it
   (or set it to the new address) in `data\settings.json` on the USB.
   Check any build with `python deploy\client_compat_test.py --base <address>`.
8. `r6ctl autostart` so it all comes back after a reboot. The distro stops when no
   Windows session is attached, so the logon task also holds it open (`r6ctl keepalive`).

## Moving to your own domain later (Cloudflare Tunnel)

Tailscale Funnel is the free option. When you have a domain, a Cloudflare Tunnel
gives you `https://r6.yourdomain.com` with Cloudflare's DDoS protection in front:

1. Put the domain on Cloudflare (free plan). Zero Trust > Networks > Tunnels >
   *Create a tunnel* > Docker. Copy the **token** only.
2. Add a public hostname: `r6.yourdomain.com` -> service `http://server:8000`.
3. In `deploy\.env`: set `CLOUDFLARE_TUNNEL_TOKEN=...`, `COMPOSE_PROFILES=cloudflare`
   and `R6_SERVER_PUBLIC_URL=https://r6.yourdomain.com`, then `r6ctl up`.
4. Stop the Funnel container if you no longer want it: `r6ctl logs` shows both;
   remove the `tailscale` service from `docker-compose.yml` when you're ready.
5. Rebuild the clients with the new address.

Caveat: Cloudflare's free plan rejects request bodies over **100 MB**. Teammate
recorder chunks (<= 25 MiB) and most match packages are fine; a very long match
package over 100 MB would need to go through Tailscale Funnel or a paid plan.

## Everything lives in three Docker volumes

`r6_data` (databases, uploads, recordings, `server_config.json` with the tokens),
`r6_ollama_models`, `r6_ts_state` (the Tailscale login). Back up `r6_data` with:

```
wsl -d R6Host -u root --exec tar -czf - -C /var/lib/docker/volumes/r6_data/_data . > r6_data_backup.tgz
```

## Troubleshooting

- **`r6ctl` says Docker didn't come up**: `wsl --shutdown`, then `r6ctl status`.
- **Funnel address not found**: `r6ctl logs tailscale`. Usually the sign-in link
  hasn't been approved, or HTTPS certificates are off in the second account.
- **Analysis is slow or hogging the PC**: `r6ctl pause` while gaming, `r6ctl resume` after.
- **Changed `.wslconfig`/`wsl.conf`**: `wsl --shutdown` applies it.
