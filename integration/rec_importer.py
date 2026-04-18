import subprocess
import json
import time
import tempfile
import os
from pathlib import Path
from typing import Optional, Callable

from app.config import R6_DISSECT_PATH
from models.import_result import ImportResult, ImportStatus
from models.round import Round


MAP_ID_LOOKUP: dict[int, str] = {
    417890697769: "Clubhouse",
    108179795804: "Bank",
    108179870768: "Border",
    108179936024: "Chalet",
    108179936776: "Consulate",
    108180068392: "Coastline",
    108180068680: "Hereford Base",
    108180134456: "House",
    108180134744: "Kafe Dostoyevsky",
    108180200520: "Kanal",
    108180200808: "Oregon",
    108180266296: "Plane",
    108180266584: "Skyscraper",
    108180332360: "Theme Park",
    108180332648: "Tower",
    108180398424: "Villa",
    108180398712: "Yacht",
    108180464488: "Fortress",
    108180464776: "Outback",
    108180530552: "Emerald Plains",
    108180530840: "Stadium Bravo",
    108180596616: "Nighthaven Labs",
    108180596904: "Lair",
    108180662680: "Close Quarter",
    108180662968: "Favela",
    108180728456: "Donut",
}

MAX_RETRIES     = 5
RETRY_DELAY     = 2.0
DISSECT_TIMEOUT = 90


class RecImporter:
    """
    Parses match replay folders using r6-dissect.
    Uses temp file output (more reliable than stdout capture).
    Sequential processing only — no threads (avoids UI deadlocks).
    Retries each file up to MAX_RETRIES times.
    """

    def __init__(
        self,
        dissect_path: Path = R6_DISSECT_PATH,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.dissect_path = dissect_path
        self._log = log_callback or (lambda msg: print(f"[RecImporter] {msg}"))

        if not self.dissect_path.exists():
            raise FileNotFoundError(
                f"r6-dissect not found at {self.dissect_path}"
            )

    # =====================================================
    # PUBLIC: Single folder — sequential
    # =====================================================

    def import_match_folder(self, folder: Path) -> ImportResult:
        rec_files = sorted(folder.glob("*.rec"))

        if not rec_files:
            self._log(f"No .rec files in {folder.name}")
            return ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message=f"No .rec files found in {folder.name}",
            )

        self._log(f"Found {len(rec_files)} .rec file(s) in {folder.name}")

        parsed_rounds: list[Round] = []
        failed_files:  list[str]  = []
        map_name:      Optional[str] = None
        score_us:      Optional[int] = None
        score_them:    Optional[int] = None

        for rec_file in rec_files:
            self._log(f"  Parsing {rec_file.name}...")
            raw, err = self._run_dissect_with_retry(rec_file)

            if raw is None:
                self._log(
                    f"  ✗ {rec_file.name} — all {MAX_RETRIES} attempts failed: {err}"
                )
                failed_files.append(rec_file.name)
                continue

            try:
                round_obj, meta = self._parse_round(raw)
                parsed_rounds.append(round_obj)

                if map_name is None and meta.get("map_name"):
                    map_name = meta["map_name"]
                    self._log(f"  Map: {map_name}")
                if score_us is None and meta.get("score_us") is not None:
                    score_us = meta["score_us"]
                if score_them is None and meta.get("score_them") is not None:
                    score_them = meta["score_them"]

                self._log(
                    f"  ✓ R{round_obj.round_number} "
                    f"| {round_obj.side} | {round_obj.outcome}"
                    f" | site: {round_obj.site or '?'}"
                )

            except Exception as parse_err:
                self._log(f"  ✗ {rec_file.name} — parse error: {parse_err}")
                failed_files.append(rec_file.name)

        if not parsed_rounds:
            msg = (
                f"All {len(rec_files)} files failed in {folder.name}. "
                f"Failed: {', '.join(failed_files)}"
            )
            self._log(f"CRITICAL: {msg}")
            return ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message=msg,
                map_name=map_name,
            )

        if failed_files:
            msg = (
                f"{len(parsed_rounds)}/{len(rec_files)} rounds parsed. "
                f"Failed: {', '.join(failed_files)}"
            )
            self._log(f"PARTIAL: {msg}")
            status = ImportStatus.PARTIAL_FAILURE
        else:
            msg    = None
            status = ImportStatus.SUCCESS
            self._log(f"SUCCESS: {len(parsed_rounds)} rounds from {folder.name}")

        return ImportResult(
            status=status,
            map_name=map_name,
            score_us=score_us,
            score_them=score_them,
            rounds=parsed_rounds,
            error_message=msg,
        )

    # =====================================================
    # PUBLIC: Multiple folders — sequential, no threads
    # =====================================================

    def import_multiple_folders(
        self,
        folders: list[Path],
        max_workers: int = 1,       # ignored — always sequential
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> list[ImportResult]:
        results: list[ImportResult] = []

        msg = f"Importing {len(folders)} folder(s) sequentially..."
        self._log(msg)
        if log_callback:
            log_callback(msg)

        for i, folder in enumerate(folders):
            msg = f"Processing folder {i+1}/{len(folders)}: {folder.name}"
            self._log(msg)
            if log_callback:
                log_callback(msg)

            result = self.import_match_folder(folder)
            results.append(result)

            summary = (
                f"Folder {i+1} done: {result.status.value} "
                f"— {len(result.rounds)} rounds"
            )
            self._log(summary)
            if log_callback:
                log_callback(summary)

        return results

    # =====================================================
    # INTERNAL: Run r6-dissect via temp file output
    # =====================================================

    def _run_dissect_with_retry(
        self, rec_file: Path
    ) -> tuple[Optional[dict], Optional[str]]:
        last_error = "Unknown error"

        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                self._log(f"    Retry {attempt}/{MAX_RETRIES}...")
                time.sleep(RETRY_DELAY)

            tmp_path = Path(tempfile.mktemp(suffix=".json"))

            try:
                proc = subprocess.run(
                    [
                        str(self.dissect_path),
                        str(rec_file),
                        "--format", "json",
                        "--output", str(tmp_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=DISSECT_TIMEOUT,
                    cwd=str(self.dissect_path.parent),
                )

                stderr = proc.stderr.strip() if proc.stderr else ""

                if "panic:" in stderr or "goroutine" in stderr:
                    panic_lines = [
                        line for line in stderr.splitlines()
                        if line.startswith("panic:") or "unknown" in line.lower()
                    ]
                    panic_msg = panic_lines[0] if panic_lines else stderr[:150]

                    # Only skip retries for unknown operator — that's truly unrecoverable
                    # with the old binary. Index errors and other panics may be transient.
                    if "role unknown for operator" in panic_msg.lower():
                        self._log(
                            f"    r6-dissect crashed: {panic_msg}\n"
                            f"    Unknown operator — rebuild r6-dissect with the patched binary."
                        )
                        return None, f"r6-dissect outdated: {panic_msg}"
                    else:
                        # All other panics: log and retry normally
                        last_error = f"r6-dissect panic: {panic_msg}"
                        self._log(f"    Attempt {attempt}: {last_error}")
                        continue

                # Check temp file first
                if tmp_path.exists() and tmp_path.stat().st_size > 0:
                    try:
                        content = tmp_path.read_text(encoding="utf-8", errors="ignore")
                        data = self._parse_json_safe(content, rec_file.name)
                        if data is not None:
                            return data, None
                    except Exception as e:
                        last_error = f"Temp file parse error: {e}"
                    finally:
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass

                # Fallback: stdout
                stdout = proc.stdout.strip() if proc.stdout else ""
                if stdout:
                    data = self._parse_json_safe(stdout, rec_file.name)
                    if data is not None:
                        return data, None

                last_error = (
                    f"Exit {proc.returncode}"
                    + (f" | {stderr[:200]}" if stderr else "")
                    + (" | no output" if not stdout else "")
                )
                self._log(f"    Attempt {attempt}: {last_error}")

            except subprocess.TimeoutExpired:
                last_error = f"Timed out after {DISSECT_TIMEOUT}s"
                self._log(f"    Attempt {attempt}: {last_error}")
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

            except Exception as e:
                last_error = str(e)
                self._log(f"    Attempt {attempt}: {last_error}")
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        return None, last_error

    def _parse_json_safe(
        self, text: str, filename: str
    ) -> Optional[dict]:
        """
        Parses JSON from text, handling leading garbage.
        Returns None if no valid JSON found.
        """
        text = text.strip()
        if not text:
            return None

        # Find the first '{' — skip any BOM or debug output before JSON
        json_start = text.find("{")
        if json_start == -1:
            self._log(f"    No JSON object found in output for {filename}")
            return None

        try:
            return json.loads(text[json_start:])
        except json.JSONDecodeError as e:
            # Try to find a complete JSON object
            # r6-dissect sometimes writes multiple objects
            brace_depth = 0
            json_end    = -1
            for i, ch in enumerate(text[json_start:], start=json_start):
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_end = i + 1
                        break
            if json_end != -1:
                try:
                    return json.loads(text[json_start:json_end])
                except json.JSONDecodeError:
                    pass
            self._log(f"    JSON decode error for {filename}: {e}")
            return None

    # =====================================================
    # INTERNAL: Parse one round's data
    # =====================================================

    def _parse_round(self, data: dict) -> tuple[Round, dict]:
        recording_player_id           = data.get("recordingPlayerID")
        our_team_index: Optional[int] = None

        for player in data.get("players", []):
            if player.get("id") == recording_player_id:
                our_team_index = player.get("teamIndex")
                break

        teams      = data.get("teams", [])
        score_us:  Optional[int] = None
        score_them: Optional[int] = None
        our_side:  Optional[str] = None
        outcome:   Optional[str] = None

        for i, team in enumerate(teams):
            role  = team.get("role", "").lower()
            score = team.get("score")
            won   = team.get("won", False)

            if our_team_index is not None and i == our_team_index:
                score_us = score
                our_side = "attack" if role == "attack" else "defense"
                outcome  = "win" if won else "loss"
            else:
                score_them = score

        map_data   = data.get("map", {})
        map_id_raw = map_data.get("id")
        map_name   = MAP_ID_LOOKUP.get(map_id_raw, map_data.get("name"))

        # r6-dissect roundNumber is 0-indexed
        round_number = data.get("roundNumber", 0)
        if isinstance(round_number, int):
            round_number = round_number + 1  # always convert to 1-indexed

        round_obj = Round(
            round_id=None,
            match_id=None,
            round_number=max(1, round_number),
            side=our_side or "attack",
            site=data.get("site", ""),
            outcome=outcome or "loss",
            resources=None,
            player_stats=[],
        )

        return round_obj, {
            "map_name":  map_name,
            "score_us":  score_us,
            "score_them": score_them,
        }