import time
import json
import threading
from pathlib import Path
from typing import Callable, Optional
from integration.rec_importer import RecImporter
from integration.whisper_transcriber import WhisperTranscriber
from models.import_result import ImportResult, ImportStatus
from models.round_resources import RoundResources
from integration.discord_capture import DiscordCapture
from app.config import settings
from database.repositories import Repository
from models.match import Match, Round
from datetime import datetime
from analysis.transcript_parser import TranscriptParser
from analysis.timeline_aligner import TimelineAligner
from app.packaging import SessionPackage, generate_session_id
from app.upload_queue import UploadQueue, QUEUE_DIR


def should_defer_transcription_to_server(
    analysis_mode: str,
    upload_voice: bool,
    server_url: str,
    server_reachable: bool,
    fallback_to_local_analysis: bool,
) -> tuple[bool, str]:
    """
    Pure decision function for whether end_session() should skip local
    Whisper transcription and let the server's own job worker handle it
    instead — the point of remote mode for a time-pressured use case (e.g.
    an esports team that needs to be out the door right after a match).

    Returns (defer: bool, reason: str) — reason is a human-readable log
    line explaining the decision, so callers don't need to re-derive it.

    Kept side-effect-free and independent of SessionManager so it can be
    unit tested directly against every combination of inputs, rather than
    only indirectly through a full end_session() call.
    """
    if analysis_mode == "local":
        return False, "Local mode — transcription always runs locally."

    if not upload_voice:
        return False, (
            "Remote mode is configured, but 'Include voice/audio recordings' "
            "is off in Settings -> Remote Sync, so the server has no audio to "
            "transcribe — running transcription locally for this session."
        )

    if not server_url:
        return False, "No server_url configured — running transcription locally."

    if server_reachable:
        return True, (
            "Server is reachable — transcription will run there once this "
            "session finishes uploading. Skipping local transcription."
        )

    if fallback_to_local_analysis:
        return False, (
            "Server not reachable right now — falling back to local "
            "transcription for this session."
        )

    return True, (
        "Server not reachable and 'Fall back to local analysis' is off — "
        "this session's transcription stays queued on the server until it "
        "can be uploaded and processed there."
    )


class SessionManager:
    """
    Handles recording session lifecycle.
    Auto-creates a DB match record for every ImportResult that has rounds,
    whether SUCCESS or PARTIAL_FAILURE — so manual entry can always save.
    Creates standardized .r6session archives and registers them in UploadQueue.
    """

    def __init__(
        self,
        replay_folder: Path,
        importer: RecImporter,
        recording_path: Optional[Path] = None,
        transcribe: bool = True,
        stability_wait: float = 5.0,
        stability_checks: int = 4,
    ) -> None:
        self.replay_folder    = replay_folder
        self.importer         = importer
        self.recording_path   = recording_path
        self.transcribe       = transcribe
        self.stability_wait   = stability_wait
        self.stability_checks = stability_checks
        self._snapshot: set[Path] = set()
        self._transcriber: Optional[WhisperTranscriber] = None
        self.upload_queue     = UploadQueue()

        self._discord = DiscordCapture()
        self._discord_user_files: dict[str, Path] = {}

        # Work completed so far in THIS session. Matches are now imported,
        # packaged, and queued for upload as they finish rather than all at
        # once at the stop button, so end_session() has to assemble its
        # results from what earlier passes already did instead of from one
        # batch of its own. See process_pending_matches().
        self._processed_folders: set[Path] = set()
        self._session_folders: list[Path] = []
        self._session_results: list[ImportResult] = []
        self._created_session_ids: list[str] = []
        # Held for the duration of a processing pass. A live pass fired by
        # the session timer and the final pass from end_session() must never
        # overlap, or the same folder gets imported and packaged twice.
        self._process_lock = threading.Lock()
        self._sync_coordinator = None  # type: ignore[var-annotated]

    # =====================================================
    # SESSION START
    # =====================================================

    def start_session(self) -> None:
        self._snapshot = self._scan_match_folders()
        self._start_background_sync()

    def _start_background_sync(self) -> None:
        """
        Keeps retrying queued uploads in the background for as long as the
        app is open.

        SyncCoordinator.start_in_background() already existed and nothing
        ever called it, so the only upload attempt a package ever got was the
        single pass fired right after it was built. One transient failure — a
        sleeping server, a wifi blip — parked it at upload_failed until
        somebody noticed and pressed "Sync Pending Sessions" by hand. That is
        how nine sessions piled up unsent while the retry machinery sat there
        fully written and unused.

        Deliberately shares this manager's UploadQueue instance instead of
        letting the coordinator construct its own: two UploadQueue objects
        over one queue.json each hold the whole dict in memory and write it
        back wholesale, so the one with the stale copy silently erases the
        other's entries.
        """
        from app.sync_coordinator import SyncCoordinator

        if self._sync_coordinator is not None:
            self._sync_coordinator.stop()
        self._sync_coordinator = SyncCoordinator(upload_queue=self.upload_queue)
        self._sync_coordinator.start_in_background()

    def stop_background_sync(self) -> None:
        if self._sync_coordinator is not None:
            self._sync_coordinator.stop()
            self._sync_coordinator = None

    def upload_readiness(self) -> dict:
        """
        Whether everything this client holds has reached the server yet —
        i.e. whether the USB can be pulled without losing anything.

        Reloads the queue from disk first because the background sync thread
        writes it, so an in-memory copy here goes stale within seconds.
        """
        from app.sync_coordinator import SyncCoordinator

        self.upload_queue.load()
        items = self.upload_queue.list_items()
        pending = [i for i in items if i.package_status in ("pending_upload", "upload_failed", "uploading")]
        uploaded = [i for i in items if i.package_status == "uploaded"]

        coordinator = self._sync_coordinator or SyncCoordinator(upload_queue=self.upload_queue)
        return {
            "upload_enabled": coordinator.should_attempt_upload(),
            "pending": len(pending),
            "uploaded": len(uploaded),
            "safe_to_remove": not pending,
            "blocked_reasons": sorted({
                str(i.last_error) for i in pending if i.last_error
            }),
        }

    def start_discord_capture(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:

        token      = str(settings.get("discord_bot_token")  or "")
        channel_id = int(settings.get("discord_channel_id") or 0)

        if not token or not channel_id:
            if log_callback:
                log_callback(
                    "[Discord] Not configured — using heuristic speaker detection. "
                    "Set token and channel ID in Settings → Discord for named speakers."
                )
            return False

        session_name = f"session_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return self._discord.start_capture(token, channel_id, session_name, log_callback)


    def stop_discord_capture(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Path]:
        return self._discord.stop_capture(log_callback)

    # =====================================================
    # SESSION END
    # =====================================================

    def process_pending_matches(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
        quiet_when_idle: bool = False,
        wait_for_running_pass: bool = False,
    ) -> list[ImportResult]:
        """
        Imports, packages, and queues every match folder that has appeared and
        gone stable since the last pass. Returns only what THIS pass handled.

        Designed to be called repeatedly while the session is still running,
        so each match is on its way to the server minutes after it ends
        instead of everything being done in one burst after the stop button.
        That burst was the whole reason stopping took so long: a session's
        entire import, audio slicing, and upload had nowhere to happen except
        after you asked to leave.

        Idempotent by folder: anything already handled is tracked in
        _processed_folders and never picked up twice, so the final pass from
        end_session() only does whatever the live passes had not got to yet
        (a match still in progress at the stop button, or one that had not
        finished being written).

        quiet_when_idle suppresses the "nothing found" chatter for the
        periodic background passes, where finding nothing is the normal case.

        wait_for_running_pass makes the call block until any pass already in
        flight has finished instead of skipping. end_session() needs that:
        skipping there would let it read _session_results before a live pass
        still mid-import had added its own, silently dropping a match from
        the session.
        """
        def log(msg: str) -> None:
            print(f"[SessionManager] {msg}")
            if status_callback:
                status_callback(msg)

        # A pass already running means the timer fired again while the last
        # one was still importing. Skip rather than queue up behind it; the
        # next tick will pick up whatever this one does not.
        if not self._process_lock.acquire(blocking=wait_for_running_pass):
            return []

        try:
            current_folders = self._scan_match_folders()
            new_folders = current_folders - self._snapshot - self._processed_folders

            # The newest match folder may still be mid-match. R6 writes each
            # round's .rec only when that round ENDS, so between rounds a
            # half-played match's folder sits unchanged for minutes and
            # passes the size-stability check below -- importing it then
            # would record just the rounds played so far, and it would never
            # be looked at again. So during play, a match only counts as
            # finished once a newer match folder exists; the final pass at
            # the stop button (wait_for_running_pass=True) takes the newest
            # one too, since by then nothing more is being written.
            if not wait_for_running_pass and current_folders:
                newest = max(current_folders, key=self._folder_created_at)
                if newest in new_folders:
                    new_folders = new_folders - {newest}

            if not new_folders:
                if not quiet_when_idle:
                    log("No new match folders found.")
                return []

            stable_folders = self._filter_stable_folders(new_folders)

            if not stable_folders:
                if not quiet_when_idle:
                    log("Folders found but not yet stable.")
                return []

            log(f"Found {len(stable_folders)} match folder(s) ready — importing now...")
            results = self._import_and_package(stable_folders, log)

            # Only mark folders handled once the pass actually completed, so
            # a crash mid-pass leaves them to be retried rather than lost.
            self._processed_folders.update(stable_folders)
            self._session_folders.extend(stable_folders)
            self._session_results.extend(results)
            return results
        finally:
            self._process_lock.release()

    @staticmethod
    def _clip_match_audio(pieces, segments, ffmpeg_path, session_id, result, catalog_db,
                          temp_paths: list[Path]) -> tuple[list[Path], dict]:
        """
        Cuts one match's audio out of the OBS recording(s).

        When routing was confirmed for every recording file involved (see
        OBSController.ensure_comms_tracks), the Discord track ("team":
        everyone else) and your mic ("self") are cut as two separate files, so
        the server knows whose voice every line is. Otherwise -- recordings
        made before this, or when OBS setup failed -- one clip from the
        default track, as before.

        The metadata says where the clip sits in time (epoch start of each
        piece), your UTC offset (replay timestamps are local time), whose PC
        recorded it, and your roster, so the server can put every line of
        speech on the same clock as the kill feed and name the speakers.
        """
        import tempfile as _tempfile
        from integration.obs_controller import track_layout_for
        from integration.whisper_transcriber import extract_match_audio

        seg_start = {path: start for start, _end, path in segments}
        layouts = [track_layout_for(seg_start.get(path, 0.0)) for path, _, _ in pieces]
        split = all(l is not None for l in layouts)

        out_dir = Path(_tempfile.mkdtemp(prefix=f"{session_id}_audio_"))
        temp_paths.append(out_dir)
        files: list[Path] = []
        if split:
            for name in ("team", "self"):
                clip = out_dir / f"{name}.m4a"
                if extract_match_audio(ffmpeg_path, pieces, clip, stream_index=layouts[0][name]):
                    files.append(clip)
            if len(files) < 2:
                split, files = False, []
        if not split:
            clip = out_dir / "mixed.m4a"
            if extract_match_audio(ffmpeg_path, pieces, clip):
                files.append(clip)

        first_epoch = seg_start.get(pieces[0][0], 0.0) + pieces[0][1]
        timeline_rounds = getattr(result, "timeline_rounds", None) or []
        roster: dict[str, str] = {}
        try:
            with catalog_db.get_connection() as conn:
                for name, alias in conn.execute(
                    """SELECT p.name, a.alias FROM players p
                       LEFT JOIN player_aliases a ON a.player_id = p.player_id
                       WHERE p.is_team_member = 1"""
                ):
                    roster[str(name).lower()] = str(name)
                    if alias:
                        roster[str(alias).lower()] = str(name)
        except Exception:
            pass
        audio_meta = {
            "layout": "split" if split else "mixed",
            "pieces": [[round(seg_start.get(path, 0.0) + off, 3), round(dur, 3)] for path, off, dur in pieces],
            "utc_offset_sec": time.localtime(first_epoch).tm_gmtoff,
            "self_username": next((r.get("recording_username") for r in timeline_rounds
                                   if r.get("recording_username")), ""),
            "roster": roster,
        }
        return files, audio_meta

    def _import_and_package(
        self,
        stable_folders: list[Path],
        log: Callable[[str], None],
    ) -> list[ImportResult]:
        """One batch: parse the replays, build a .r6session per match, queue
        it for upload, and write the local match records."""
        log(f"Importing {len(stable_folders)} stable folder(s)...")
        self.importer._log = log
        results = self.importer.import_multiple_folders(
            stable_folders, log_callback=log
        )

        # ── Learn new operators/maps before anything uses the names ──
        # Done here, not only in match creation below, so the package's
        # metadata and the "name this map" prompt both see resolved names.
        from database.game_catalog import learn_from_import, client_hints_for
        catalog_db = Repository().db
        for result in results:
            try:
                outcome = learn_from_import(catalog_db, result, log=log)
                log(f"  Map: {outcome.map_name}"
                    + ("  ⚑ needs a name -- you'll be asked after import" if outcome.map_needs_name else ""))
            except Exception as cat_err:
                log(f"  [catalog] Could not update the game catalog (non-fatal): {cat_err}")

        # ── Package sessions into .r6session archives ─────────────
        log("Packaging session archives (.r6session)...")
        from app.packaging import generate_source_fingerprint
        created_session_ids: list[str] = []

        # 2026-09-15 fix: a real overnight run surfaced this the hard way --
        # one continuous OBS recording spanning N match folders used to get
        # its ENTIRE file (self.recording_path) copied into every single
        # one of those N .r6session packages below (full duplication of a
        # multi-GB file, once per match), which is exactly what exhausted a
        # USB stick's free space and failed every packaging attempt in one
        # batch, back to back. Fix: slice out just each match's own time
        # window before embedding it, using the exact same TimelineAligner
        # window computation _run_transcription() already
        # uses to clip that match's *transcript* out of the same recording
        # -- so the audio a package carries and the transcript window it's
        # meant to line up with are computed the same way. Reuses
        # _extract_audio_chunk/_find_ffmpeg from whisper_transcriber.py
        # rather than a second hand-written ffmpeg invocation -- it already
        # downmixes to mono/16kHz, which also means each package's audio is
        # far smaller than a full raw recording, not just non-duplicated.
        _voice_upload_active = bool(
            settings.UPLOAD_VOICE and self.recording_path and self.recording_path.exists()
        )
        # 2026-09-24 fix: OBS splits a long recording into several files (every
        # ~4 GiB here), so a match can live in any of them -- or straddle two.
        # Windows are now worked out as absolute wall-clock time and mapped
        # onto whichever recording files cover them, instead of assuming
        # self.recording_path is the one file that holds everything. (That
        # assumption produced truncated clips, empty clips, and -- at session
        # end, when recording_path became the LAST split -- a clip of the wrong
        # ten minutes of audio.) The clip is also AAC now, not WAV: ~57 MB per
        # 30-minute match was timing out mid-upload.
        _pkg_aligner: Optional[TimelineAligner] = None
        _pkg_segments: list[tuple[float, float, Path]] = []
        _pkg_ffmpeg_path: Optional[Path] = None
        _pkg_temp_audio_files: list[Path] = []

        if _voice_upload_active:
            from integration.whisper_transcriber import _find_ffmpeg, extract_match_audio
            _pkg_aligner = TimelineAligner()
            _pkg_segments = TimelineAligner.recording_segments(self.recording_path)
            _pkg_ffmpeg_path = _find_ffmpeg()
            if _pkg_ffmpeg_path is None:
                log("  Voice upload is on but ffmpeg wasn't found — "
                    "packages in this batch will have no audio.")
                _voice_upload_active = False

        for folder, result in zip(stable_folders, results):
            rec_files = sorted(folder.glob("*.rec"))
            session_id = generate_session_id()
            source_fp = generate_source_fingerprint(folder.name, rec_files)

            meta = {
                "map_name": result.map_name or "Unknown",
                "score_us": result.score_us,
                "score_them": result.score_them,
                "folder_name": folder.name,
                "client_name": settings.CLIENT_NAME,
                "timestamp": datetime.now().isoformat(),
            }
            # Names this client already knows for the match's in-game IDs,
            # so the server's catalog learns them too -- including a map
            # you named here that the server has never seen named.
            try:
                meta["catalog"] = client_hints_for(catalog_db, result)
            except Exception:
                pass

            telemetry = {
                "rounds_parsed": len(result.rounds),
                "error_message": result.error_message,
                "import_status": result.status.value,
            }

            is_comp = (result.status == ImportStatus.SUCCESS)

            # Opt-in voice uploads — sliced to this match's own window, not
            # the whole shared recording (see 2026-09-15 comment above).
            audio_files: list[Path] = []
            if _voice_upload_active:
                try:
                    abs_start, abs_end = _pkg_aligner.get_match_window_abs(
                        folder, result.round_timestamps
                    )
                    pieces = TimelineAligner.audio_pieces(_pkg_segments, abs_start, abs_end)
                    if not pieces:
                        log(f"  No recording file covers {folder.name}'s time window — "
                            f"packaging without audio for this match.")
                    else:
                        audio_files, audio_meta = self._clip_match_audio(
                            pieces, _pkg_segments, _pkg_ffmpeg_path, session_id, result,
                            catalog_db, _pkg_temp_audio_files,
                        )
                        if audio_files:
                            meta["audio"] = audio_meta
                            total_sec = sum(d for _, _, d in pieces)
                            size_mb = sum(p.stat().st_size for p in audio_files) / (1024 * 1024)
                            tracks = ("your mic + Discord as separate tracks"
                                      if audio_meta["layout"] == "split" else "one mixed track")
                            log(f"  Audio clip for {folder.name}: {total_sec:.0f}s from "
                                f"{len(pieces)} recording file(s), {tracks} ({size_mb:.1f} MB)")
                        else:
                            log(f"  Could not clip audio for {folder.name} — "
                                f"packaging without audio for this match.")
                except Exception as clip_err:
                    log(f"  Audio clip failed for {folder.name}: {clip_err} — "
                        f"packaging without audio for this match.")

            try:
                pkg_path = SessionPackage.create_package(
                    output_dir=QUEUE_DIR,
                    session_id=session_id,
                    rec_files=rec_files,
                    metadata=meta,
                    telemetry=telemetry,
                    audio_files=audio_files if settings.UPLOAD_VOICE else None,
                    is_complete=is_comp,
                    source_fingerprint=source_fp,
                )
                log(f"  ✓ Package created: {pkg_path.name}")

                pkg_status = "created" if settings.ANALYSIS_MODE == "local" else "pending_upload"
                if not is_comp:
                    pkg_status = "partial_data"

                self.upload_queue.add_item(
                    session_id=session_id,
                    package_path=pkg_path,
                    package_status=pkg_status,
                    local_analysis_status="processing",
                    source_fingerprint=source_fp,
                )
                created_session_ids.append(session_id)

            except Exception as pkg_err:
                log(f"  ✗ Failed to create package for {folder.name}: {pkg_err}")

        # Clean up the temporary per-match audio clips created above --
        # SessionPackage.create_package() already copied whichever ones
        # succeeded into their .r6session zip, so nothing is lost by
        # removing the loose temp files now regardless of whether that
        # particular package succeeded or failed.
        for _clip in _pkg_temp_audio_files:
            try:
                if _clip.is_dir():
                    import shutil as _shutil
                    _shutil.rmtree(_clip, ignore_errors=True)
                else:
                    _clip.unlink()
            except Exception:
                pass

        # ── Create match records in local DB ──────────────────────
        log("Creating local match records...")
        self._auto_create_matches(results, log)

        # Local analysis for this batch is finished the moment its records
        # are written — the upload leg below is independent of it and never
        # substitutes for it.
        for sid in created_session_ids:
            self.upload_queue.update_item(sid, local_analysis_status="completed")

        self._created_session_ids.extend(created_session_ids)
        self._request_upload(created_session_ids, log)
        return results

    def _request_upload(
        self,
        created_session_ids: list[str],
        log: Callable[[str], None],
    ) -> None:
        """
        Kicks off an upload pass for a freshly packaged batch, in the
        background so it never holds up the next match being played.

        Anything not uploaded here (local mode, automatic-without-server, or
        a failed attempt) simply stays queued for the periodic sync pass or a
        manual "Sync Pending Sessions".
        """
        if not created_session_ids:
            return
        try:
            from app.sync_coordinator import SyncCoordinator
            # Reuse the session's coordinator so both share one UploadQueue
            # instance (see _start_background_sync). This immediate pass only
            # exists so a just-finished match starts uploading now instead of
            # waiting out the background loop's poll interval.
            coordinator = self._sync_coordinator or SyncCoordinator(
                upload_queue=self.upload_queue
            )
            if coordinator.should_attempt_upload():
                def _sync_bg() -> None:
                    try:
                        coordinator.sync_once()
                    except Exception as sync_err:
                        log(f"Background sync error (non-fatal): {sync_err}")

                threading.Thread(target=_sync_bg, daemon=True, name="SessionSync").start()
            elif settings.ANALYSIS_MODE != "local":
                log(
                    "Remote upload not attempted (mode configured but "
                    "server_url/upload_automatically not both set) — package(s) remain "
                    "queued for manual or background sync."
                )
        except Exception as sync_init_err:
            log(f"Sync coordinator initialization failed (non-fatal): {sync_init_err}")

    # =====================================================
    # SESSION END
    # =====================================================

    def end_session(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> list[ImportResult]:
        """
        Finishes the session: sweeps up any match not already handled by the
        live passes, then does the work that genuinely can only happen once
        the recording has stopped (transcription of the whole session) and
        reclaims disk space.
        """
        def log(msg: str) -> None:
            print(f"[SessionManager] {msg}")
            if status_callback:
                status_callback(msg)

        # Final sweep. During a live session most matches are already done by
        # now; typically this only catches the one that was still being
        # written when you hit stop.
        log("Finishing up — checking for matches not yet processed...")
        self.process_pending_matches(
            status_callback=status_callback,
            wait_for_running_pass=True,
        )

        results = list(self._session_results)
        stable_folders = list(self._session_folders)
        created_session_ids = list(self._created_session_ids)

        if not results:
            log("No new match folders found.")
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="No new match folders detected since session start.",
            )]

        # ── Decide whether to defer transcription to the server ───
        # When a server is configured, reachable right now, and this
        # session's package includes audio, hand transcription off to the
        # server's own job worker instead of running Whisper here. This is
        # the point of remote mode for a time-pressured use case: the client
        # can finish and be unplugged immediately, while the server (which
        # never gets shut off mid-session) takes as long as it needs.
        defer_transcription_to_server = False
        if self.transcribe and self.recording_path and settings.ANALYSIS_MODE != "local":
            reachable = False
            if settings.UPLOAD_VOICE and settings.SERVER_URL:
                try:
                    from app.uploader import SessionUploader
                    reachable = SessionUploader().test_connection().success
                except Exception as probe_err:
                    log(f"Server reachability check failed (non-fatal): {probe_err}")
                    reachable = False

            defer_transcription_to_server, reason = should_defer_transcription_to_server(
                analysis_mode=settings.ANALYSIS_MODE,
                upload_voice=settings.UPLOAD_VOICE,
                server_url=settings.SERVER_URL,
                server_reachable=reachable,
                fallback_to_local_analysis=settings.FALLBACK_TO_LOCAL_ANALYSIS,
            )
            log(reason)

        # ── Start transcription in background — don't block ──────
        if self.transcribe and self.recording_path and not defer_transcription_to_server:
            log("Starting transcription in background (will not block import)...")

            def _transcribe_bg() -> None:
                try:
                    self._run_transcription(results, stable_folders, log_callback=log)
                except Exception as e:
                    log(f"Transcription error (non-fatal): {e}")

            t = threading.Thread(target=_transcribe_bg, daemon=True, name="Transcription")
            t.start()
            # Don't join — let it run in background
        elif self.transcribe and not self.recording_path:
            log("No recording path — skipping transcription.")
        elif self.transcribe and defer_transcription_to_server:
            log("Transcription deferred to the server for this session.")

        # ── Reclaim USB space from old recordings ────────────────
        # This has always existed and was never once called, which is how a
        # 64GB stick ended up holding every recording back to May and hit
        # 0 bytes free mid-import (failing packaging, the database writes,
        # and the transcript export in one go). Runs last so nothing above
        # is affected, and keeping the newest 3 always spares this session's
        # own recording -- which the transcription thread launched above is
        # still reading from right now.
        try:
            self.cleanup_old_recordings(log_callback=log)
        except Exception as cleanup_err:
            log(f"Recording cleanup error (non-fatal): {cleanup_err}")

        return results

    # =====================================================
    # AUTO-CREATE MATCH RECORDS
    # =====================================================

    def _auto_create_matches(
        self,
        results: list[ImportResult],
        log: Callable[[str], None],
    ) -> None:
        """
        Creates match records, saves rounds, player stats, and round events.

        Delegates to analysis/match_builder.py, which holds this exact logic
        standalone (Qt-free) so the server's Milestone 4 phase 2 pipeline can
        build an identical match record from a session's bundled .rec files
        without a second, drifting copy of it. See that module for details.
        """
        from analysis.match_builder import build_matches_from_import_results

        repo = Repository()
        build_matches_from_import_results(
            repo,
            results,
            recording_path=str(self.recording_path) if self.recording_path else None,
            log=log,
        )

    def _save_raw_player_stats(
        self,
        repo: "Repository",
        round_id: int,
        round_obj: "Round",
        log: Callable[[str], None],
    ) -> int:
        """Thin back-compat wrapper — see analysis/match_builder.save_raw_player_stats."""
        from analysis.match_builder import save_raw_player_stats

        return save_raw_player_stats(repo, round_id, round_obj, log)

    # =====================================================
    # TRANSCRIPTION
    # =====================================================

    def _run_transcription(
        self,
        results: list[ImportResult],
        folders: list[Path],
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        from app.config import TRANSCRIPTS_DIR

        if self._transcriber is None:
            if log_callback:
                log_callback("Loading Whisper model...")
            self._transcriber = WhisperTranscriber()

        if self.recording_path is None:
            return

        # ── Step 1: Transcribe the FULL recording once ────────────
        if log_callback:
            log_callback("Transcribing full recording (this may take several minutes)...")

        full_result = self._transcriber.transcribe_full(
            self.recording_path,
            progress_callback=log_callback,
        )

        # ── Step 2: Diarize speakers across full recording ────────
        if log_callback:
            log_callback("Analyzing speaker patterns...")

        all_segments = full_result.get("segments", [])

        # 2026-09-11 fix: was hardcoded n_speakers=5 (assuming a full
        # 5-stack every session) -- now caps at however many team players
        # are actually configured in Settings, falling back to 5 (R6's own
        # team size) if none are set up yet, so a fresh install behaves
        # the same as before. This is a CEILING passed through to
        # diarize_speakers(), not a target it forces -- see the 2026-09-11
        # note in whisper_transcriber.py's _cluster_segments_by_voice() for
        # how the actual speaker-count auto-detection works underneath it.
        try:
            roster_size = len(Repository().get_team_players())
        except Exception:
            roster_size = 0
        speaker_cap = roster_size if roster_size > 0 else 5

        speakers = self._transcriber.diarize_speakers(
            all_segments, n_speakers=speaker_cap, audio_path=self.recording_path,
        )

        if log_callback:
            for spk, data in speakers.items():
                log_callback(
                    f"  {spk}: {data['word_count']} words | "
                    f"{data['talk_time']:.0f}s talk time"
                )

        # ── Step 3: Get session start time ───────────────────────
        # Shared with the server-side transcription pipeline (see
        # server/services/session_processing.py) via TimelineAligner's
        # static helper, so a session processed remotely aligns audio
        # to matches identically to one processed here locally.
        session_start_epoch: Optional[float] = None

        if self.recording_path and self.recording_path.exists():
            session_start_epoch = TimelineAligner.parse_session_start_epoch(
                self.recording_path
            )
            if session_start_epoch is not None and log_callback:
                import datetime as _dt
                readable = _dt.datetime.fromtimestamp(
                    session_start_epoch
                ).strftime("%Y-%m-%d %H:%M:%S")
                log_callback(f"Session start: {readable}")
            elif log_callback:
                log_callback("Could not determine session start time.")

        # ── Step 4: Clip + store per-match transcript ─────────────
        aligner  = TimelineAligner()
        parser   = TranscriptParser()
        repo     = Repository()
        match_clips: list[dict] = []

        for i, (result, folder) in enumerate(zip(results, folders)):
            clipped: dict = {"text": "", "segments": []}
            start_sec, end_sec = 0.0, 0.0

            try:
                start_sec, end_sec = aligner.get_match_window(
                    folder, session_start_epoch
                )
                clipped = self._transcriber.clip_to_match(
                    full_result, start_sec, end_sec
                )
                if log_callback:
                    word_count = len(clipped.get("text", "").split())
                    log_callback(
                        f"Match {i+1} transcript: "
                        f"{start_sec:.0f}s – {end_sec:.0f}s "
                        f"({word_count} words)"
                    )
            except Exception as e:
                if log_callback:
                    log_callback(f"Clip failed for {folder.name}: {e}")

            text     = clipped.get("text", "")
            segments = clipped.get("segments", [])

            # Parse callouts for this match window
            parsed  = parser.parse_segments_list(segments, match_id=result.match_id)

            # Build storage dict including speaker data — clipped to this
            # match's own window (Milestone 6 fix: `speakers` above covers
            # the whole session's recording, so without clipping every
            # match in a multi-match session stored identical session-wide
            # speaker stats instead of its own). Segments are kept (not
            # just the word_count/talk_time/top_words aggregates) so the
            # speaker-tagging screen has actual lines to show per speaker.
            storage = parser.to_storage_dict(parsed)
            match_speakers = self._transcriber.clip_speakers_to_match(
                speakers, start_sec, end_sec
            )
            storage["speakers"] = {
                spk: {
                    "word_count": data["word_count"],
                    "talk_time":  round(data["talk_time"], 1),
                    "top_words":  data["top_words"][:10],
                    "segments":   data["segments"],
                }
                for spk, data in match_speakers.items()
            }

            match_clips.append({
                "match_id":  result.match_id,
                "start_sec": start_sec,
                "end_sec":   end_sec,
                "text":      text,
            })

            if result.match_id is not None:
                try:
                    with repo.db.get_connection() as conn:
                        conn.execute(
                            """INSERT INTO transcripts
                                (match_id, raw_text, processed_segments_json)
                            VALUES (?, ?, ?)
                            ON CONFLICT DO NOTHING""",
                            (result.match_id, text, json.dumps(storage))
                        )
                        conn.commit()
                except Exception as db_err:
                    print(f"[SessionManager] Transcript store failed: {db_err}")

            result.transcript_text     = text
            result.transcript_segments = segments

        # ── Step 5: Per-user transcription (Discord audio) ────────
        per_user_attributed: list[dict] = []
        user_names = self._discord.get_user_names()

        if self._discord_user_files:
            if log_callback:
                log_callback(
                    f"Transcribing {len(self._discord_user_files)} "
                    "Discord speaker tracks..."
                )
            per_user_results = self._transcriber.transcribe_per_user(
                self._discord_user_files,
                progress_callback=log_callback,
            )
            per_user_attributed = self._transcriber.build_attributed_transcript(
                per_user_results
            )

            # Save full attributed transcript
            if per_user_attributed and self.recording_path:
                session_name = self.recording_path.stem.replace(" ", "_")
                attr_path    = TRANSCRIPTS_DIR / f"session_{session_name}_speakers.txt"
                attr_path.write_text(
                    self._transcriber.format_attributed_transcript(per_user_attributed),
                    encoding="utf-8",
                )
                if log_callback:
                    log_callback(f"Speaker transcript → {attr_path.name}")

        # ── Step 5: Export full transcript TXT ───────────────────
        try:
            
            session_name = self.recording_path.stem.replace(" ", "_")
            full_txt_path = TRANSCRIPTS_DIR / f"session_{session_name}_full.txt"
            self._transcriber.export_full_transcript(
                full_result,
                match_clips,
                full_txt_path,
                speakers=speakers,
            )
            if log_callback:
                log_callback(f"Full transcript exported → {full_txt_path.name}")
        except Exception as e:
            if log_callback:
                log_callback(f"Full transcript export failed: {e}")

    # =====================================================
    # FOLDER SCAN
    # =====================================================

    def _scan_match_folders(self) -> set[Path]:
        if not self.replay_folder.exists():
            return set()
        return {
            p for p in self.replay_folder.iterdir()
            if p.is_dir() and p.name.startswith("Match-")
        }

    # =====================================================
    # STABILITY CHECKS
    # =====================================================

    def _filter_stable_folders(self, folders: set[Path]) -> list[Path]:
        return [f for f in folders if self._is_folder_stable(f)]

    # A folder whose newest .rec hasn't been touched for this long is done
    # being written. R6 writes each round file in one go at the end of the
    # round, so this is a safe margin.
    SETTLED_AFTER_SEC = 45

    def _is_folder_stable(self, folder: Path) -> bool:
        # Fast path: matches that finished a while ago are stable right now,
        # instead of costing stability_wait seconds of sleeping each.
        try:
            recs = list(folder.glob("*.rec"))
            newest = max((f.stat().st_mtime for f in recs), default=None)
        except OSError:
            newest = None
        if newest is not None and time.time() - newest >= self.SETTLED_AFTER_SEC:
            return True

        previous_size = -1
        for _ in range(self.stability_checks):
            if not folder.exists():
                return False
            current_size = self._get_folder_rec_size(folder)
            if current_size == previous_size and current_size > 0:
                return True
            previous_size = current_size
            time.sleep(self.stability_wait)
        return False

    @staticmethod
    def _folder_created_at(folder: Path) -> float:
        try:
            return folder.stat().st_ctime  # creation time on Windows
        except OSError:
            return 0.0

    def _get_folder_rec_size(self, folder: Path) -> int:
        return sum(
            f.stat().st_size
            for f in folder.glob("*.rec")
            if f.exists()
        )
    
    def cleanup_old_recordings(
        self,
        keep_latest_n: int = 3,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        """
        Deletes old recording files to free USB space.
        Keeps the most recent `keep_latest_n` recordings.
        Returns number of files deleted.
        """
        from app.config import RECORDINGS_DIR

        def log(msg: str) -> None:
            print(f"[Cleanup] {msg}")
            if log_callback:
                log_callback(msg)

        recordings = sorted(
            [
                f for f in RECORDINGS_DIR.glob("*.mp4")
                if f.is_file()
            ] + [
                f for f in RECORDINGS_DIR.glob("*.mkv")
                if f.is_file()
            ],
            key=lambda f: f.stat().st_mtime,
            reverse=True,   # newest first
        )

        if len(recordings) <= keep_latest_n:
            log(
                f"Only {len(recordings)} recording(s) found — "
                f"nothing to delete (keeping {keep_latest_n})."
            )
            return 0

        to_delete = recordings[keep_latest_n:]
        deleted   = 0
        freed_mb  = 0.0

        for f in to_delete:
            try:
                mb = f.stat().st_size / (1024 * 1024)
                f.unlink()
                log(f"Deleted: {f.name} ({mb:.0f} MB)")
                deleted += 1
                freed_mb += mb
            except Exception as e:
                log(f"Could not delete {f.name}: {e}")

        log(f"Cleanup complete: {deleted} file(s) deleted, {freed_mb / 1024:.1f} GB freed.")
        return deleted


    def get_storage_usage(self) -> dict:
        """Returns dict with storage info for the USB drive."""
        from app.config import BASE_DIR, RECORDINGS_DIR, DATA_DIR
        import shutil

        result: dict = {}

        try:
            usage = shutil.disk_usage(str(BASE_DIR))
            result["total_gb"]   = round(usage.total / (1024**3), 1)
            result["used_gb"]    = round(usage.used  / (1024**3), 1)
            result["free_gb"]    = round(usage.free  / (1024**3), 1)
            result["percent_used"] = round(usage.used / usage.total * 100, 1)
        except Exception:
            result["error"] = "Could not read disk usage"

        # Recording sizes
        try:
            recordings = list(RECORDINGS_DIR.glob("*.mp4")) + \
                        list(RECORDINGS_DIR.glob("*.mkv"))
            result["recording_count"] = len(recordings)
            result["recordings_gb"]   = round(
                sum(f.stat().st_size for f in recordings) / (1024**3), 2
            )
        except Exception:
            result["recording_count"] = 0
            result["recordings_gb"]   = 0.0

        return result