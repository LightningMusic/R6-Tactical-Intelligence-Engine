"""
Re-reads a stored session's replays to fill in what older imports lacked:
gadget usage per player-round and the objective (plant / defuse) facts. It
needs no transcription, so it takes seconds, and the AI text is regenerated
afterwards from the stored data.
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from server.config import server_settings
from server.database import server_db


def package_path(session_id: str) -> Optional[Path]:
    with server_db.get_connection() as conn:
        row = conn.execute("SELECT package_hash FROM server_packages WHERE session_id = ?", (session_id,)).fetchone()
    return server_settings.UPLOADS_DIR / f"{row['package_hash']}.r6session" if row else None


def apply_round_updates(conn, match_id: int, rounds: list[Any]) -> dict[str, int]:
    """Writes each re-read round's gadget numbers and events onto the already-stored match."""
    stats_updated = events_updated = 0
    for rnd in rounds:
        row = conn.execute("SELECT round_id FROM rounds WHERE match_id = ? AND round_number = ?",
                           (match_id, int(rnd.round_number))).fetchone()
        if row is None:
            continue
        for raw in rnd.raw_player_stats:
            if "gadget_start" not in raw:
                continue
            cur = conn.execute(
                """UPDATE player_round_stats SET ability_start = ?, ability_used = ?
                   WHERE round_id = ? AND player_id = (SELECT player_id FROM players WHERE lower(name) = lower(?))""",
                (int(raw["gadget_start"]), int(raw.get("gadget_used", 0) or 0), row[0], raw.get("username", "")),
            )
            stats_updated += cur.rowcount
        if rnd.round_events is not None:
            name = f"round_{int(rnd.round_number)}_events"
            conn.execute("DELETE FROM derived_metrics WHERE match_id = ? AND metric_name = ?", (match_id, name))
            conn.execute("INSERT INTO derived_metrics (match_id, metric_name, metric_value, is_ai_generated, metric_text) "
                         "VALUES (?, ?, 0, 0, ?)", (match_id, name, json.dumps(rnd.round_events.to_dict())))
            events_updated += 1
    conn.commit()
    return {"stats": stats_updated, "events": events_updated}


def backfill_session(session_id: str, log=print) -> dict[str, Any]:
    from integration.rec_importer import RecImporter
    from server.match_db import get_match_repo
    from server.services.comms_service import CommsService

    row = CommsService._get(session_id)
    match_id = int(row["match_id"]) if row and row["match_id"] is not None else None
    path = package_path(session_id)
    if match_id is None or path is None or not path.exists():
        return {"error": "No stored match or package for that session."}

    with tempfile.TemporaryDirectory() as tmp:
        replays = Path(tmp) / "replays"
        replays.mkdir()
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.filename.lower().endswith(".rec"):
                    (replays / Path(info.filename).name).write_bytes(z.read(info))
        result = RecImporter(dissect_path=server_settings.R6_DISSECT_PATH, log_callback=log).import_match_folder(replays)

    if not result.rounds:
        return {"error": result.error_message or "No rounds could be read from the stored replays."}
    repo = get_match_repo()
    with repo.db.get_connection() as conn:
        done = apply_round_updates(conn, match_id, result.rounds)
    log(f"[Backfill] match {match_id}: updated {done['stats']} player-round rows and {done['events']} rounds of events.")

    # A round whose "our team" could not be worked out (2026-10-05: stray player ids) was also left out
    # of the comms timeline. When the session already has a transcript, refresh its saved rounds from
    # this read and rebuild the timeline, so teammates' lines and callouts are placed against the right rounds.
    try:
        if json.loads(row["host_utterances_json"] or "[]") and getattr(result, "timeline_rounds", None):
            CommsService.save_rounds(session_id, result.timeline_rounds, match_id)
            if CommsService.build(session_id) is not None:
                done["timeline_rebuilt"] = True
                log(f"[Backfill] match {match_id}: comms timeline rebuilt.")
    except Exception as e:                                       # noqa: BLE001 - a repair, never fatal
        log(f"[Backfill] match {match_id}: comms timeline not rebuilt: {e}")
    return {"match_id": match_id, **done}
