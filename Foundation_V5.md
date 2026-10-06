# R6 Tactical Intelligence Engine

# FOUNDATION V5 — AS-BUILT ARCHITECTURE

**Supersedes:** Foundation V4 (2026-09-11). V4 described a Windows-exe server reached over a personal Tailscale network. This describes what exists as of **2026-10-01**: the server now runs in an isolated Docker stack, it is publicly reachable on purpose, and teammates record their own mic through a browser page instead of installing anything. Where V4 and reality now disagree, this document is the source of truth. `Foundation_V3_2.md` and `Foundation_V4.md` are kept unchanged as the historical record of the original plan and the first as-built state.

---

## 0. What changed since V4

| Area | V4 (2026-09-11) | V5 (2026-10-01) |
|---|---|---|
| Server runtime | `R6Server.exe` (PyInstaller, ~4.5 GB) on the host PC's Windows account | Docker stack (server + Ollama + Tailscale) inside a sealed WSL2 distro; the old exe is retired |
| Public address | Funnel from the host's *personal* tailnet | Funnel from a **second** Tailscale account, so teammates never see the host's own tailnet. Cloudflare Tunnel is wired in but dormant, for the day there is a domain |
| Teammate audio | Not captured (only the host's own recording) | Browser recorder (`/join`), `R6Voice.exe`, `R6Companion.exe`; comms timeline built from all of it |
| Credentials | One API token | API token + narrow voice token + per-person invite tokens |
| Client OBS capture | One mixed audio track | Three tracks: Discord (team), own mic (self), everything else |
| Server analysis | Report from transcript + stats | Speaker-labelled transcript, comms timeline against the kill feed, measured comms summary feeding the AI debrief |
| Client schema | Migration V4 | Migration **V5** (game-ID catalog tables) |
| Model | portable Ollama inside `server_data/` | Ollama container, `llama3.1:8b`, CPU-only |

---

## 1. System Overview

One repository, five deliverables:

| Piece | What it is | Who runs it | Where |
|---|---|---|---|
| **`R6Analyzer.exe`** (client) | PySide6 desktop app: OBS control, `.rec` import, local fallback transcription/AI, player/operator database, Dashboard | The host only | The host's USB stick; no admin rights |
| **Server** | FastAPI app + Whisper + Ollama + the dashboard and `/join` pages | Always on | Docker in WSL2 on the host's desktop (`deploy/`) |
| **`/join` browser recorder** | One web page: open your invite link, press Start; the browser records your own mic and uploads it | **Every teammate except the two stick users** | Any modern browser, no install |
| **`R6Voice.exe`** | Tiny Windows app that records your mic and uploads it | Optional | Any Windows PC |
| **`R6Companion.exe`** | Drives OBS on a PC the stick is plugged into: follows the host's start/stop, records audio, ships chunks | Stick users only | A USB stick carrying the exe and a portable OBS |

The client still works completely standalone with no server configured. With a server configured, the client uploads the raw package after a match and does nothing else, so the host can unplug and leave.

**Non-goals, still true:** no third-party cloud/API dependencies for analysis (Whisper and Ollama run on the host's own hardware), no real-time overlay, no video/OCR parsing, no Discord bot capturing per-user audio (declined by the coach, V4 §8; the browser/R6Voice/Companion paths are opt-in per person). **What changed:** the server is now publicly reachable by design; that is what lets teammates join from a browser. Its protection is §3 (isolation) and §7 (credentials), not obscurity.

---

## 2. Who Uses What

| Principal | Uses | Credential | Reaches |
|---|---|---|---|
| **Host** (Elijah) | `R6Analyzer.exe`, the dashboard, `r6ctl` | Main **API token** | Everything |
| **Stick users** (the host's own stick, and the older stick expected to go to James) | `R6Companion.exe` / `R6Voice.exe` | **Voice token**, embedded at build time | `/voice/*`, `/companion/heartbeat`, `/join/whoami` only |
| **Browser teammates** (everyone else) | `/join#<invite token>` | **Invite token**, one per person | The same four endpoints, for their own username only |
| **Anyone on the internet** | Static pages only | none | `/`, `/dashboard`, `/join` (markup), `/api/v1/health` |

**Scale decision (2026-10-01):** only two USB sticks exist: the host's, and the host's older stick, which will probably go to James. Everyone else is served through the website. So the browser path is the primary teammate path, not a fallback, and the USB-stick path is deliberately small. Consequences: the voice token lives in at most the two sticks (plus any `R6Voice.exe` copies handed out earlier, unknown), and the day-to-day credential teammates hold is a revocable, expiring invite link.

---

## 3. Server Runtime (Docker in a sealed WSL2 distro)

```
internet --HTTPS--> Tailscale Funnel (2nd account) --> server container --> ollama container
                                                           |
                                                           +-- /data volume (databases, uploads, voice, recordings, tokens)
```

* **Where:** WSL2 distro `R6Host` (Ubuntu 24.04, installed at `D:\WSL\R6Host`) with Windows drive automount and interop **off**: the distro cannot see `C:`/`D:` or start Windows programs. Docker runs inside it. `deploy\r6ctl.bat` drives everything from Windows.
* **Compose project `r6`** (`deploy/docker-compose.yml`): `server`, `ollama`, `tailscale`, plus dormant `cloudflared` (profile `cloudflare`) and one-shot `ollama-pull` (profile `setup`).
* **Networks:** `backend` is *internal* (no internet) and holds `ollama`; `egress` has outbound internet for `server` and `tailscale` only. Ollama has no published port and no internet.
* **Host firewall:** `r6-egress.sh` installs a `DOCKER-USER` REJECT for loopback, private and CGNAT ranges, so even a compromised container cannot reach the host PC, the LAN, or the host's Tailscale network. Only the public internet is reachable.
* **Least privilege:** non-root user, read-only root filesystem (`/data` volume and `/tmp` tmpfs are the only writable places), all Linux capabilities dropped, `no-new-privileges`, per-container CPU/RAM/PID caps. Defaults: server 6 CPUs/8 GB, Ollama 6 CPUs/10 GB, Tailscale 1 CPU/512 MB. The whole VM is capped at 10 threads/18 GB by `~\.wslconfig`.
* **Published port:** the server binds only `127.0.0.1:${R6_LOCAL_PORT}` (now **8000**) for a client running on the same PC. Nothing listens on the LAN.
* **Public address:** Tailscale Funnel from the container's own node `r6-server` on a second, free Tailscale account (userspace mode, no TUN, no extra privileges). The account's policy needs `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`; without it the node is online but the address never resolves. The address is `R6_SERVER_PUBLIC_URL` in the gitignored `deploy/.env` and `public_url` in the volume's `server_config.json`.
* **Analysis hardware:** Ollama runs CPU-only (`num_gpu=0`) on purpose, so a game on the same PC keeps the whole GPU; on this machine's Radeon the CPU was also faster. Model `llama3.1:8b` was picked by an end-to-end A/B on a real 2-round match (3B: 1.5 min but ignored kill data; Qwen 7B: 2.7 min but contradicted itself; Llama 8B: 3.5 min and the only one to report KDs correctly). Whisper model: `base`.
* **Volumes:** `r6_data` (databases, uploads, voice, comms, `server_config.json`), `r6_ollama_models`, `r6_ts_state` (the Tailscale login). Back up `r6_data` with the `tar` one-liner in `deploy/README.md`.
* **Keep-alive:** the distro shuts down when no `wsl.exe` session is attached, which would stop the containers. The scheduled task **"R6 Server (WSL keep-alive)"** (`r6ctl keepalive`) holds one open; `r6ctl autostart` installs it. Without it the server silently dies at logoff.

### `r6ctl` commands

| Command | Does |
|---|---|
| `status` | containers, CPU/RAM, health, public address |
| `up` / `down` | copy code in, rebuild what changed, start / stop (data kept) |
| `logs [server\|ollama\|tailscale] [-f]` | logs |
| `pause` / `resume` | hold / allow Whisper + Ollama processing (uploads are always accepted) |
| `model` | (re)download the AI model |
| `login` | sign the Tailscale container in and publish the address |
| `migrate` | copy the retired Windows server's data into the volume |
| `cutover` | stop the old server and its tasks, final data copy, Docker takes port 8000 (`--retire-old-address` also ends the bridge) |
| `retire-old-address` | turn off the old Funnel (`tailscale funnel reset` on the host) |
| `copy-key` | put the main API key on the clipboard without printing it, for signing in to the web dashboard (run it as `deploy\r6ctl.bat copy-key`; `r6ctl` alone is not on the PATH) |
| `rotate-tokens [api\|voice\|both] [--yes]` | generate new keys in the volume's `server_config.json` (every other setting kept), keep the old file as `server_config.json.pre-rotation`, restart the server; `rotate-tokens done` deletes that rollback copy |
| `autostart [off]` | start the stack at Windows logon |
| `host` / `setup` / `build` | install/refresh Docker + firewall in the distro; first-time setup; build the image |

### Deployment state (2026-10-01)

* **Cutover is done.** The host ran `r6ctl cutover --yes` on 2026-09-30/10-01: old `R6Server.exe` stopped; scheduled tasks "R6Analyzer Remote Server" and "R6Analyzer Server Watchdog" disabled; Docker listens on port 8000; data migrated; **API and voice tokens migrated unchanged**, so every existing credential kept working.
* **Old-address bridge is still on.** The host's personal-tailnet Funnel still publishes the *old* public address and now proxies to the Docker container, so apps built before cutover keep working. It is switched off with `r6ctl retire-old-address`, deliberately **not yet run** (see §14).
* **Clients rebuilt** with the new address; embedded address and keys verified inside the finished exes (§12). Compatibility test: 13/13 against both the new and the old address.
* **Cloudflare path (future, needs a domain):** put the domain on Cloudflare (free), create a tunnel to `http://server:8000`, set `CLOUDFLARE_TUNNEL_TOKEN`, `COMPOSE_PROFILES=cloudflare` and the new `R6_SERVER_PUBLIC_URL` in `deploy/.env`, rebuild clients. Caveat: Cloudflare's free plan rejects request bodies over 100 MB; voice chunks are far below that, but a very long match package could exceed it. Full steps in `deploy/README.md`.

---

## 4. Current File Structure

```plaintext
R6Analyzer/
├── main.py                       <-- client entry; configures logging first
├── server_main.py                <-- server entry (the container's CMD); logging first
│
├── app/                          <-- client
│   ├── app_controller.py, config.py (PATH AUTHORITY), logging_setup.py
│   ├── session_manager.py        <-- recording lifecycle, transcription dispatch, sync trigger
│   ├── sync_coordinator.py, upload_queue.py, uploader.py, packaging.py
│   ├── companion_link.py         <-- NEW: tells the server when the host starts/stops; reads companion status
│   ├── server_credentials.py     <-- build-time embed target (kept EMPTY in the working tree)
│
├── gui/                          <-- main_window, dashboard_view, match_view, recording_view, analysis_view,
│                                     export_view, settings_view (9 tabs), speaker_tagging_dialog;
│                                     db_editor_view.py is still a 0-byte stub
├── database/                     <-- db_manager, schema.sql, repositories (shared), migrations (schema V5),
│                                     seed_operators, game_catalog.py (NEW: self-updating operator/map IDs)
├── integration/                  <-- obs_controller (3-track routing), whisper_transcriber, rec_importer,
│                                     discord_capture, voice_buffers, ubisoft_catalog.py (NEW: operator/gadget sync)
├── analysis/                     <-- intel_engine, match_builder, metrics_engine, report_generator,
│                                     timeline_aligner, transcript_parser, event_parser, dashboard_stats,
│                                     comms_timeline.py (NEW), voice_align.py (NEW)
├── models/
│
├── server/                       <-- FastAPI; runs in the container
│   ├── main.py, config.py, auth.py, database.py, match_db.py, repositories.py, storage.py, worker.py
│   ├── invites.py                <-- NEW: browser invite tokens
│   ├── dashboard_routes.py       <-- NEW: serves /, /dashboard, /join with hardened headers
│   ├── api/v1.py
│   ├── services/                 <-- session_processing, package_validation, comms_service.py (NEW)
│   ├── static/                   <-- dashboard.html (incl. invite management), join.html (NEW)
│
├── companion/                    <-- NEW: R6Companion.exe (core, companion_app, obs_link, audio_export, credentials)
├── voice_recorder/               <-- NEW: R6Voice.exe (r6voice, recorder, uploader, credentials)
├── r6-dissect/                   <-- Go replay parser source (fork); Windows exe bundled in the client, Linux build compiled in Docker
│
├── deploy/                       <-- NEW: Docker stack and operations
│   ├── docker-compose.yml, Dockerfile, Dockerfile.dockerignore (allow-list), requirements-container.txt
│   ├── r6ctl.bat / r6ctl.ps1     <-- the one command that drives the server
│   ├── client_compat_test.py, smoke_test.py, migrate_data.py
│   ├── .env (gitignored secrets) / .env.example, README.md
│
├── scripts/                      <-- embed_client_credentials.py, sync_deployed_server_config.py (Docker-aware),
│                                     e2e_browser_recorder.py, setup_tailscale_funnel.py, make_companion_usb.ps1, backfill_assists.py
├── build_scripts/                <-- build_and_deploy.bat's internals (env_setup, locate_ffmpeg, verify_usb_drive, ...)
├── tests/                        <-- 32 files, 223 tests collected (§13)
│
├── build_and_deploy.bat          <-- THE build script for client, R6Voice, R6Companion (and the legacy server exe)
├── R6Analyzer.spec / R6Server.spec / R6Voice.spec / R6Companion.spec   <-- NOTE: *.spec is gitignored (§14)
│
├── remote_deploy.bat, setup_ssh_server.ps1, Remote/            <-- V4's SSH remote-rebuild pipeline, unchanged and unverified
├── server_watchdog.ps1, setup_server_autostart.ps1,
│   toggle_server.bat, toggle_analysis.bat, setup_tailscale_funnel.bat   <-- LEGACY (Windows-exe server era)
│
├── data/                         <-- client data, relative to the USB root (matches.db, settings.json, recordings, logs)
└── server_data/                  <-- legacy Windows-server data folder (ignored by git); live data is the r6_data volume
```

---

## 5. Databases

### Client (`data/matches.db`, `database/schema.sql`, migrations up to **V5**)
V3.2 core tables unchanged (`matches`, `rounds`, `players`, `maps`, `operators`, `gadgets`, `operator_gadget_options`, `player_round_stats`, `round_resources`, `transcripts`, `derived_metrics`, `metadata`), plus:
* V4: `player_aliases`, `transcript_speaker_labels` (V4 §3, §8).
* V5: `operator_game_ids`, `map_game_ids`, `map_sites`, `unknown_maps`. Replays identify operators and maps only by numeric IDs; `database/game_catalog.py` learns them from imported replays (linking by name, then by bomb-site names, otherwise flagging an `unknown_maps` row for a human to name once, which backfills every match already played on it). `integration/ubisoft_catalog.py` fills the parts replays never carry (operators, gadgets, abilities) from Ubisoft's official operator pages, fail-safe: a failed or implausibly small fetch changes nothing.

`database/repositories.py` and `analysis/match_builder.py` remain the single shared implementation of "replay becomes a match" for client and server.

### Server (`server_matches.db` for bookkeeping, `matches.db` for the shared match schema)
`server_sessions`, `server_packages`, `server_jobs`, `server_parsed_matches`, `server_transcripts` (V4), plus:
* `voice_chunks`: one row per 5-minute teammate chunk (`recording_id`, `chunk_index`, `username`, `start_epoch` from the teammate's own clock, duration, sample rate, file, sha256, `is_final`).
* `session_comms`: everything needed to rebuild one session's comms timeline without re-reading its package (host utterances, rounds + kill feed, which teammate recordings are folded in, the timeline).
* `session_voice`: a teammate's transcribed speech for one session, so a rebuild only transcribes recordings it has not seen.
* `companion_control` (one row: should the team be recording) and `companions` (each check-in's status).
* `invites`: invite id, username, label, **hash of the token**, created/expires/revoked/last-seen.
* `voice_profiles`: one learned voiceprint per in-game name (running-mean embedding, sample count, seconds of audio); see §8.
* `team_roster`: the host's saved pick-list of in-game names and nicknames for the dashboard's invite form (`server/roster.py`). It lives only in the volume, never in the repo, so teammates' game names are not published with the code.

---

## 6. API Surface (`/api/v1`)

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness (also the Docker healthcheck) |
| `GET /auth/test` | API token | credential check used by the client's Settings |
| `POST /sessions/upload`, `GET /sessions`, `/{id}`, `/{id}/status`, `/{id}/summary`, `/{id}/transcript`, `/{id}/analysis`, `/{id}/comms`; `POST /{id}/retry` | API token | package upload and results |
| `GET /sessions/{id}/report`, `/pdf` | API token | report retrieval (`/report` is still a 501 stub) |
| `GET /voice/ping`, `POST /voice/chunks` | voice token, invite, or API token | connection check + clock; upload one chunk (idempotent) |
| `GET /join/whoami` | voice / invite / API | whose link this is + server time (clock-offset estimate) |
| `POST /companion/heartbeat` | voice / invite / API | a recorder's status in, the team's recording state out |
| `PUT /companion/control`, `GET /companion/status` | API token | host sets "recording"; host reads every recorder seen in the last 24 h |
| `GET/POST /invites`, `POST /invites/{id}/regenerate`, `DELETE /invites/{id}` | API token | invite management (the dashboard has the UI) |
| `GET /voices`, `DELETE /voices/{username}` | API token | learned voice profiles (what the server knows, and forgetting one) |
| `GET/PUT /roster` | API token | the saved team pick-list for the invite form; `PUT` replaces the whole list, and one bad name rejects the save (400) and leaves the old list intact |
| `GET /voice/recordings` | API token | teammate recordings the server holds |

Voice-chunk guards: allowed types `.ogg .opus .flac .wav`; `recording_id` must be 32 lowercase hex; chunk ≤ 900 s, 8-48 kHz, `start_epoch` within 30 days of now. Voice token: up to 60 MiB per chunk. **Invite tokens are tighter:** the upload's `username` must equal the invite's, ≤ 25 MiB per chunk, and at most **14 hours of audio per person per day** (HTTP 429 beyond that), so a leaked link cannot fill the disk. Uploads are idempotent, so a retry after a dropped connection simply overwrites.

---

## 7. Credentials & Security Model

### Three credentials, deliberately unequal

| | Main **API token** | **Voice token** | **Invite token** |
|---|---|---|---|
| Held by | host client, dashboard sign-in | sticks (embedded in `R6Companion.exe`/`R6Voice.exe`) | one teammate's browser link |
| Reaches | everything | voice upload, heartbeat, `whoami` | the same, only as its one username |
| Revocable | rotate (touches every client) | rotate (touches every stick) | per person, instantly, from the dashboard |
| Expires | no | no | yes: 180 days default, 400 max |
| Stored server-side | `server_config.json` in the volume | same file | **SHA-256 hash only** in `invites` |

Format: `inv_<8 hex id>_<secret>`. Shown **once** at creation; a lost link is replaced with *regenerate*, which kills the old one. Comparison is constant-time. The token rides in the URL **fragment** (`/join#inv_...`), which browsers never send to the server. The `/join` response carries `no-store`, `no-referrer`, a CSP with `default-src 'none'` and same-origin `connect-src` (no third-party connections of any kind), `X-Frame-Options: DENY`, and a microphone-only permissions policy.

### Where the keys live and how they get to clients
* Server: `server_config.json` in the `r6_data` volume holds `api_token`, `voice_token`, `public_url`; self-provisioned on first run and reused thereafter (env `R6_SERVER_API_TOKEN`/`_HASH`, `R6_SERVER_VOICE_TOKEN` override).
* Clients get them at **build time**, never typed: `scripts/sync_deployed_server_config.py` (now Docker-aware: it reads the volume through `wsl.exe -d R6Host --exec cat ...`) then `scripts/embed_client_credentials.py --write` / `--write-voice` write them into `app/server_credentials.py`, `voice_recorder/credentials.py`, `companion/credentials.py` immediately before PyInstaller and `--clear` / `--clear-voice` empty them right after, success or failure. **All three files must be empty in the working tree; verified empty 2026-10-01.**
* **Address precedence (a real trap):** a `server_url`/`api_key` saved in the client's `data\settings.json` beats the embedded value. A stale pinned `server_url` therefore silently points a freshly rebuilt client at the old address. Clear it (or set it to the new one) after every address change; done on 2026-10-01. Companion/R6Voice use their embedded values, else their own settings.
* Verification of a build does not trust the build log: read `PYZ.pyz` out of the finished exe (`PyInstaller.archive.readers.CArchiveReader`) and compare the embedded address and keys with the server's. All four deployed exes matched on 2026-10-01.

### Other secrets
* `deploy/.env` (gitignored) holds `R6_SERVER_PUBLIC_URL`, `R6_LOCAL_PORT`, and optional `TS_AUTHKEY` / `CLOUDFLARE_TUNNEL_TOKEN`; `TS_AUTHKEY` is cleared after login.
* `server_data/` and `laptop_smb_credentials.json` are gitignored (V4 §9). **Resolved:** `server_data/server_config.json` was never committed to git history, so V4's "check whether to rotate" concern does not apply. The six tracked `server_data/uploads/*.r6session` files are 964-byte test fixtures, not real match data.
* Dev/build tooling secrets (SSH key-only access, the dedicated SMB account) are unchanged from V4 §9.

### Rotation
**Both keys were rotated on 2026-10-01** (`r6ctl rotate-tokens both`), after a PowerShell alias collision on 2026-09-30 had printed the old API and voice tokens into a local Claude Code session transcript (never into a repo file or git history). Verified afterwards against both the local port and the public address: new keys accepted, old keys refused with 401, the voice key refused on API-only endpoints, `public_url` preserved. Invite links were unaffected. The generated keys are 43 (API) and 32 (voice) characters of URL-safe randomness.

Rotating again is one command plus a rebuild: `r6ctl rotate-tokens`, then `build_and_deploy.bat`, which re-embeds the new keys into the three exes. The pinned `api_key` in `data\settings.json` (repo and USB) was cleared so clients use the embedded key and a rotation no longer needs a settings edit. Anything built before a rotation (an old `R6Voice.exe`, a stick not rebuilt) is refused until rebuilt, which also makes the old-address bridge (§3) pointless once the second stick is rebuilt. The dashboard needs the new API key at its next sign-in.

### Known gaps
* The server prints the API token on startup (V4's "read it off the console" convenience); in Docker that lands in the container logs (20 MB x 5 files, local driver). Removing that banner is optional hardening.
* The only application-level abuse control is the per-invite daily audio cap. Token guessing is impractical (24-32 bytes of entropy) but there is no lockout; Funnel/Cloudflare are the outer layer.

---

## 8. Teammate Audio & the Comms Timeline

### Three ways in, one pipeline

| | Browser `/join` | `R6Voice.exe` | `R6Companion.exe` |
|---|---|---|---|
| Needs | a browser and a mic | any Windows PC | a PC the stick is plugged into; carries its own OBS |
| Start/stop | follows the host (default), or "always" mode | manual Start/Stop | follows the host automatically |
| Network loss | queues chunks in IndexedDB, uploads later | queue in `%LOCALAPPDATA%\R6Voice\pending` | queue on the stick |
| Keeps the screen awake | Wake Lock where supported | n/a | n/a |
| Used by | everyone except the two stick users | optional | the host's stick and James's stick |

All three cut a recording into chunks (**5 minutes** for `R6Voice.exe` and `R6Companion.exe`, **20 seconds** for the browser page, so a closed tab loses almost nothing) and upload each as it closes, identified by a 32-hex `recording_id` and `chunk_index`, stamped with the sender's own PC clock (`start_epoch`). Closing the laptop or powering the PC off loses at most the last few minutes; whatever is queued uploads next time. Recorders heartbeat every few seconds; the host's client re-sends "recording" every minute, and if the host goes silent for 20 minutes the server tells recorders to stop (a crashed host PC must not leave everyone recording).

### Server pipeline (`server/services/comms_service.py`, `analysis/comms_timeline.py`, `analysis/voice_align.py`)
1. **Host package processed:** the host's tracks are transcribed separately: `self` (the host's mic, every line theirs) and `team` (Discord output, everyone else mixed). Older single-track packages still work, just without speaker names. Rounds and the kill feed (with `elapsedSeconds`) are saved.
2. **Teammate chunks arrive**, often after the match was already processed.
3. **Align and merge:** for each session whose time window overlaps a recording (with ±120 s margin), the teammate's audio is assembled and lined up against the host's Discord track. A teammate's words are in both recordings but not sample-identical (Discord's codec and noise suppression, other voices on top), so alignment cross-correlates 32-band log spectrograms (10 ms steps) over the teammate's speech and fits **offset + drift** across windows. A candidate offset is then **verified** (≥ 35% of the teammate's own lines must be audible in the Discord track at that offset, peak ≥ 4σ), which is what makes "this person was not in this match" a safe answer. The speech is then gated to where they actually talk and transcribed under their name.
4. **Timeline:** everything goes onto one POSIX-epoch clock. Replay headers carry the host PC's *local* wall-clock time labelled "Z" (it is not UTC; V4 §11 noted the neighbouring bug), so the host's UTC offset converts it; `elapsedSeconds` places each event within the round (`PREP_OFFSET_SEC = 1.0`). Output: a speaker-labelled transcript, the timeline, and a *measured* comms summary (warnings before deaths, talk-over, callout-after-fight patterns) that feeds the AI debrief.
5. `process_pending()` runs whenever the worker is idle and rebuilds sessions once a teammate's newest chunk is 90 s old, so late uploads still land in the right match.

Clocks matter: the PCs' clocks only need to agree to roughly a second or two (Windows time sync on); the search is narrow first and widens only if nothing convincing is found.

### Voice recognition (learned voices, `server/voice_id.py`, added 2026-10-02)
* **Learning:** when a teammate's own recording (companion or browser) is verified against the host's Discord track, the stretches of that track where their mic shows them speaking are their voice as Discord delivers it. Those stretches (≥ 1 s, longest 40 per session) are embedded with Resemblyzer (a 256-number voiceprint; the model ships inside the image, ~23 ms per clip on CPU) and averaged into one running-mean profile per in-game name (`voice_profiles`). A profile is not used until it has 5 stretches behind it.
* **Recognising:** when the timeline is built, each Discord line still labelled "Team (unassigned)" (≥ 1 s) is compared with the profiles and named only if the best score is ≥ 0.75 **and** at least 0.05 ahead of the runner-up; otherwise it stays unassigned. Anyone who has their own aligned recording in that session is skipped (their lines already come from their own track). Voiceprints are cached per session (`comms/<session>/voice_embeddings.json`), so rebuilds are cheap.
* **So the whole team can be named without everyone recording every night:** one or two verified sessions per person teach the server their voice.
* **Not validated on real recordings yet.** The thresholds are conservative starting values; expect to tune them after the first sessions with real teammate recordings. `GET /voices` shows what has been learned and how much audio is behind each profile; `DELETE /voices/<name>` forgets one; `R6_VOICE_ID=0` switches the feature off. A recording that does not line up with the Discord track (such as the unexplained 2026-10-01 one) teaches nothing.
* Needed a fix to work at all: Resemblyzer's `webrtcvad` imports `pkg_resources`, which setuptools 81+ no longer ships, so voice clustering had never run in the container; the Dockerfile now pins `setuptools==80.9.0`.

### What the AI debrief is given (rewritten 2026-10-02, `analysis/team_facts.py`)
* **Our team only.** The server reads the recording player's team from the replay (`ours` per round) and analyses only those players; opponents are no longer written up. Nicknames come from the package roster, overridden by the host's saved team list.
* **Numbers are worked out in code, not by the model.** Each player is judged against the team's average tonight and against their own history over earlier stored matches (needs 3+): the facts arrive pre-judged as `(+)` / `(-)` / `(=)` with words from fixed bands (a 1.00 K/D is "in line", never "high"), with a small-sample caution. The model only phrases STRENGTH / FOCUS / DRILL from them.
* **Round patterns are written by code** and swapped into the finished debrief, because the 8B model misread round numbers.
* **Broken metrics removed.** "Man-advantage conversion" and "clutch rate" counted every player in the match (so always 0%); they are replaced by first-kill conversion and clutches from the kill feed. Utility efficiency is hidden because ability use is never recorded (item 9 in §14).
* Regenerating without re-transcribing: `CommsService._refresh_ai(match_id, refresh_players=True)` redoes the AI text from stored data without re-transcribing (used on 2026-10-02 for the 2026-10-01 matches).
* **Objective play, utility and operators lead; kills are supporting evidence (2026-10-02).** The debrief is now assembled in a fixed order by code (`assemble_report`): MATCH SUMMARY (model), ROUND PATTERNS, OBJECTIVE PLAY, UTILITY & OPERATORS, WHAT TO FOCUS ON NEXT, then COMMUNICATION (model). Focus points are ordered plants, post-plant results, gadget use, then comms, side balance and first kills. In a player's write-up a K/D fact weighs half as much as before and a gadget fact more than any K/D fact.
* **Only the saved team list gets a personal write-up.** Team numbers still include the whole five (the random fifth teammate counts toward the team), but only usernames in the host's saved team list (`team_roster`) are written up and named in the utility lines (`report_players`).
* **Gadget usage is now measured (`integration/replay_utility.py`).** r6-dissect reads nothing about gadgets, so this reads the decompressed replay itself (needs `zstandard`): a player's own entity (their handle minus 7) points at their operator-gadget item, whose charge counter is re-sent on every change; a drop is a use (the sum of drops, because recharging gadgets rise and fall). Operators with no countable gadget are absent (not measured, never "unused"). The format knowledge comes from the public documentation of the wnc-replay/replay-tool project (independent implementation, nothing copied). **Correction, 2026-10-05:** the counter's first real value is the *starting* count, written in the item's creation record (marker byte `0x1B`). The first version of the reader looked only at update records (`0x22`/`0x23`), so it missed the starting count (undercounting uses by one, and reading 1-charge gadgets as unused) and sometimes gave a neighbouring entity's count to the wrong player. A property now belongs to the nearest preceding record header `[marker 0x1A/0x1B/0x22/0x23][entity ref][00 00 00 00]`. After the fix the starting counts match the operator catalogue for most operators (Ace 3, Bandit 4, Goyo 4, Kaid 2, Mute 4...; Thorn, Ying and Kali read 4 in every round, so those 'seed' catalogue values are probably outdated). The attack-side figures reported on 2026-10-02 (such as "Thermite 0 of 4" and attack gadgets used in 6 of 14 operator-rounds) were artefacts of that bug; corrected, the 2026-10-01 matches read attack 11/12 on Fortress and 4/8 on Lair, defense 12/12 on both. Known limit: a charge used before the replay's first sync of the item (seen once: Fuze at 3 of 4) is not counted.
* **Secondary gadgets (frags, stuns, claymores, wire...) are tracked too (2026-10-05).** A second loadout slot on the player's entity (hash `0xF7B590D8`) points at the secondary-gadget item, read exactly like the operator gadget: each player's own count, not the shared per-team pool the game also keeps (that pool also drops when a teammate dies carrying them, which is why it first looked unattributable). Cross-checked in Fortress round 4: every use matched a pool drop, and each death-sized pool drop matched a teammate's death with the gadget unused. Stored in the round events (`player_derived.secondary_start` / `secondary_used`, `secondary_tracked`), shown as a team total and a per-player SECONDARY line, and only raised as a focus point when the team's use is very low; it is never turned into a personal strength or weakness (not using a claymore is not a mistake). The gadget type is not named.
* **Plants and defuses: who did them, and attempts that did not finish (2026-10-05, `integration/replay_utility.py`).** Y11S3 replays no longer carry the player in r6-dissect's defuser packet, so r6-dissect credited whoever was first in the player list (it named an opposing defender as the planter, and put a "defuse complete" 1 s after a plant). The replay still records the answer: a plant or defuse is a 7-second countdown in the defuser-timer packets, and the player doing it has their controller's "weapon ready" flag (hash `0xD48DDCA4`) drop to false in the same packet and come back up just after the countdown ends. Across all 16 rounds of 2026-10-01 exactly one player on the right side had that signature in every countdown (one case, a Montagne whose flag was already down and never changed, is decided by elimination and marked "likely"). A countdown that stops before zero is an attempt that did not finish (planter killed or let go); a real defuse is a completed countdown, so the old "within 5 s" timing guess is only the fallback for matches whose replay could not be read. The same approach appears in the julio208920 fork of r6-dissect; this is an independent implementation. The report lists planters/defusers from the saved team list only (others as "a teammate"), interrupted attempts on both sides, and whether we went for the defuse after an enemy plant. If the replay cannot be read it says so instead of guessing. **The Go parser was fixed the same way (2026-10-05, `r6-dissect/dissect/defuse.go`, tests in `defuse_test.go`):** it now records every player's controller entity and every weapon-ready change, credits the right player on `DefuserPlantComplete` / `DefuserDisableComplete` (blank rather than a guess when the replay is unclear), emits one event per countdown (the old "second 0.00x packet = disable" false positive is gone, so teams no longer both read as winners), ignores a lone `0.00`, and drops a "disabled" event in a round the attackers won by score. Compared on 58 real rounds from 9 matches (Sep 21 to Oct 1, all Y11S3_Alpha04): the patched parser and the independent Python reader named the same player in every round, and "both teams won" fell from 15 rounds to 0. One rule needed the round result: a countdown that reaches zero but is followed by an attackers' win is a defuser killed in the last instant (final values cannot tell it apart: genuine completions have ended at 0.000, 0.003 to 0.007 and 0.022). The Windows `integration/bin/r6-dissect.exe` is rebuilt from this source (`go build -trimpath -ldflags "-s -w"`, the same flags the Docker build uses); `libr6dissect.dll` is an unused leftover from upstream. The old exe is not kept in the repo.
* **Operator names in transcripts (`analysis/operator_vocab.py`).** Last night's 1,229 lines showed Whisper mostly gets operator names right; its misses are mostly ordinary English words ("with" for Twitch, "back" for Buck), which a fuzzy fix would make far worse. So only spellings that can mean nothing but an operator ("yeager", "dokebi", "thorne", "denaria", "capitan"...) are corrected, and the list is meant to be extended from real transcripts. A Whisper `initial_prompt` of operator names was considered and rejected: with `condition_on_previous_text=False` it only influences the first 30-second window of each chunk.
* **Re-reading old sessions:** `POST /api/v1/sessions/{id}/reread-replays` re-reads a stored session's replays for gadget usage and objective events (seconds, no transcription) and regenerates its AI text; used for the 2026-10-01 matches.

### Client-side capture (OBS)
`integration/obs_controller.py` routes audio into **three tracks**: Discord (`team`, track 1), own mic (`self`, track 2), everything else (`other`, track 3, kept but off the comms tracks). `data/recordings/track_layouts.json` records when routing was confirmed, so packaging only treats the split as real for recordings made after it ran (an older file has the same number of streams but each is the full mix).

### Validation status
Unit and integration tests cover the pipeline, including a synthetic 4-voice Discord mix, and the replay-to-speech timing was sanity-checked on the 2026-09-29 Border match (10 rounds, 41 min of comms: speech onsets cluster 1-3 s after kills, as the reaction callouts should). **No real multi-teammate practice has run through it yet.** The first practice with browser teammates is the real validation (§14).

---

## 9. Client Flow & Sync (carried from V4, condensed)

1. **Start Recording** snapshots the Replay folder, routes OBS tracks, starts OBS, and tells the server the team is recording (`companion_link`).
2. **Stop Recording** stops OBS, diffs the folder, waits for file stability, tells the server to stop.
3. **Processing:** with a reachable server the client uploads the raw `.r6session` package (zip + `manifest.json` + SHA-256 for every file) and does nothing else; if the server is unreachable it falls back to full local processing rather than losing data. Pending uploads persist on disk with exponential backoff, surviving a drive-letter change or restart.
4. **Handoff:** `ImportResult` (`SUCCESS` / `PARTIAL_FAILURE` / `CRITICAL_FAILURE`) routes to Analysis View or the manual Match View (V3.2 §10, unchanged).

`analysis_mode`: `local`, `remote`, `automatic` (default: upload whenever a server is configured). The server runs `RecImporter` → `match_builder` → Whisper → `IntelEngine` with the same shared code the client uses, and results appear on the dashboard and under `/sessions/{id}/...`. The host can `r6ctl pause` Whisper/Ollama while gaming; uploads are always accepted.

Still **not done:** server-side speaker *diarization* (the server instead labels speakers from the separate host tracks and teammates' own recordings, which is more reliable); only the client's local fallback pipeline runs diarization.

---

## 10. Path Authority & Portability

**Client** (`app/config.py`): `BASE_DIR` comes from `sys.executable`'s parent when frozen, never the working directory. V3.2's rule (no `os.getcwd()`, no hardcoded strings) still holds. **Server:** V4's confirmed exception (`DATA_DIR` relative to the launch directory, which once produced two independent tokens on one PC) is **closed by the Docker move**: `R6_SERVER_DATA_DIR=/data` is a single named volume, and the sync script reads that volume as the one source of truth.

---

## 11. Logging & Observability

Unchanged from V4 §7: `configure_logging()` tees stdout/stderr into a rotating file (2 MB x 5) at the very start of both entry points (`data/logs/r6analyzer.log` on the USB), and `pipe_process_to_log()` captures long-running child processes. New: the container logs go to Docker's `local` driver (20 MB x 5 per container), read with `r6ctl logs`. Still no in-app log viewer.

---

## 12. Build & Deploy

* **Client, R6Voice, R6Companion:** `build_and_deploy.bat` (run from the repo root). It syncs tokens/address from the Docker volume, embeds credentials, builds with PyInstaller, clears the credential files, and deploys to the USB behind the V4 safety gates (hard drive-identity check by label and size; `settings.json` and `matches.db` backed up before and verified after, auto-restored if missing). It copies `R6Voice.exe` to `F:\R6Voice` and, if a stick labelled `R6_COMPANION` is plugged in, installs `R6Companion.exe` plus a portable OBS (program files only; the stick's own OBS settings are kept). A failed R6Voice/R6Companion build never fails the client build.
* **Server:** `deploy\r6ctl.bat up` (copies code into the distro, rebuilds changed layers, restarts). The legacy `R6Server.exe` is still built by `build_and_deploy.bat`, and the script restarts it only if it was running before the build; it is retired and could be removed from the pipeline.
* **Batch files must be CRLF.** An LF-only `build_and_deploy.bat` once ran mangled commands. `.gitattributes` enforces this only for `deploy/r6ctl.bat`; `build_and_deploy.bat` relies on manual care, so re-check line endings after every edit.
* V4's fragility notes still apply (prefer inlining over new shared subroutines in `build_and_deploy.bat`) and the SSH/SMB remote-rebuild pipeline is unchanged and still unverified end to end.

---

## 13. Testing & Verification

* **Unit/integration suite:** 223 tests collected. Run with the system Python 3.12 (`python -m pytest`), ignoring `tests/test_local_regression.py`, `tests/test_obs_profile_rotation.py`, `tests/test_should_defer_transcription.py`, and deselecting three environment-dependent tests (`test_intel_engine_overrides.py::test_generate_reports_unavailable_backend_without_crashing`, `test_server_match_analysis.py::test_real_intel_engine_with_no_backend_marks_failed_not_completed`, `test_settings.py::test_settings_defaults`). Last full run: **217 passed, 3 skipped, 3 deselected.** Two tests can wake a real Ollama, so check what you deselect on a gaming PC. Back up `data/matches.db`, `data/settings.json`, `data/queue/queue.json` around runs; tests touch them.
* **`deploy/client_compat_test.py --base <address>`:** drives the *real* client classes with the real keys (client `test_connection`/upload/status, companion control and heartbeat, R6Voice ping and chunk upload; wrong keys must be refused). 13 checks; cleans up nothing server-side, so remove its `Compat_Test` rows afterwards. This is what proved the cutover safe.
* **`deploy/smoke_test.py`** (server end to end; default `--base` is `http://127.0.0.1:8000`), **`scripts/e2e_browser_recorder.py`** (throwaway server + real Edge/Chrome with a fake microphone: follows the host, chunks tile on the server clock, offline stretch queued then uploaded, revoked link refused; needs `playwright`).
* **Companion power-cut test (2026-10-01, real `R6Companion.exe` + its portable OBS, run from a scratch copy under a throwaway name):** the companion started OBS by itself ~20 s after the host's Start (nothing touched in OBS); the first 5-minute piece uploaded as it closed; then a normal host Stop uploaded the final 97 s piece, with nothing left queued. **That run did NOT test a hard power cut** (corrected 2026-10-02): the "kill" matched processes by the short 8.3 form of the folder name while Windows reports the long form, so nothing was killed and the stray OBS kept running until it was found the next morning. **Real hard-kill test, 2026-10-02** (new build, scratch copy, throwaway name; the script resolves the path, kills by PID and asserts the processes are dead): the first 5-minute piece uploaded as it closed; at 76 s into piece 2 the companion and its OBS (3 processes) were force-killed; OBS's stale "crashed" marker was left behind; on relaunch the companion started cleanly (no Safe Mode dialog), recovered the interrupted file and uploaded its 45.7 s as chunk 1 of the same recording, and nothing was left queued. Only the in-progress piece is at risk in a hard power-off, and it was recovered here. Both sticks are NTFS (the FAT32 caveats in older notes no longer apply to them).
* **Hardening added the same day:** settings, saved export state and upload-queue entries are written write-then-rename (a power cut keeps the old file); a half-written queue entry is set aside instead of blocking the queue; after the host stops, the companion ships its last file within ~3 s instead of waiting for the 15 s loop.

---

## 14. Open Items & Next Steps

### Do next
1. **`r6ctl retire-old-address`.** The old-address bridge no longer protects anything: the 2026-10-01 key rotation made every pre-rotation app unusable regardless of address, and the rebuild already put the new address and keys on both sticks (`F:` and `H:\R6Companion`, verified in the finished exes). Run it once James's stick is handed over (or he uses `/join`); any `R6Voice.exe` handed out earlier is dead until replaced. The only reason to wait is if you want to keep the old URL answering "401" instead of "unreachable".
2. **Create invite links** for each browser teammate from the dashboard (in-game name exactly as it appears in replays; they are shown once). Hand them out with `voice_recorder/FOR_TEAMMATES.txt`-style instructions adapted to the browser.
3. **First real practice = the validation** of the whole comms timeline with real teammates. Expect to tune; watch `r6ctl logs server`.
4. **Host app froze on Start Session (2026-10-01); mitigated, root cause never seen directly.** The log showed OBS connecting at 16:43 and then 50 minutes of silence until a restart; no OBS-side log or Windows hang record survived. Suspect: `RecordingView._start_session` ran the whole OBS start-up on the GUI thread (scene switch, `ensure_comms_tracks()` with its dozen-plus websocket calls, `StartRecord`; the OBS client's per-call timeout is 60 s). Fixed in the next build: the OBS start now runs on a worker thread (`_obs_start_worker`, status "Starting OBS recording...", double-click ignored, button restored on failure), and `app/stall_watchdog.py` writes every thread's stack to `data/logs/r6analyzer.log` if the window stops responding for 8 s. **If it ever freezes again, read the `[Watchdog]` lines in that log: they name the call it is stuck in.**
5. **Unexplained companion recording (2026-10-01 17:34-18:56).** `H:\R6Companion` recorded 16 pieces (80 min) under a teammate's username, started 14 s after the host's Start, and was closed at Stop, leaving one unexported 2-minute file (`recordings\2026-10-01 18-54-42.mp4`) that uploads on the stick's next launch. The host says they did not run it; nothing on the host PC auto-launches it (no scheduled task, Run key, Startup entry or autorun). Do NOT delete the server-side recording: it may be that teammate's real mic (they played in that session). Resolved 2026-10-02: for both matches the pipeline reported "could not line up with the Discord track" and used none of it (0 lines), so it did not pollute the timeline; it is safe to delete.
6. **Server transcription is slow and hogs the PC.** Whisper (`base`, `beam_size=5`, `word_timestamps=True`, temperature fallback) runs inside a 6-CPU container cap (torch 5 threads); on 2026-10-01 a 41-minute track took over 3 h while the host was gaming and building. A restart of the server kills the job in progress, so tuning (greedy decoding, silence skipping, thread count) and any `rotate-tokens` must wait until the queue is empty.
7. **Resolved 2026-10-02: the startup banner printed the API key into the container logs, and a log read leaked the live key into a Claude session transcript.** The banner no longer prints it (`server/main.py`, covered by `tests/test_startup_banner.py`; use `r6ctl copy-key`), and the API key alone was rotated (`r6ctl rotate-tokens api`; the voice key was never exposed). Verified: new key accepted, old key 401, voice key and public address untouched. The rollback copy (`server_config.json.pre-rotation`, which holds the leaked key) should be deleted with `r6ctl rotate-tokens done` once the rebuilt clients are confirmed working.
8. **OBS detection (fixed and built 2026-10-02).** The host app treated any `obs64` process as its own, so the companion's OBS running on the same PC made it skip launching its own OBS and then fail to connect (error dialogs). `_obs_is_running()` now ignores OBS processes under an `R6Companion` folder.
9. **Resolved 2026-10-02 for new matches: gadget use is now measured** (see §8, `integration/replay_utility.py`). Matches imported before it have `ability_used` = 0 everywhere (match 40 and earlier included) and are treated as "not measured" unless re-read (`reread-replays`); the old "man-advantage conversion 0%" metric was removed. The old `secondary_used` column stays unused; secondary gadgets live in the round events instead (§8). Matches 22 and 23 (2026-10-01) were re-read after the 2026-10-05 fixes; other old matches still hold the first reader's (undercounted) numbers until `reread-replays` is run on them. The same debrief also misstates round numbers (llama3.1:8b), so trust the measured numbers over the narrative. The two jobs of 2026-10-01 took about 6 h and 4.7 h of processing each (the second also waited 5.4 h in the queue), so the debrief arrived roughly 10 h after the second upload (CPU contention plus the slow decode settings in item 6).
10. **Resolved 2026-10-05: a stale saved API key locked the host app out of its own server for a whole practice night.** The app prefers an API key saved in Settings over the one built into the exe, and the stick's `data/settings.json` still held the pre-rotation key, so every upload was refused ("Invalid or expired API token"); the key rotation of 2026-10-02 had only been verified inside the exes, not against that override. Because the server closes the connection early on a refused key, big uploads surfaced as bogus `SSLEOFError` network errors, and past the retry cap a refused key counted as a refused *package* (never retried). Fixed in code (`app/uploader.py`, `app/sync_coordinator.py`, `app/config.py`): when two different keys exist, the app asks the server (`/auth/test`) which it accepts BEFORE sending a package, uses that one and drops a refused saved key; a refused key no longer counts toward the retry cap; and the log says "Server check failed: <reason>" instead of "not reachable". Tonight's six matches were uploaded from the stick with the app's own sync code after clearing the stale key. After any future key rotation, check `data/settings.json` on each stick as well as the exes (`scripts/` has no check for this; the app now heals itself).
11. **Recordings are no longer deleted before their matches are safe on the server (2026-10-05).** The automatic cleanup used to keep the newest 3 recordings and delete the rest unconditionally, and removed a 5 GB video the same night every upload was failing. `app/recording_safety.py` now allows deletion only when every match from the recording (matched by time: a match is queued during the recording or within 30 min of it stopping) is uploaded AND the server finished analysing it, the recording is not recent, and its start time can be read from the file name; otherwise it is kept and the log says why. The manual "clean old recordings" button follows the same rule. If the stick is nearly full and recordings are being kept, the log says so (the cleanup exists because a full 64 GB stick once broke an import).
12. **The browser recorder recorded nothing on 2026-10-05 because the host's start signal was refused (fixed, with a loud warning added).** A teammate (Zander) opened his invite link, pressed Start and his page checked in for two hours (over 2,000 heartbeats), but it uploaded nothing. The page defaults to "follow the host": it records only while the host app's session is running, which the host app announces with `PUT /api/v1/companion/control` once a minute and the page reads back from its heartbeat reply. All 129 of those signals that night got HTTP 401 (the stale saved key of item 10), so the page sat on "Waiting for the host to start a match" all evening. The host app used to swallow that failure silently; `CompanionLink.set_recording` now records why it failed and the recording view prints one warning when it first fails ("Couldn't tell teammates' recorders to start: the server refused this app's API key. Anyone recording in 'follow the host' mode will NOT record until this is fixed") and one line when it recovers. Fallback while that is broken: the page's "Record continuously" checkbox (ignores the host).
    * **Survives a shutdown (`scripts/e2e_browser_shutdown.py`, real Edge, fake microphone):** recorded about 52 s with the network down, killed the browser process with no Stop pressed, reopened the same profile at `/join` with no invite link: the page remembered the invite and uploaded the saved chunks by itself (40 s survived; only the last unsaved <20 s, still in memory, is lost). Every 20 s chunk is written to the browser's IndexedDB the moment it is cut and removed only after the server accepts it. **Not testable here:** a school-managed PC that wipes the browser profile at logoff would lose anything not yet uploaded, so the page now also warns before closing while chunks are waiting, and a teammate should leave the tab open until "Waiting" reads 0 (the companion stick avoids this by writing to the USB drive).
    * **"database is locked" refused requests (fixed).** Six requests that night (a teammate's invite check, and the worker's own idle loop) died with `sqlite3.OperationalError: database is locked`: the server's two SQLite files used the default journal, in which a big write makes readers fail after 5 s. `server/database.py` and the server's match database (`DatabaseManager(wal=True)`) now use write-ahead logging with a 30 s busy timeout; the two live files were switched to WAL in place on 2026-10-05 (the mode is stored in the file, so no restart was needed). The USB stick's own database keeps the default journal (safe to unplug). The code change and the join-page tweak reach the container with the next `r6ctl up`; avoid that while a transcription job runs (a restart requeues it).
14. **Before every practice, run the connection preflight (`scripts/preflight_connections.py`, added 2026-10-06).** It finds the sticks by volume label and, for each exe, reads the credentials actually baked into it and tests them against the live public address: the host app's API key (`/auth/test`), R6Voice's and the companion's voice key (`/voice/ping`, which also reports the PC's clock error), the stick's saved settings (a saved key overrides the built-in one: the 2026-10-05 failure), nothing stuck in the upload queue, the host's start/stop signal (re-sends the current state, changes nothing), the upload route (a junk package must be rejected with 400, not 401), the companion's own settings and pending recordings, and which invite links work. `--live-invite` adds a real browser-recorder round trip with a throwaway invite. `scripts/e2e_live_recorder.py` runs the whole chain that failed on 2026-10-05 against the live server (host key flips the start signal, a real browser with a fake microphone notices within seconds, records, uploads, and the final chunk lands); it briefly flips the host signal (restored) and leaves a test recording named Preflight_Web, so do not run it while teammates' recorders are online. Run both with the project's `.venv` Python (PyInstaller is needed to read the exes); the live one also needs `pip install playwright` and Edge/Chrome. **What it cannot test:** the host app's link to OBS on the practice PC. The stick's OBS keeps its websocket password in that PC's `%APPDATA%\obs-studio` (there is no `portable_mode.txt`), and the stick holds one saved profile ("Default"). On the dev desktop that password does NOT match (OBS answers "Authentication failed"), but the dev desktop is not where practices happen (the 10/1 and 10/5 replays were never on it); OBS connected on the practice PC on both days and nothing about it has changed. The first Start Session on the practice PC is the real test: look for "OBS connected". If a stick is used on another PC, add an OBS profile for it (the app tries every saved profile).
13. **The replay's recording-player lookup was wrong in some rounds (fixed 2026-10-05).** `RecImporter.find_recorder` now matches the Ubisoft profile id first. In rounds 1-3 of one match every player carried the same stray numeric id (6366317606386794496), so no player matched, the round had "0 ours, 10 theirs", and its kill feed, objective data and comms timeline were lost. `backfill_session` (`reread-replays`) now also refreshes a session's saved rounds and rebuilds its comms timeline when it already has a transcript.

### Safety
* **Committed and pushed to `origin/main` on 2026-10-01.** Five weeks of work had gone uncommitted (last commit 2026-08-26); it went up as eight commits (build tooling, Docker + server, recorders, client, tests, docs, `r6ctl rotate-tokens`, this document's update). The owner believes the GitHub repo is public (not verified; `gh` is not installed) and accepted that. The pushed range was scanned for token values, auth keys, invite links and the second-account identifiers and came back clean.
* The four PyInstaller specs are now version-controlled (`*.spec` is no longer ignored); the three credential target files are committed as **empty** placeholders. Never `git add .` or `git commit -a`: that would also stage the next item.
* **Still deliberately uncommitted:** `data/settings.json` (holds a live OBS password; the API key is now cleared), `data/matches.db`, `data/queue/queue.json` (all three are tracked, so a plain `commit -a` would publish them, and untracking them would make a `git pull` on another PC delete that PC's copies), the rebuilt `integration/bin/r6-dissect.exe`, and six deleted `server_data/uploads` test fixtures. A scratch file named `Remote` is untracked junk.
* **The r6-dissect Go fork is not versioned.** `r6-dissect/` is a git link with no `.gitmodules`, pointing at the upstream project, and the fork's six edited Go files (`dissect/defuse.go`, `feedback.go`, `player.go`, `reader.go`, `scoreboard.go`, `time.go`) exist only as uncommitted changes in that nested repo. The owner has said they do not mind either way and, on 2026-10-05, that the parser may be changed freely wherever the raw replay data allows. The edits now matter (the server image and the sticks are built from them), so `deploy/r6-dissect-fork.patch` holds the fork's whole uncommitted diff (apply with `git apply` inside a fresh upstream checkout) as a safety copy; refresh it with `git -C r6-dissect diff > deploy/r6-dissect-fork.patch` after further edits. If the edits ever need sharing, vendor the sources into this repo or publish a fork and point the link at it.
* The committed `data/settings.json` in git history already contains an `obs_password` (an API key has never been committed), and that history is now pushed. It only protects the local OBS WebSocket on the dev PC; change it in OBS (Tools, WebSocket Server Settings) and in the app's Settings, since the repo is probably public.
* `r6_data` volume has no scheduled backup; use the `tar` one-liner in `deploy/README.md` periodically (it holds every match and recording).

### Decisions pending
* Whether to drop the legacy `R6Server.exe` from `build_and_deploy.bat`, and delete the old Windows-server files and `D:\R6_PROJ_backup_2026-09-29`, once happy with Docker.
* Optional: link `lammtozzz` to Zander (Settings → Players aliases); known usernames are `lammtozzz` = Zander, `LightningMusic6` = Elijah.
* Domain + Cloudflare Tunnel when affordable (the foundation is in place, §3).

### Carried from V4 (unchanged)
SMB push of the remote-deploy pipeline never verified end to end; `gui/db_editor_view.py` is an intentional empty stub; no in-app log viewer; real-Ollama server analysis now *has* run on this hardware (the Docker server reprocessed real matches), but the multi-teammate path has not.

---

## 15. Fixed-in-production Bugs Worth Knowing About

Carried from V4 §11 (kills/deaths always zero; dashboard cut off on small screens; console flashing during local transcription; missing no-window flag in `timeline_aligner`), plus:
* **Replay timestamps are host-local with a bogus "Z"**: always convert via the host's UTC offset (`comms_timeline.local_stamp_to_epoch`).
* **A pinned `server_url` in `settings.json` overrides rebuilt clients** (§7).
* **The WSL distro dies without an attached session**, silently stopping the containers (keep-alive task, §3).
* **Funnel address never resolves without the `funnel` nodeAttr**, and public DNS can take 5-15 minutes (sometimes only after restarting the tailscale container).
* **Ollama HTTP 500 "failed to allocate CPU_REPACK buffer"** means commit-limit exhaustion (C: nearly full), not low RAM; kill any orphan `llama-server`.
* **LF-only `.bat` files** break `cmd.exe` (§12).
* **PowerShell 5.1/7 traps used by `r6ctl`:** an alias beats a same-named function (a helper named `H` ran `Get-History`), `Clean` is a reserved block keyword in 7.3+, .NET `Process` stdin adds a BOM, `wsl.exe` output is UTF-16, and `wsl --exec` avoids shell quoting.
