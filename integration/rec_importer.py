import subprocess
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# How many times to retry a single .rec file before giving up
MAX_RETRIES   = 5
RETRY_DELAY   = 1.5   # seconds between retries
# Timeout for r6-dissect subprocess per file
DISSECT_TIMEOUT = 60  # seconds


class RecImporter:
    """
    Parses one or more match replay folders using r6-dissect.
    Retries each file up to MAX_RETRIES times before marking partial failure.
    Never stops early — always processes every file it can.
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
                f"r6-dissect not found at {self.dissect_path}\n"
                f"Expected: {self.dissect_path}"
            )

    # =====================================================
    # PUBLIC: Single folder
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

        parsed_rounds:  list[Round] = []
        failed_files:   list[str]   = []
        map_name:       Optional[str] = None
        score_us:       Optional[int] = None
        score_them:     Optional[int] = None

        for rec_file in rec_files:
            self._log(f"  Parsing {rec_file.name}...")
            raw, err = self._run_dissect_with_retry(rec_file)

            if raw is None:
                self._log(f"  ✗ {rec_file.name} failed after {MAX_RETRIES} attempts: {err}")
                failed_files.append(rec_file.name)
                continue

            try:
                round_obj, meta = self._parse_round(raw)
                parsed_rounds.append(round_obj)

                if map_name is None and meta.get("map_name"):
                    map_name = meta["map_name"]
                    self._log(f"  Map detected: {map_name}")
                if score_us is None and meta.get("score_us") is not None:
                    score_us = meta["score_us"]
                if score_them is None and meta.get("score_them") is not None:
                    score_them = meta["score_them"]

                self._log(
                    f"  ✓ {rec_file.name} → Round {round_obj.round_number} "
                    f"| {round_obj.side} | {round_obj.outcome}"
                )

            except Exception as parse_err:
                self._log(f"  ✗ {rec_file.name} parse error: {parse_err}")
                failed_files.append(rec_file.name)

        # ── Determine final status ────────────────────────────
        if not parsed_rounds:
            msg = (
                f"All {len(rec_files)} rounds failed to parse in {folder.name}.\n"
                f"Failed files: {', '.join(failed_files)}"
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
            msg = None
            status = ImportStatus.SUCCESS
            self._log(
                f"SUCCESS: {len(parsed_rounds)} rounds parsed from {folder.name}"
            )

        return ImportResult(
            status=status,
            map_name=map_name,
            score_us=score_us,
            score_them=score_them,
            rounds=parsed_rounds,
            error_message=msg,
        )

    # =====================================================
    # PUBLIC: Multiple folders (parallel)
    # =====================================================

    def import_multiple_folders(
        self,
        folders: list[Path],
        max_workers: int = 2,   # lowered from 4 — USB I/O is the bottleneck
    ) -> list[ImportResult]:
        results: dict[int, ImportResult] = {}

        self._log(
            f"Starting import of {len(folders)} folder(s) "
            f"with {max_workers} worker(s)..."
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self.import_match_folder, folder): i
                for i, folder in enumerate(folders)
            }

            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    self._log(f"Worker exception for folder #{index}: {e}")
                    results[index] = ImportResult(
                        status=ImportStatus.CRITICAL_FAILURE,
                        error_message=str(e),
                    )

        return [results[i] for i in range(len(folders))]

    # =====================================================
    # INTERNAL: Run dissect with retry
    # =====================================================

    def _run_dissect_with_retry(
        self, rec_file: Path
    ) -> tuple[Optional[dict], Optional[str]]:
        """
        Runs r6-dissect on a single file, retrying up to MAX_RETRIES times.
        Returns (parsed_dict, None) on success or (None, error_string) on failure.
        """
        last_error = "Unknown error"

        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                self._log(
                    f"    Retry {attempt}/{MAX_RETRIES} for {rec_file.name}..."
                )
                time.sleep(RETRY_DELAY)

            try:
                proc = subprocess.run(
                    [str(self.dissect_path), str(rec_file), "--format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=DISSECT_TIMEOUT,
                )

                # r6-dissect sometimes exits non-zero but still outputs valid JSON
                stdout = proc.stdout.strip()
                stderr = proc.stderr.strip()

                if proc.returncode != 0 and not stdout:
                    last_error = (
                        f"Exit code {proc.returncode}"
                        + (f": {stderr}" if stderr else "")
                    )
                    self._log(f"    Attempt {attempt} failed: {last_error}")
                    continue

                if not stdout:
                    last_error = "Empty output from r6-dissect"
                    self._log(f"    Attempt {attempt} failed: {last_error}")
                    continue

                # Try to parse JSON — handle leading garbage before '{'
                json_start = stdout.find("{")
                if json_start == -1:
                    last_error = "No JSON object found in output"
                    self._log(f"    Attempt {attempt} failed: {last_error}")
                    continue

                data = json.loads(stdout[json_start:])
                return data, None

            except subprocess.TimeoutExpired:
                last_error = f"r6-dissect timed out after {DISSECT_TIMEOUT}s"
                self._log(f"    Attempt {attempt} failed: {last_error}")

            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                self._log(f"    Attempt {attempt} failed: {last_error}")

            except Exception as e:
                last_error = str(e)
                self._log(f"    Attempt {attempt} failed: {last_error}")

        return None, last_error

    # =====================================================
    # INTERNAL: Parse one round
    # =====================================================

    def _parse_round(self, data: dict) -> tuple[Round, dict]:
        recording_player_id             = data.get("recordingPlayerID")
        our_team_index: Optional[int]   = None

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
                score_us  = score
                our_side  = "attack" if role == "attack" else "defense"
                outcome   = "win" if won else "loss"
            else:
                score_them = score

        map_data   = data.get("map", {})
        map_id_raw = map_data.get("id")
        map_name   = MAP_ID_LOOKUP.get(map_id_raw, map_data.get("name"))

        round_number = data.get("roundNumber", 0)

        # roundNumber is 0-indexed in r6-dissect — convert to 1-indexed
        if isinstance(round_number, int) and round_number == 0:
            round_number = 1
        elif isinstance(round_number, int):
            round_number = round_number + 1

        round_obj = Round(
            round_id=None,
            match_id=None,
            round_number=round_number,
            side=our_side or "attack",
            site=data.get("site", ""),
            outcome=outcome or "loss",
            resources=None,
            player_stats=[],
        )

        meta = {
            "map_name":  map_name,
            "score_us":  score_us,
            "score_them": score_them,
        }

        return round_obj, meta