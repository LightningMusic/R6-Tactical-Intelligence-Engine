import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from server.config import server_settings
from server.database import server_db
from server.repositories import ServerRepository
from server.storage import storage_manager
from server.services.package_validation import ServerPackageValidator


class SessionProcessingService:
    """
    Processes an uploaded .r6session archive:
    1. Re-validates archive integrity.
    2. Extracts to isolated work directory server_data/work/<job_id>/.
    3. Reads metadata.json and telemetry.json.
    4. Saves summary results into server database.
    5. Cleans up isolated work directory upon completion.
    """

    @classmethod
    def process_session_job(cls, job_id: str, archive_path: Path, session_id: str) -> Dict[str, Any]:
        valid, msg, manifest = ServerPackageValidator.validate_package(archive_path)
        if not valid:
            raise ValueError(f"Package validation failed: {msg}")

        work_dir = storage_manager.create_work_directory(job_id)

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(work_dir)

            metadata_file = work_dir / "metadata.json"
            meta_data = {}
            if metadata_file.exists():
                meta_data = json.loads(metadata_file.read_text(encoding="utf-8"))

            telemetry_file = work_dir / "telemetry.json"
            telem_data = {}
            if telemetry_file.exists():
                telem_data = json.loads(telemetry_file.read_text(encoding="utf-8"))

            map_name = meta_data.get("map_name", "Unknown")
            rounds_count = int(telem_data.get("rounds_parsed", 0))

            summary = {
                "session_id": session_id,
                "map_name": map_name,
                "score_us": meta_data.get("score_us"),
                "score_them": meta_data.get("score_them"),
                "rounds_parsed": rounds_count,
                "client_name": meta_data.get("client_name", "Unknown"),
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            now = datetime.now(timezone.utc).isoformat()
            with server_db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO server_parsed_matches (session_id, map_name, rounds_count, summary_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        map_name = excluded.map_name,
                        rounds_count = excluded.rounds_count,
                        summary_json = excluded.summary_json,
                        created_at = excluded.created_at
                    """,
                    (session_id, map_name, rounds_count, json.dumps(summary), now),
                )
                conn.commit()

            # ── Milestone 4: transcription (phase 1), then full AI analysis
            # (phase 2) — in that order, since analysis wants the transcript
            # already in hand if one exists. Both are entirely best-effort:
            # neither can fail this job. A session that fails both still
            # gets its basic map/score summary above, which is all local
            # processing has ever guaranteed.
            cls._maybe_transcribe(work_dir, session_id, meta_data)
            cls._maybe_analyze(work_dir, session_id)

            return summary

        finally:
            storage_manager.cleanup_work_directory(job_id)

    @classmethod
    def _maybe_transcribe(cls, work_dir: Path, session_id: str, meta_data: Optional[dict] = None) -> None:
        """
        Milestone 4, phase 1: if the uploaded package included an audio
        recording, transcribe it here on the server instead of requiring
        the client to do it locally. The client already slices the audio
        down to this match's own window before uploading it, so this just
        runs it through WhisperTranscriber/TranscriptParser as-is — no
        second alignment pass, and no TimelineAligner involved.

        Deliberately never raises: a transcription failure (missing
        model, ffmpeg issue, corrupt audio, etc.) should not fail the
        whole job — the session still gets its basic map/score summary
        either way, it just won't have a transcript.
        """
        audio_dir = work_dir / "audio"
        if not audio_dir.is_dir() or not any(audio_dir.iterdir()):
            return  # client didn't include audio for this session — nothing to do

        try:
            from analysis.transcript_parser import TranscriptParser
            from server.services.comms_service import CommsService

            # No second alignment pass here. app/session_manager.py already
            # sliced this exact clip down to just this match's window,
            # against the real OBS recording and the real session start
            # time, before ever bundling it into the upload -- that's the
            # whole reason the client sends a small per-match clip instead
            # of the full multi-hour recording. Re-deriving a "session
            # start" here from THIS clip's own filename can't work even in
            # principle: it's a random temp name from tempfile.mktemp(),
            # never the OBS-pattern name TimelineAligner.parse_session_
            # start_epoch() looks for, so it always fell through to the
            # clip's own extraction-time mtime -- not a session start at
            # all -- and re-cropped an already-correct clip against a
            # meaningless reference point. That's what was producing
            # "Recording: 0.0 min (0 MB)" and empty transcripts for every
            # real session tonight: the crop range landed outside the
            # clip's actual (short) length.
            #
            # 2026-09-30: a package from a current client carries one clip per
            # OBS track (your mic, and Discord = everyone else) plus where the
            # clip sits in time; CommsService transcribes each on its own so
            # every line knows whose it is. Older single-track packages go
            # through the same call and come out as before, speaker unknown.
            full_result = CommsService.transcribe_host_audio(work_dir, meta_data or {}, session_id)
            if full_result is None:
                return

            text = full_result.get("text", "")
            segments = full_result.get("segments", [])

            parser = TranscriptParser()
            parsed = parser.parse_segments_list(segments)
            storage = parser.to_storage_dict(parsed)

            ServerRepository().save_transcript(
                session_id=session_id,
                raw_text=text,
                processed_segments_json=json.dumps(storage),
                word_count=len(text.split()),
            )
            print(f"[SessionProcessing] Transcribed session {session_id}: {len(text.split())} words.")

        except Exception as e:
            print(f"[SessionProcessing] Transcription skipped for session {session_id}: {e}")

    @classmethod
    def _maybe_analyze(cls, work_dir: Path, session_id: str) -> None:
        """
        Milestone 4, phase 2: rebuilds the full match record (rounds,
        per-player kills/deaths/assists, kill-feed events — everything
        r6-dissect gives us automatically, no manual entry involved, exactly
        like app/session_manager.py's local _auto_create_matches) in the
        server's own matches.db, attaches the phase-1 transcript if one was
        produced, then runs the same IntelEngine the client uses to generate
        the match summary and per-player intel — all without the client
        lifting a finger beyond uploading.

        Never raises: this is the one step in the whole pipeline most likely
        to be unavailable on a fresh server (no Ollama installed yet), and a
        missing AI backend should mean "no report yet" for this session, not
        a failed job. update_analysis_status() records exactly why, so the
        dashboard can say so instead of just not showing anything.
        """
        replays_dir = work_dir / "replays"
        repo_job = ServerRepository()

        if not replays_dir.is_dir() or not any(replays_dir.glob("*.rec")):
            repo_job.update_analysis_status(session_id, "failed", error="No replay files in package.")
            return

        try:
            from integration.rec_importer import RecImporter
            from analysis.match_builder import build_matches_from_import_results
            from analysis.intel_engine import IntelEngine
            from server.match_db import get_match_repo

            repo_job.update_analysis_status(session_id, "analyzing")

            importer = RecImporter(dissect_path=server_settings.R6_DISSECT_PATH)
            result = importer.import_match_folder(replays_dir)

            if not result.rounds:
                repo_job.update_analysis_status(
                    session_id, "failed",
                    error=result.error_message or "No rounds could be parsed from the replay.",
                )
                return

            catalog_hints = None
            try:
                meta = json.loads((work_dir / "metadata.json").read_text(encoding="utf-8"))
                catalog_hints = meta.get("catalog")
            except Exception:
                pass

            match_repo = get_match_repo()
            build_matches_from_import_results(
                match_repo, [result], log=print, catalog_hints=catalog_hints,
            )

            if result.match_id is None:
                repo_job.update_analysis_status(session_id, "failed", error="Match record could not be created.")
                return

            match_id = result.match_id
            repo_job.update_analysis_status(session_id, "analyzing", match_id=match_id)

            # Carry the phase-1 transcript (if any) over into the match
            # schema's own transcripts table, so IntelEngine's existing
            # transcript-reading code works completely unmodified.
            transcript = repo_job.get_transcript(session_id)
            if transcript and transcript.get("raw_text"):
                with match_repo.db.get_connection() as conn:
                    conn.execute(
                        """INSERT INTO transcripts (match_id, raw_text, processed_segments_json)
                           VALUES (?, ?, ?) ON CONFLICT DO NOTHING""",
                        (match_id, transcript["raw_text"], transcript.get("processed_segments_json")),
                    )
                    conn.commit()

            # Comms timeline: rounds + kill feed from the replays, any
            # teammate recordings already uploaded, then the timeline itself
            # -- before the AI runs, so its debrief gets the measured comms.
            try:
                from server.services.comms_service import CommsService
                CommsService.save_rounds(session_id, result.timeline_rounds, match_id)
                CommsService.merge_voice(session_id)
                timeline = CommsService.build(session_id)
                if timeline:
                    print(f"[SessionProcessing] Comms timeline for match {match_id}: "
                          f"{sum(len(r['items']) for r in timeline['rounds'])} items, "
                          f"{len(timeline['flags'])} flagged moments.")
            except Exception as comms_err:
                print(f"[SessionProcessing] Comms timeline skipped for session {session_id}: {comms_err}")

            intel = IntelEngine(
                ollama_exe=server_settings.OLLAMA_EXE,
                ollama_models=server_settings.OLLAMA_MODELS_DIR,
                default_model=server_settings.OLLAMA_MODEL,
                db_path=server_settings.MATCHES_DB_PATH,
                schema_path=server_settings.MATCHES_SCHEMA_PATH,
                ollama_options=server_settings.OLLAMA_OPTIONS,
                ollama_url=server_settings.OLLAMA_URL or None,
            )

            from server.services.comms_service import CommsService as _Comms
            ours = _Comms.our_players_from_rounds(getattr(result, "timeline_rounds", None) or [])
            _, display = _Comms.team_context(match_id)

            report = _Comms.roster_usernames() or None
            match_analysis = intel.analyze_match(match_id, our_players=ours or None, display_names=display,
                                                 report_players=report)
            if "error" in match_analysis:
                repo_job.update_analysis_status(session_id, "failed", match_id=match_id, error=match_analysis["error"])
                return

            summary_text = match_analysis.get("ai_match_summary", "")
            if summary_text.startswith("[AI unavailable]") or summary_text.startswith("[AI] Generation failed"):
                repo_job.update_analysis_status(session_id, "failed", match_id=match_id, error=summary_text.splitlines()[0])
                return

            player_intel = intel.get_player_intel(match_id, our_players=ours or None, display_names=display,
                                                  report_players=report)
            with match_repo.db.get_connection() as conn:
                conn.execute("DELETE FROM derived_metrics WHERE match_id = ? AND metric_name LIKE 'ai_player_intel::%'",
                             (match_id,))
                conn.commit()
            for player_name, text in player_intel.items():
                intel.store_ai_text(match_repo, match_id, f"ai_player_intel::{player_name}", text)

            repo_job.update_analysis_status(session_id, "completed", match_id=match_id)
            print(f"[SessionProcessing] AI analysis complete for session {session_id} (match {match_id}).")

        except Exception as e:
            print(f"[SessionProcessing] Analysis failed for session {session_id}: {e}")
            try:
                repo_job.update_analysis_status(session_id, "failed", error=str(e)[:500])
            except Exception:
                pass
