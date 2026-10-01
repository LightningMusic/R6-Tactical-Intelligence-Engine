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

### Known gaps
* **Key rotation undecided.** On 2026-09-30 a PowerShell alias collision printed the API and voice tokens into a local Claude Code session transcript (not into any repo file). Rotating means regenerating `server_config.json`'s tokens, rebuilding the clients/sticks, and updating the pinned `api_key` in `data\settings.json`. Offered, not decided.
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

All three cut a recording into **5-minute chunks** and upload each as it closes, identified by a 32-hex `recording_id` and `chunk_index`, stamped with the sender's own PC clock (`start_epoch`). Closing the laptop or powering the PC off loses at most the last few minutes; whatever is queued uploads next time. Recorders heartbeat every few seconds; the host's client re-sends "recording" every minute, and if the host goes silent for 20 minutes the server tells recorders to stop (a crashed host PC must not leave everyone recording).

### Server pipeline (`server/services/comms_service.py`, `analysis/comms_timeline.py`, `analysis/voice_align.py`)
1. **Host package processed:** the host's tracks are transcribed separately: `self` (the host's mic, every line theirs) and `team` (Discord output, everyone else mixed). Older single-track packages still work, just without speaker names. Rounds and the kill feed (with `elapsedSeconds`) are saved.
2. **Teammate chunks arrive**, often after the match was already processed.
3. **Align and merge:** for each session whose time window overlaps a recording (with ±120 s margin), the teammate's audio is assembled and lined up against the host's Discord track. A teammate's words are in both recordings but not sample-identical (Discord's codec and noise suppression, other voices on top), so alignment cross-correlates 32-band log spectrograms (10 ms steps) over the teammate's speech and fits **offset + drift** across windows. A candidate offset is then **verified** (≥ 35% of the teammate's own lines must be audible in the Discord track at that offset, peak ≥ 4σ), which is what makes "this person was not in this match" a safe answer. The speech is then gated to where they actually talk and transcribed under their name.
4. **Timeline:** everything goes onto one POSIX-epoch clock. Replay headers carry the host PC's *local* wall-clock time labelled "Z" (it is not UTC; V4 §11 noted the neighbouring bug), so the host's UTC offset converts it; `elapsedSeconds` places each event within the round (`PREP_OFFSET_SEC = 1.0`). Output: a speaker-labelled transcript, the timeline, and a *measured* comms summary (warnings before deaths, talk-over, callout-after-fight patterns) that feeds the AI debrief.
5. `process_pending()` runs whenever the worker is idle and rebuilds sessions once a teammate's newest chunk is 90 s old, so late uploads still land in the right match.

Clocks matter: the PCs' clocks only need to agree to roughly a second or two (Windows time sync on); the search is narrow first and widens only if nothing convincing is found.

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
* **`deploy/smoke_test.py`** (server end to end; its default `--base` still says port 8001 and now needs `8000`), **`scripts/e2e_browser_recorder.py`** (throwaway server + real Edge/Chrome with a fake microphone: follows the host, chunks tile on the server clock, offline stretch queued then uploaded, revoked link refused; needs `playwright`).

---

## 14. Open Items & Next Steps

### Do next
1. **James's stick, then `r6ctl retire-old-address`.** The old address is the only thing keeping pre-cutover apps alive. With two sticks the gating is short: the host's own stick already has the new address; the older stick needs a fresh `R6Companion.exe` (plug it in labelled `R6_COMPANION` and run `build_and_deploy.bat`) or James simply uses his `/join` link. Any `R6Voice.exe` handed out before cutover also needs replacing. Then retire the bridge.
2. **Create invite links** for each browser teammate from the dashboard (in-game name exactly as it appears in replays; they are shown once). Hand them out with `voice_recorder/FOR_TEAMMATES.txt`-style instructions adapted to the browser.
3. **First real practice = the validation** of the whole comms timeline with real teammates. Expect to tune; watch `r6ctl logs server`.

### Safety (found while writing V5)
* **Git has not been committed since 2026-08-26.** About 127 changed/untracked paths, including everything new since then (`server/invites.py`, `join.html`, `comms_service.py`, `companion/`, `voice_recorder/`, `deploy/`, the new analysis modules, and most of V4's own work) exist only in the working tree. Commit in sensible chunks and push; do not use `git add .` (there are credential target files and a `Claude outputs/` folder).
* **`*.spec` is gitignored**, so the four PyInstaller specs (the build recipes) are in no repository. Un-ignore them or back them up.
* `r6_data` volume has no scheduled backup; use the `tar` one-liner in `deploy/README.md` periodically (it holds every match and recording).

### Decisions pending
* Rotate the API and voice tokens (§7), or not.
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
