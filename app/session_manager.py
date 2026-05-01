import time
import json
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
from app.config import TRANSCRIPTS_DIR

        
        
        


class SessionManager:
    """
    Handles recording session lifecycle.
    Auto-creates a DB match record for every ImportResult that has rounds,
    whether SUCCESS or PARTIAL_FAILURE — so manual entry can always save.
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
        
        self._discord = DiscordCapture()
        self._discord_user_files: dict[str, Path] = {}

    # =====================================================
    # SESSION START
    # =====================================================

    def start_session(self) -> None:
        self._snapshot = self._scan_match_folders()

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

    def end_session(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> list[ImportResult]:
        import threading

        def log(msg: str) -> None:
            print(f"[SessionManager] {msg}")
            if status_callback:
                status_callback(msg)

        log("Scanning for new match folders...")
        current_folders = self._scan_match_folders()
        new_folders     = current_folders - self._snapshot

        if not new_folders:
            log("No new match folders found.")
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="No new match folders detected since session start.",
            )]

        log(f"Found {len(new_folders)} new folder(s). Checking stability...")
        stable_folders = self._filter_stable_folders(new_folders)

        if not stable_folders:
            log("Folders found but not yet stable.")
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="New folders found but none were stable enough to read.",
            )]

        log(f"Importing {len(stable_folders)} stable folder(s)...")
        self.importer._log = log
        results = self.importer.import_multiple_folders(
            stable_folders, log_callback=log
        )

        # ── Create match records immediately ─────────────────────
        log("Creating match records...")
        self._auto_create_matches(results, log)

        # ── Start transcription in background — don't block ──────
        if self.transcribe and self.recording_path:
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
        For every result that has at least one parsed round, create a match record
        in the DB and save all round data including player stats from the replay.
        Works for both SUCCESS and PARTIAL_FAILURE.
        """
        repo = Repository()

        for result in results:
            if not result.rounds:
                log("  Skipping match creation — no rounds parsed.")
                continue
            if result.match_id is not None:
                log(f"  Match {result.match_id} already exists.")
                continue

            map_name = result.map_name or "Unknown"
            if map_name and map_name.startswith("Map("):
                map_name = "Unknown"

            try:
                map_id = repo.get_map_id_by_name(map_name)

                match = Match(
                    match_id=None,
                    datetime_played=datetime.now(),
                    opponent_name="Imported",
                    map=map_name,
                    result=None,
                    recording_path=str(self.recording_path)
                        if self.recording_path else None,
                    rounds=[],
                )
                match_id = repo.insert_match(match)
                result.match_id = match_id
                result.map_id   = map_id

                total_stats_saved = 0

                for round_obj in result.rounds:
                    round_obj.match_id = match_id

                    resources = RoundResources(
                        resource_id=None,
                        round_id=0,
                        side=round_obj.side,
                        team_drones_start=10,
                        team_drones_lost=0,
                        team_reinforcements_start=10,
                        team_reinforcements_used=0,
                    )
                    round_id = repo.insert_round(round_obj, match_id)
                    repo.insert_round_resources(resources, round_id)

                    # ── Save player stats from replay data ────────────
                    stats_saved = self._save_raw_player_stats(
                        repo, round_id, round_obj, log
                    )
                    total_stats_saved += stats_saved

                log(
                    f"  ✓ Created match {match_id}: {map_name} "
                    f"({len(result.rounds)} rounds, {total_stats_saved} player stat rows)"
                )

            except Exception as e:
                log(f"  ✗ Failed to create match record: {e}")

    def _save_raw_player_stats(
        self,
        repo: "Repository",
        round_id: int,
        round_obj: "Round",
        log: Callable[[str], None],
    ) -> int:
        """
        Converts raw_player_stats dicts from the replay into PlayerRoundStats
        records in the database. Returns the number of rows saved.

        For players on our team: match against team_players by Ubisoft username
        (case-insensitive). If no match found, still save using username as name.
        For opponent players: always saved as non-team-member guests.
        Stats available from replay: kills, deaths, assists, operator name.
        Everything else (engagements, gadget, ability) defaults to 0/None.
        """
        if not round_obj.raw_player_stats:
            return 0

        saved = 0

        # Pre-load team players for name matching
        team_players = repo.get_team_players()
        team_name_map = {p.name.lower(): p for p in team_players}

        for raw in round_obj.raw_player_stats:
            username   = raw.get("username", "")
            op_name    = raw.get("operator", "")
            kills      = int(raw.get("kills",   0))
            deaths     = int(raw.get("deaths",  0))
            assists    = int(raw.get("assists", 0))
            is_our_team = bool(raw.get("is_our_team", False))

            # ── Resolve player ────────────────────────────────────
            player = None

            if is_our_team and username:
                # Try exact match first, then case-insensitive
                player = team_name_map.get(username.lower())

            if player is None:
                # Look up by username in players table (may already exist from prior imports)
                with repo.db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT * FROM players WHERE LOWER(name) = LOWER(?)",
                        (username,)
                    ).fetchone()
                if row:
                    from models.player import Player
                    player = Player(
                        player_id=row["player_id"],
                        name=row["name"],
                        is_team_member=bool(row["is_team_member"]),
                    )

            if player is None and username:
                # Create as a new non-team player
                from models.player import Player
                new_player = Player(
                    player_id=None,
                    name=username,
                    is_team_member=False,
                )
                try:
                    player_id = repo.insert_player(new_player)
                    new_player = Player(
                        player_id=player_id,
                        name=username,
                        is_team_member=False,
                    )
                    player = new_player
                except Exception as e:
                    log(f"    Could not create player '{username}': {e}")
                    continue

            if player is None or player.player_id is None:
                log(f"    Skipping stat row — no username in replay data")
                continue

            # ── Resolve operator ──────────────────────────────────
            operator = None
            if op_name:
                operator = repo.get_operator_by_name(op_name)
                if operator is None:
                    # Try case-insensitive partial match (r6-dissect may use different casing)
                    operator = repo.get_operator_by_name_fuzzy(op_name)

            if operator is None:
                # Use a fallback operator_id=0 placeholder if operator not found
                # This avoids FK violations while still saving K/D/A
                # The user can correct it via Manual Entry
                log(f"    Operator '{op_name}' not found in DB — skipping player '{username}'")
                continue

            # ── Build and insert PlayerRoundStats ─────────────────
            from models.player_round_stats import PlayerRoundStats
            stat = PlayerRoundStats(
                stat_id=None,
                round_id=round_id,
                player_id=player.player_id,
                player=player,
                operator=operator,
                kills=kills,
                deaths=deaths,
                assists=assists,
                engagements_taken=0,   # not available from replay
                engagements_won=0,     # not available from replay
                ability_start=operator.ability_max_count,
                ability_used=0,        # not available from replay
                secondary_gadget=None,
                secondary_start=0,
                secondary_used=0,
                plant_attempted=False,
                plant_successful=False,
            )

            try:
                repo.insert_player_round_stats(stat, round_id, player.player_id)
                saved += 1
            except Exception as e:
                log(f"    Could not save stats for '{username}': {e}")

        return saved

    # =====================================================
    # TRANSCRIPTION
    # =====================================================

    def _run_transcription(
        self,
        results: list[ImportResult],
        folders: list[Path],
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:


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
        speakers = self._transcriber.diarize_speakers(all_segments, n_speakers=5)

        if log_callback:
            for spk, data in speakers.items():
                log_callback(
                    f"  {spk}: {data['word_count']} words | "
                    f"{data['talk_time']:.0f}s talk time"
                )

        # ── Step 3: Get session start time ───────────────────────
        session_start_epoch: Optional[float] = None

        if self.recording_path and self.recording_path.exists():
            # Strategy 1: parse timestamp from OBS filename
            # OBS names files like "2026-04-27 16-38-48.mp4"
            import re as _re
            stem = self.recording_path.stem  # "2026-04-27 16-38-48"
            m = _re.match(
                r"(\d{4}-\d{2}-\d{2})\s+(\d{2}-\d{2}-\d{2})", stem
            )
            if m:
                try:
                    import datetime as _dt
                    date_str = m.group(1)
                    time_str = m.group(2).replace("-", ":")
                    dt = _dt.datetime.strptime(
                        f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
                    )
                    # OBS saves in local time
                    session_start_epoch = dt.timestamp()
                    if log_callback:
                        log_callback(
                            f"Session start (from filename): "
                            f"{dt.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                except Exception as e:
                    if log_callback:
                        log_callback(f"Could not parse filename timestamp: {e}")

            # Strategy 2: file creation time
            if session_start_epoch is None:
                try:
                    session_start_epoch = self.recording_path.stat().st_ctime
                    import datetime as _dt
                    readable = _dt.datetime.fromtimestamp(
                        session_start_epoch
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    if log_callback:
                        log_callback(f"Session start (from ctime): {readable}")
                except Exception as e:
                    if log_callback:
                        log_callback(f"Could not read file ctime: {e}")

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

            # Build storage dict including speaker data
            storage = parser.to_storage_dict(parsed)
            storage["speakers"] = {
                spk: {
                    "word_count": data["word_count"],
                    "talk_time":  round(data["talk_time"], 1),
                    "top_words":  data["top_words"][:10],
                }
                for spk, data in speakers.items()
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

    def _is_folder_stable(self, folder: Path) -> bool:
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

        for f in to_delete:
            try:
                mb = f.stat().st_size / (1024 * 1024)
                f.unlink()
                log(f"Deleted: {f.name} ({mb:.0f} MB)")
                deleted += 1
            except Exception as e:
                log(f"Could not delete {f.name}: {e}")

        total_freed = sum(
            0 for f in to_delete
        )   # already deleted, can't stat
        log(f"Cleanup complete: {deleted} file(s) deleted.")
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