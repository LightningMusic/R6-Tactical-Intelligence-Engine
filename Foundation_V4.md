# R6 Tactical Intelligence Engine

# FOUNDATION V4 — AS-BUILT ARCHITECTURE

**Supersedes:** Foundation V3.2 (pre-build specification, 2026-08-27-ish). V3.2 described what was about to be built; this describes what actually exists as of 2026-09-11, across six milestones, two audits, and a string of real-hardware-only bugs V3.2 had no way to anticipate. Where V3.2 and reality now disagree, this document is the current source of truth — V3.2 is kept only as a historical record of the original plan.

---

## 1. System Overview

Two separate portable Windows executables, built from one repository, sharing a database schema and an analysis engine by import rather than by copy:

* **`R6Analyzer.exe`** (the client) — PySide6 desktop app, runs from a USB stick, no admin rights required. Controls OBS recording, imports `.rec` replays, transcribes comms, runs local AI analysis, and manages the operator/gadget/player database.
* **`R6Server.exe`** (the server) — headless FastAPI app, meant to run on a machine that stays on (home PC or a laptop). Optionally takes over transcription and AI analysis from the client so you can disconnect and leave immediately after a match, instead of waiting for local processing.

The client works completely standalone with no server configured — everything V3.2 originally specified still works exactly as it did. The server is additive: point the client at one (Settings → Remote Sync) and processing that used to happen locally happens there instead.

Non-goals, still true: no cloud/third-party API dependencies (the optional server is *yours*, self-hosted, reached over your own Tailscale network — never a public cloud service), no real-time/live overlay, no video frame/OCR parsing, no Discord/third-party scraping (the Discord-bot per-user-capture idea was explicitly declined by the coach in Milestone 6 — see §8).

---

## 2. Current File Structure

```plaintext
R6Analyzer/
│
├── main.py                      <-- client entry point; configures logging FIRST (see §7)
├── server_main.py                <-- server entry point; configures logging FIRST (see §7)
│
├── app/
│   ├── app_controller.py
│   ├── config.py                 <-- CLIENT PATH AUTHORITY (sys.executable resolution)
│   ├── logging_setup.py          <-- NEW (2026-09-06 / 2026-09-11) -- see §7
│   ├── session_manager.py        <-- recording lifecycle, transcription dispatch, sync trigger
│   ├── sync_coordinator.py       <-- NEW (M3) -- upload mode policy, retry/backoff
│   ├── upload_queue.py           <-- NEW (M3) -- on-disk pending-upload queue
│   ├── uploader.py               <-- NEW (M3) -- headless HTTP client to R6Server
│   ├── packaging.py              <-- .r6session zip packaging + verification
│   ├── server_credentials.py     <-- build-time embed target, placeholder in the repo (see §9)
│
├── gui/
│   ├── main_window.py            <-- also handles screen-fit sizing (see §11)
│   ├── dashboard_view.py         <-- team + per-player trend views (M6)
│   ├── match_view.py
│   ├── recording_view.py
│   ├── analysis_view.py          <-- + "Tag Speakers" entry point (M6)
│   ├── export_view.py            <-- + Sync button, Session Queue table (M3)
│   ├── settings_view.py          <-- + Remote Sync tab (M3), player aliases + merge tool (M6)
│   ├── speaker_tagging_dialog.py <-- NEW (M6)
│   ├── db_editor_view.py         <-- still a 0-byte stub; undecided, not built
│
├── database/
│   ├── db_manager.py             <-- accepts db_path/schema_path override (server reuse, M4P2)
│   ├── schema.sql                <-- now includes player_aliases, transcript_speaker_labels (M6)
│   ├── repositories.py           <-- SHARED data-access layer, client + server both import it
│   ├── seed_operators.py
│   ├── migrations.py             <-- V4 migration adds the M6 tables
│
├── integration/
│   ├── bin/r6-dissect.exe
│   ├── obs_controller.py
│   ├── whisper_transcriber.py    <-- + real voice-clustering diarization (M6), no-window audio load (2026-09-10)
│   ├── voice_buffers.py
│   ├── discord_capture.py        <-- optional; per-user capture, NOT the declined per-user bot idea
│   ├── rec_importer.py           <-- kills/deaths join fixed 2026-09-02 (see §11)
│
├── analysis/
│   ├── transcript_parser.py
│   ├── intel_engine.py           <-- SHARED, Ollama-first/llama-cpp-fallback, accepts overrides (M4P2)
│   ├── metrics_engine.py
│   ├── report_generator.py
│   ├── timeline_aligner.py       <-- shared session-start-epoch parsing (M4P1), no-window fix (2026-09-10)
│   ├── match_builder.py          <-- NEW (M4P2) -- shared replay-to-database logic, client+server
│   ├── event_parser.py
│
├── models/                       <-- unchanged from V3.2's plan
│
├── server/                       <-- NEW since V3.2 -- see §5
│   ├── main.py, config.py, auth.py, database.py, match_db.py, repositories.py, storage.py, worker.py
│   ├── api/v1.py
│   ├── services/session_processing.py, package_validation.py
│   ├── static/dashboard.html
│   ├── requirements.txt          <-- kept separate from the client's
│
├── scripts/                      <-- NEW -- app-feature build helpers (not dev tooling, see below)
│   ├── embed_client_credentials.py
│   ├── sync_deployed_server_config.py   <-- NEW (2026-09-10, see §9)
│   ├── setup_tailscale_funnel.py
│
├── build_scripts/                 <-- NEW (2026-09-06) -- build_and_deploy.bat's own internals, see §10
│   ├── env_setup.bat, locate_ffmpeg.bat
│   ├── run_and_stream_to_log.ps1, verify_usb_drive.ps1, sync_deploy_assets.ps1
│
├── laptop_scripts/                <-- NEW (2026-09-10), lives on the LAPTOP not this repo checkout
│   ├── verify_and_repair_smb_share.ps1, setup_laptop_smb_account.ps1
│
├── build_and_deploy.bat           <-- THE one script you run; see §10
├── remote_deploy.bat              <-- SSH target on the main PC, see §10
├── setup_ssh_server.ps1           <-- one-time main-PC setup, see §10
├── R6Analyzer.spec / R6Server.spec
│
├── exports/                       <-- Relative to USB Root
├── data/                          <-- Relative to USB Root (client)
│   ├── matches.db, recordings/, transcripts/, reports/
│   ├── logs/                      <-- NEW (2026-09-06) -- r6analyzer.log, rotating, see §7
│
├── server_data/                   <-- Relative to wherever R6Server.exe was STARTED FROM (see §6's caveat)
│   ├── server_config.json (api_token, public_url), server_matches.db, matches.db, uploads/, work/, reports/
│   ├── models/ (Whisper), ollama/ (portable AI backend)
│   ├── logs/                      <-- NEW (2026-09-06) -- r6server.log, rotating, see §7
│
└── resources/icons/
```

---

## 3. Database Schema

Core tables unchanged from V3.2 (`matches`, `rounds`, `players`, `maps`, `operators`, `gadgets`, `operator_gadget_options`, `player_round_stats`, `round_resources`, `transcripts`, `derived_metrics`). Two tables added in Milestone 6, migration V4:

* **`player_aliases`** — maps other usernames/tags to one canonical `players` row, case-insensitively. Every raw username seen during import is resolved through this before ever creating a new player record, so a tag change or alt account no longer silently creates a duplicate "ghost" player.
* **`transcript_speaker_labels`** — per-match assignment of a diarized `Speaker_N` cluster to a real team player, from the manual "Tag Speakers" step.

`database/repositories.py` is the single shared data-access layer both `R6Analyzer.exe` and `R6Server.exe` import unmodified — client and server can never drift apart on how a replay becomes a match record, because `analysis/match_builder.py` (also shared, extracted in M4P2) is the only place that logic exists.

---

## 4. Operational Modes & Handoff (client)

Unchanged from V3.2's core flow:
1. **Start Recording** → `session_manager` snapshots the R6 Replay folder, starts OBS.
2. **Stop Recording** → folder diff + file-stability lock check.
3. **Processing** → `rec_importer` → (local or deferred) transcription → (local or deferred) AI analysis.
4. **Handoff** → `ImportResult` (`SUCCESS` / `PARTIAL_FAILURE` / `CRITICAL_FAILURE`) routes to Analysis View or Match View, unchanged since V3.2 §10.

What's new since V3.2: step 3's transcription and AI analysis can each be **deferred to the server** instead of running locally — see §5.

---

## 5. Sync & Server Processing (Milestones 3 & 4 — new since V3.2)

### Client sync modes
`app/config.py`'s `analysis_mode` setting: `local` (never uploads, exactly V3.2 behavior), `remote` (uploads whenever a server is configured), `automatic` (the default since M3 pass 3 — uploads automatically the moment a server URL is set, no separate toggle needed). `SyncCoordinator.should_attempt_upload()` is the single source of truth for this policy; `app/upload_queue.py` persists pending uploads to disk (survives a USB drive-letter change or an app restart) with exponential backoff on retry.

### Deferred processing (the actual point of having a server)
The original plan (M3) was "process everything locally, then optionally upload the result." That was replaced (M4) by: if a server is configured and reachable, the client uploads the raw package and does **nothing** else — no local Whisper run, no local AI analysis — so you can unplug the USB and leave almost immediately. `should_defer_transcription_to_server()` in `app/session_manager.py` is the decision function; if the server isn't reachable, the client falls back to full local processing rather than losing data.

The server then runs, on its own machine and its own schedule: `RecImporter` → `match_builder` (same shared code the client uses) → `WhisperTranscriber` → `IntelEngine.analyze_match()` + `.get_player_intel()` — the exact same pipeline the client would have run, just on a machine that stays on. Results land on `server/static/dashboard.html` (a self-contained, no-CDN page) and are pollable via `GET /api/v1/sessions/{id}/{transcript,analysis,summary}`.

**Known real cost:** bundling Whisper (PyTorch) into `R6Server.exe` took its build size from ~8MB to ~4.5GB. This only matters for how you move the server folder around (it's meant to live permanently on the home machine, not travel on the USB) — it has no runtime effect.

**Known unverified gap (carried since M4):** the real end-to-end path (real replay → real transcription model → real Ollama report) has never been exercised outside mocked/unit tests in the dev sandbox this project is built in — only on your own hardware. If a session's analysis keeps coming back `failed: [AI unavailable]`, the portable Ollama install under `server_data/ollama/` most likely hasn't been extracted yet (see `claude/milestone4-server-transcription.md` §4).

---

## 6. Path Authority & Portability

**Client (`app/config.py`) — the rule, followed correctly everywhere in `app/`, `gui/`, `integration/`, `analysis/`:** `BASE_DIR` resolves via `sys.executable`'s parent directory when frozen — never the process's current working directory. `Foundation_V3_2.md`'s original rule ("No module may use `os.getcwd()` or hardcoded strings — all paths pull from `config.py`") still holds and was re-confirmed by a dedicated grep across the whole codebase on 2026-09-10 that found no violations on the client side.

**Server (`server/config.py`) — one confirmed exception, partially mitigated, not fully fixed.** `ServerSettings.DATA_DIR` resolves as `Path(os.getenv("R6_SERVER_DATA_DIR", "./server_data")).resolve()` — relative to the process's *working directory at launch*, not the exe's own location. In practice this means `build_and_deploy.bat` (always run from the project root) and a double-clicked, deployed `R6Server.exe` (run from `dist\R6Server\`) each self-provision their own, independent `server_config.json` — including their own independent API token. This caused a real, reproduced bug on 2026-09-10: a client built against the project root's token could not authenticate against the actually-deployed, actually-running server at all.

**Current mitigation, not a structural fix:** `scripts/sync_deployed_server_config.py`, called by `build_and_deploy.bat` right before it embeds credentials into the client build, copies whatever token (and `public_url`) the deployed `dist\R6Server\server_data\server_config.json` already has into the project root's copy first — so every build embeds the token the actually-running server is using. This was a deliberate choice (of four options presented) over the structural fix of making `DATA_DIR` exe-relative like the client's `BASE_DIR` — worth reconsidering if this class of bug resurfaces in a form the sync script doesn't cover (e.g., the server is later moved to run from yet a third location). Full writeup: `claude/milestone5-internet-reachability.md` §6.

---

## 7. Logging & Observability

**Added 2026-09-06, extended 2026-09-11.** Before this, there was no logging framework anywhere in the codebase — every error path was a bare `print()`, and since the client ships with `console=False` (a genuinely windowed app, `sys.stdout`/`sys.stderr` are `None`), essentially every diagnostic message from a real USB build went nowhere any human could ever see it. This was the top-priority finding of the 2026-09-06 codebase audit.

**`app/logging_setup.py`** fixes this without touching any of the ~150+ existing `print()` call sites:
- `configure_logging(logs_dir, filename)` substitutes `sys.stdout`/`sys.stderr` at the process level with a `_TeeWriter` that forwards every line to both a real console (when one exists — dev runs, the server's visible window) and a `logging.handlers.RotatingFileHandler` (2MB × 5 backups, plain timestamped text lines — meant to be opened and read, not parsed).
- Called as the very first executable code in both entry points, before any other import gets a chance to print: `main.py` → `data/logs/r6analyzer.log` (on the USB, protected from the build's USB-sync `/PURGE` step the same way `matches.db`/`settings.json` already are); `server_main.py` → `server_data/logs/r6server.log` (using `LOGS_DIR`, which existed in `server/config.py` since M4 but was never actually wired to anything until this).
- Never raises — if the log file can't be created (read-only USB, out of space), it quietly does nothing rather than taking the app down over its own diagnostics.

**`pipe_process_to_log(process, tag)`**, added 2026-09-11: the other half of "a log you can actually look over." `configure_logging()` only ever captured this app's *own* print output — a long-running background process the app launches and leaves running unattended is a different case. The one concrete instance of this: the portable Ollama server (`analysis/intel_engine.py`'s `_OllamaBackend`) used to run with `stdout=DEVNULL, stderr=DEVNULL` — if it failed to bind its port or crashed mid-generation, there was no way to ever see why, from the log or anywhere else. It now runs with `stdout=PIPE, stderr=STDOUT`, and a daemon thread feeds its output into the same rotating log file, tagged `[ollama]` so it's identifiable alongside everything else. This is deliberately scoped to processes left running in the background (a server) — a one-shot `subprocess.run()` call whose result the caller already inspects (ffmpeg, r6-dissect) already reports its own outcome and doesn't need this.

**Still on the USB, not pushed anywhere yet** — per your own instruction, this stays local until there's a reason (and a design) to push it to the server. If that's wanted later, the natural next step is an authenticated diagnostic-upload endpoint alongside the existing session-upload one, not a change to how the file itself is written.

**Not yet done:** no in-app "view log" button — reading the file currently means opening `data\logs\r6analyzer.log` directly (e.g., in Notepad, or pasting it into a Claude conversation, which is the primary way this file has been used diagnostically so far this project). Worth adding if it turns out to matter in practice.

---

## 8. Player Identity & Speaker Diarization (Milestone 6 — new since V3.2)

- **Aliases + merge**: `resolve_player_by_username()` checks canonical name then `player_aliases` before ever creating a new player — this is what makes a tag change or alt account attach to the right person instead of quietly forking a duplicate. `merge_player()` folds an already-forked duplicate back into the correct player after the fact (irreversible, confirmation-gated in the GUI).
- **Speaker diarization**: real voice-based clustering (`resemblyzer` d-vector embeddings, CPU-only, fully offline, agglomerative clustering) replaces the original silence-gap-only heuristic, with automatic fallback to that heuristic if the optional dependency isn't installed or there isn't enough usable audio. A one-time-per-match "Tag Speakers" screen assigns each `Speaker_N` cluster to a real player — diarization is a best-effort clustering, never ground truth, which is exactly why that manual step exists.
- **Why not the Discord-bot approach**: raised and explicitly declined by the coach — the team's Discord is the college's, public and monitored, and not everyone consented to a bot capturing their individual voice. A completely reasonable line to hold; the voice-clustering approach above needs nothing from Discord at all.
- **Known real cost**: `resemblyzer` pulls in PyTorch — check the built USB stick's size after a build if this becomes a problem. It also pulls in `webrtcvad`, which drags in the ancient PyPI `typing` backport package as a transitive dependency — see §10's build-pipeline note on why that specific package must never end up in the frozen build.
- **Not done**: server-side transcription does not run diarization at all yet — only the client's local pipeline is wired up.

---

## 9. Credentials & Security Model

- **Server API token**: self-provisioned on first run (`secrets.token_urlsafe(32)`), persisted to `server_config.json`, reused on every later launch (never regenerated automatically). An explicit `R6_SERVER_API_TOKEN`/`_HASH` env var always overrides this, for scripted/CI setups.
- **Embedded into the client at build time**, never typed by hand: `scripts/embed_client_credentials.py --write` overwrites `app/server_credentials.py` with the real token + `public_url` immediately before PyInstaller runs, and `--clear` restores the checked-in placeholder immediately after — success or failure — so a real secret never sits in the working tree longer than one build step. Manually-entered Settings values always take priority over the embedded fallback.
- **Public reachability**: Tailscale Funnel (`setup_tailscale_funnel.py`, one-time setup on whichever machine runs `R6Server.exe`) gives the server a stable public HTTPS address reachable from any network — the client itself never needs Tailscale installed.
- **Dev/build-tooling secrets are a separate concern from the above** — SSH (key-only, Tailscale-range-scoped firewall rule, never password auth) triggers a remote rebuild; the resulting build is pushed to the laptop's SMB share using a **dedicated local Windows account** (`setup_laptop_smb_account.ps1`, 2026-09-10) whose password is generated and stored by the script itself — nobody types it on either machine. This exists because an SSH public-key logon creates a Windows "Network" logon session with no password of its own, so it can never unlock a cached/Credential-Manager secret — explicit plaintext credentials at the point of use are the actual, documented fix (not a workaround).
- **`.gitignore` gap found and fixed 2026-09-10**: `server_data/` (holding the plaintext API token) and the new `laptop_smb_credentials.json` were never actually excluded from git. Both are now ignored. Worth independently checking whether `server_data/server_config.json` was ever actually committed to the GitHub repo's history before this fix — if so, that token should be rotated (deleting the file and letting the server self-provision a fresh one), since git history retains it regardless of a later `.gitignore` entry.

---

## 10. Build & Deploy Pipeline

**`build_and_deploy.bat`** is the one script you run — it always has been, and every reorganization of its internals has deliberately preserved that. It now builds *both* `R6Analyzer.exe` and `R6Server.exe` in one run (M3), stages the safety-checked USB deploy, and — new as of 2026-09-10 — runs `sync_deployed_server_config.py` before embedding credentials (§6/§9).

**`build_scripts\`** (2026-09-06) holds the file's own internal subroutines and PowerShell helpers, split out purely to keep the main file's line count down after it broke twice from cmd.exe's `call :label` lookup becoming unreliable at large file sizes — not a reorganization of what it does, just where the pieces live. Only subroutines using exclusively `exit /b` (never `goto :finish`) were safe to extract this way; `:main`'s own 22 `goto :finish` sites could not be split across files.

**USB safety gates** (2026-09-03, after a real data-loss incident): a hard drive-identity verification (volume label + size ceiling, via `Win32_LogicalDisk` — `Get-Volume` is confirmed blind to some redirected/virtual drives) runs before anything is written, and `settings.json`/`matches.db` are backed up (5 most recent kept) immediately before the deploy step and verified present immediately after, auto-restoring from backup if either went missing. The deploy pipeline itself never seeds or touches those two files at all anymore — the earlier version of `sync_deploy_assets.ps1` doing so is what caused the original incident.

**Every run leaves a log, and every wrapper trusts the log's own marker over its own relayed exit code** — `findstr "Final Status: SUCCESS"` as a tie-breaker, because a hard crash (a bad `cmd.exe` label lookup, a dropped SSH connection) skips straight past a script's own final status lines and can otherwise leave a wrongly-relayed success code. This pattern, established 2026-09-03, is now used by `build_and_deploy.bat`, `setup.bat`, and `remote_deploy.bat` alike.

**Remote rebuild over Tailscale + SSH** (2026-09-04, hardened 2026-09-08/09-10): from the laptop, `rebuild_via_ssh.bat` SSHes into the main PC (Windows OpenSSH Server, key-only, firewall-scoped to the Tailscale range) and runs `remote_deploy.bat`, which calls `build_and_deploy.bat` unmodified (with USB detection skipped via `R6_SKIP_USB_DEPLOY`) and then pushes the result to the laptop's own SMB share. Keepalive settings on both ends (`ClientAliveInterval`/`ServerAliveInterval`) address one class of mid-build disconnect; a laptop-side automatic share verify/repair pre-flight (`verify_and_repair_smb_share.ps1`) and the credential-automation in §9 address the rest of what's been hit so far. **Still not fully exercised end to end**: the SMB push step itself (`net use` + robocopy to the laptop) has never actually succeeded in a real test as of this writing — see `claude/remote-deploy-over-tailscale-ssh.md` for the full incident history.

**Build-tooling fragility, worth remembering for any future edit**: `build_and_deploy.bat` has broken three separate times from distinct, obscure `cmd.exe` quirks (nested-parenthesis parsing, a quoted-path invocation gotcha, `call :label` lookup flakiness at large file sizes) — none were logic bugs, all were cmd.exe itself misbehaving once the file grew large enough. Prefer inlining over adding new shared subroutines when touching this file further, and keep new explanatory comments reasonably tight.

**One real, still-open packaging risk**: `resemblyzer`'s dependency chain can leave the ancient PyPI `typing` backport package sitting in the persistent build venv if the optional diarization-package install ever fails partway — `R6Analyzer.spec`'s exclude list correctly keeps stdlib `typing` from being touched, but a stray *installed* `typing` package bundled by PyInstaller reproduces the exact historical crash through a different door. The one-line fix (run the `pip uninstall -y typing` cleanup unconditionally, not just on the success path) was identified 2026-09-06 and is believed applied via the `env_setup.bat` extraction — worth a final confirmation read of that file's current content next time it's touched.

---

## 11. Fixed-in-production bugs worth knowing about (so they aren't reintroduced)

- **Kills/deaths always showed zero** (fixed 2026-09-02): `rec_importer.py` was looking for stats nested inside each player object; the real r6-dissect schema puts them in a separate top-level `stats` array joined by username, and uses a `died: bool` field, not a `deaths: int`. Fixed via `_build_stats_by_username()`.
- **Dashboard "cut off top and bottom"** (fixed 2026-09-02): not a scroll-area bug — `main_window.py` hardcoded a 1200×750 minimum size with no regard for the actual screen (common on the school/lab computers this app targets). `_fit_to_screen()` now sizes to whatever the screen's available area actually allows, down to a 1024×600 floor.
- **Console window flashing during local transcription** (fixed 2026-09-10): not this codebase's own subprocess calls (all of which already suppress their console window) — openai-whisper's *own* internal audio loader shells out to ffmpeg without suppressing its window. Fixed by loading chunk/per-user audio into a numpy array ourselves (`_load_whisper_audio()` in `whisper_transcriber.py`) so Whisper's internal loader never runs at all.
- **`analysis/timeline_aligner.py`'s r6-dissect call was missing the no-window flag** (fixed 2026-09-10) — the one inconsistency found in an otherwise-universal pattern elsewhere in the codebase.

---

## 12. Known Open Items (as of 2026-09-11)

- `LAPTOP_SMB_USER`/`LAPTOP_SMB_PASS` in `remote_deploy.bat` — automatable via `laptop_smb_credentials.json` (§9), but the file hasn't been generated and copied over yet.
- `rebuild_via_ssh.bat`'s own separate `SHARE_USER` placeholder could read from that same credentials file — not yet wired in (this session doesn't have that file's latest content).
- The SMB push step of the remote-deploy pipeline has never actually succeeded end to end in a real test.
- Server-side real-model transcription and real-Ollama-backend analysis have never been exercised end to end outside your own hardware (see §5).
- `gui/db_editor_view.py` remains an intentional 0-byte stub — undecided, not a bug.
- Whether `server_data/server_config.json` was ever actually committed to git history predating the 2026-09-10 `.gitignore` fix is unconfirmed — worth checking, with a token rotation if so.
- No in-app log viewer yet (§7) — reading the log currently means opening the file directly.
