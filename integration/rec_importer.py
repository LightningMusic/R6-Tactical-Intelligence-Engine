import subprocess
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from app.config import R6_DISSECT_PATH
from models.import_result import ImportResult, ImportStatus
from models.round import Round


# =====================================================
# Map ID → Human Name lookup
# R6-dissect returns numeric IDs, not strings.
# Add new maps here as Ubisoft releases them.
# =====================================================
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


class RecImporter:
    """
    Parses one or more match replay folders using r6-dissect.
    Each folder contains multiple .rec files (one per round).
    Returns one ImportResult per folder.
    """

    def __init__(self, dissect_path: Path = R6_DISSECT_PATH):
        self.dissect_path = dissect_path

        if not self.dissect_path.exists():
            raise FileNotFoundError(
                f"r6-dissect not found at {self.dissect_path}"
            )

    # =====================================================
    # PUBLIC: Single folder
    # =====================================================

    def import_match_folder(self, folder: Path) -> ImportResult:
        """
        Parses all .rec files in a single match folder.
        Returns one ImportResult representing the whole match.
        """
        rec_files = sorted(folder.glob("*.rec"))

        if not rec_files:
            return ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message=f"No .rec files found in {folder.name}",
            )

        parsed_rounds: list[Round] = []
        partial_failure = False
        map_name: Optional[str] = None
        score_us: Optional[int] = None
        score_them: Optional[int] = None

        for rec_file in rec_files:
            try:
                raw = self._run_dissect(rec_file)
                round_obj, meta = self._parse_round(raw)

                parsed_rounds.append(round_obj)

                # Capture match-level metadata from any round (they all have it)
                if map_name is None and meta.get("map_name"):
                    map_name = meta["map_name"]
                if score_us is None:
                    score_us = meta.get("score_us")
                if score_them is None:
                    score_them = meta.get("score_them")

            except Exception as e:
                partial_failure = True
                continue

        if not parsed_rounds:
            return ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="All rounds failed to parse.",
            )

        status = (
            ImportStatus.PARTIAL_FAILURE if partial_failure
            else ImportStatus.SUCCESS
        )

        return ImportResult(
            status=status,
            map_name=map_name,       # raw string — repo layer resolves to map_id
            score_us=score_us,
            score_them=score_them,
            rounds=parsed_rounds,
            error_message="Some rounds failed to parse." if partial_failure else None,
        )

    # =====================================================
    # PUBLIC: Multiple folders (parallel)
    # =====================================================

    def import_multiple_folders(
        self,
        folders: list[Path],
        max_workers: int = 4,
    ) -> list[ImportResult]:
        """
        Parses multiple match folders in parallel.
        Returns one ImportResult per folder, in the same order.
        """
        results: dict[int, ImportResult] = {}

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
                    results[index] = ImportResult(
                        status=ImportStatus.CRITICAL_FAILURE,
                        error_message=str(e),
                    )

        # Return in original folder order
        return [results[i] for i in range(len(folders))]

    # =====================================================
    # INTERNAL: Run r6-dissect on one .rec file
    # =====================================================

    def _run_dissect(self, rec_file: Path) -> dict:
        try:
            result = subprocess.run(
                [str(self.dissect_path), str(rec_file), "--format", "json"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"r6-dissect failed on {rec_file.name}: {e.stderr.strip()}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Invalid JSON from r6-dissect for {rec_file.name}")

    # =====================================================
    # INTERNAL: Parse one round's JSON
    # =====================================================

    def _parse_round(self, data: dict) -> tuple[Round, dict]:
        """
        Parses a single round JSON blob.
        Returns (Round, meta_dict) where meta carries match-level info.
        """

        # ── Identify which team is "YOUR TEAM" ──────────────────
        recording_player_id = data.get("recordingPlayerID")
        our_team_index: Optional[int] = None

        for player in data.get("players", []):
            if player.get("id") == recording_player_id:
                our_team_index = player.get("teamIndex")
                break

        # ── Teams ────────────────────────────────────────────────
        teams = data.get("teams", [])

        score_us: Optional[int] = None
        score_them: Optional[int] = None
        our_side: Optional[str] = None
        outcome: Optional[str] = None

        for i, team in enumerate(teams):
            role = team.get("role", "").lower()          # "Attack" or "Defense"
            score = team.get("score")
            won = team.get("won", False)

            if our_team_index is not None and i == our_team_index:
                score_us = score
                our_side = "attack" if role == "attack" else "defense"
                outcome = "win" if won else "loss"
            else:
                score_them = score

        # ── Map ──────────────────────────────────────────────────
        map_data = data.get("map", {})
        map_id_raw = map_data.get("id")
        map_name = MAP_ID_LOOKUP.get(map_id_raw, map_data.get("name"))

        # ── Round object ─────────────────────────────────────────
        round_obj = Round(
            round_id=None,
            match_id=None,
            round_number=data.get("roundNumber", 0),
            side=our_side or "attack",
            site=data.get("site", ""),
            outcome=outcome or "loss",
            resources=None,
            player_stats=[],
        )

        meta = {
            "map_name": map_name,
            "score_us": score_us,
            "score_them": score_them,
        }

        return round_obj, meta