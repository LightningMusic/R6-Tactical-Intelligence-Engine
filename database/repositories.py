from pathlib import Path
from typing import List, Optional
from datetime import datetime
from models.map import Map
from models.player import Player
from database.db_manager import DatabaseManager
from models.match import Match
from models.round import Round
from models.round_resources import RoundResources
from models.player_round_stats import PlayerRoundStats
from models.operator import Operator
from models.gadget import Gadget


class Repository:
    """
    Central data access layer for the R6 Tactical Intelligence Engine.

    By default this talks to the client's own matches.db (via a plain
    DatabaseManager()). Pass an explicit `db_manager` — or `db_path`/
    `schema_path` — to point the same Repository at a different SQLite
    file entirely. This is what lets the server (Milestone 4, phase 2)
    reuse this exact class, and everything built on it (MetricsEngine,
    IntelEngine), against its own server-side match database instead of
    duplicating the data-access layer.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        db_path: Optional[Path] = None,
        schema_path: Optional[Path] = None,
    ):
        self.db = db_manager or DatabaseManager(db_path=db_path, schema_path=schema_path)

    # =====================================================
    # Operators
    # =====================================================

    def get_all_operators(self) -> List[Operator]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM operators").fetchall()
            return [
                Operator(
                    operator_id=row["operator_id"],
                    name=row["name"],
                    side=row["side"],
                    ability_name=row["ability_name"],
                    ability_max_count=row["ability_max_count"],
                )
                for row in rows
            ]

    def get_operator_by_id(self, operator_id: int) -> Optional[Operator]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM operators WHERE operator_id = ?",
                (operator_id,),
            ).fetchone()

            if not row:
                return None

            return Operator(
                operator_id=row["operator_id"],
                name=row["name"],
                side=row["side"],
                ability_name=row["ability_name"],
                ability_max_count=row["ability_max_count"],
            )

# ── Add these two methods to the Repository class in database/repositories.py ──

    def get_operator_by_name(self, name: str) -> Optional["Operator"]:
        """Exact case-insensitive operator name lookup."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM operators WHERE LOWER(name) = LOWER(?)",
                (name,)
            ).fetchone()
            if not row:
                return None
            return Operator(
                operator_id=row["operator_id"],
                name=row["name"],
                side=row["side"],
                ability_name=row["ability_name"],
                ability_max_count=row["ability_max_count"],
            )

    def get_operator_by_name_fuzzy(self, name: str) -> Optional["Operator"]:
        """
        Fuzzy operator lookup for r6-dissect name variations.
        Handles accent stripping (Jager→Jäger, Capitao→Capitão, Tubarao→Tubarão),
        substring matching, and alpha-only fallback.
        """
        import unicodedata

        def strip_accents(s: str) -> str:
            """Normalize unicode: Jäger→Jager, Capitão→Capitao, Nøkk→Nokk"""
            # "ø"/"Ø" have no NFD decomposition (they're distinct letters,
            # not a base letter + combining diacritic), so they pass through
            # the NFD strip below untouched -- translate them explicitly
            # first, or r6-dissect's actual output "Nokk" never matches this
            # table's stored name "Nøkk" via any pass below.
            s = s.replace("ø", "o").replace("Ø", "O")
            return "".join(
                c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn"
            )

        search = name.lower().strip()
        search_stripped = strip_accents(search)

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM operators").fetchall()

        def make_op(row) -> "Operator":
            return Operator(
                operator_id=row["operator_id"],
                name=row["name"],
                side=row["side"],
                ability_name=row["ability_name"],
                ability_max_count=row["ability_max_count"],
            )

        # Pass 1: accent-stripped exact match (Jager == Jäger)
        for row in rows:
            db_stripped = strip_accents(row["name"].lower())
            if search_stripped == db_stripped:
                return make_op(row)

        # Pass 2: accent-stripped substring match
        for row in rows:
            db_stripped = strip_accents(row["name"].lower())
            if search_stripped in db_stripped or db_stripped in search_stripped:
                return make_op(row)

        # Pass 3: direct substring on original
        for row in rows:
            db_name = row["name"].lower()
            if search in db_name or db_name in search:
                return make_op(row)

        # Pass 4: alpha-only compare (strips all non-alpha)
        search_alpha = "".join(c for c in search_stripped if c.isalpha())
        for row in rows:
            db_alpha = "".join(
                c for c in strip_accents(row["name"].lower()) if c.isalpha()
            )
            if search_alpha == db_alpha:
                return make_op(row)

        return None
    # =====================================================
    # Gadgets
    # =====================================================

    def get_gadgets_for_operator(self, operator_id: int) -> List[Gadget]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT g.*, ogo.max_count
                FROM gadgets g
                JOIN operator_gadget_options ogo
                ON g.gadget_id = ogo.gadget_id
                WHERE ogo.operator_id = ?
                """,
                (operator_id,),
            ).fetchall()

            return [
                Gadget(
                    gadget_id=row["gadget_id"],
                    name=row["name"],
                    category=row["category"],
                    max_count=row["max_count"],
                )
                for row in rows
            ]

    # =====================================================
    # Matches
    # =====================================================

    def insert_match(self, match: Match) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO matches (datetime, opponent_name, map, result, recording_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    match.datetime_played.isoformat(),
                    match.opponent_name,
                    match.map,
                    match.result,
                    match.recording_path,
                ),
            )
            conn.commit()

            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve match ID after insert.")

            return int(cursor.lastrowid)

    # =====================================================
    # Rounds
    # =====================================================

    def insert_round(self, round_obj: Round, match_id: int) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rounds (match_id, round_number, side, site, outcome)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    round_obj.round_number,
                    round_obj.side,
                    round_obj.site,
                    round_obj.outcome,
                ),
            )
            conn.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve round ID after insert.")
            return int(cursor.lastrowid)

    # =====================================================
    # Round Resources
    # =====================================================

    def insert_round_resources(self, resources: RoundResources, round_id: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO round_resources (
                    round_id,
                    team_drones_start,
                    team_drones_lost,
                    team_reinforcements_start,
                    team_reinforcements_used
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    resources.team_drones_start,
                    resources.team_drones_lost,
                    resources.team_reinforcements_start,
                    resources.team_reinforcements_used,
                ),
            )
            conn.commit()

    # =====================================================
    # Player Round Stats
    # =====================================================

    def insert_player_round_stats(
        self,
        stats: PlayerRoundStats,
        round_id: int,
        player_id: int,
    ) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO player_round_stats (
                    round_id,
                    player_id,
                    operator_id,
                    kills,
                    deaths,
                    assists,
                    engagements_taken,
                    engagements_won,
                    ability_start,
                    ability_used,
                    secondary_gadget_id,
                    secondary_start,
                    secondary_used,
                    plant_attempted,
                    plant_successful
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    player_id,
                    stats.operator.operator_id,
                    stats.kills,
                    stats.deaths,
                    stats.assists,
                    stats.engagements_taken,
                    stats.engagements_won,
                    stats.ability_start,
                    stats.ability_used,
                    stats.secondary_gadget.gadget_id
                    if stats.secondary_gadget
                    else None,
                    stats.secondary_start,
                    stats.secondary_used,
                    int(stats.plant_attempted),
                    int(stats.plant_successful),
                ),
            )
            conn.commit()

    # =====================================================
    # Players
    # =====================================================

    def insert_player(self, player: Player) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO players (name, is_team_member)
                VALUES (?, ?)
                """,
                (player.name, int(player.is_team_member)),
            )
            conn.commit()

            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve player ID after insert.")

            return int(cursor.lastrowid)


    def clear_team_players(self):
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM players WHERE is_team_member = 1"
            )
            conn.commit()

    # =====================================================
    # Maps
    # =====================================================
    def get_all_maps(self):
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM maps ORDER BY name").fetchall()
            return [row["name"] for row in rows]
    def insert_map(self, map_name: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO maps (name)
                VALUES (?)
                """,
                (map_name,),
            )
            conn.commit()

            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve map ID after insert.")

            return int(cursor.lastrowid)
    def get_map_id_by_name(self, name: str) -> Optional[int]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT map_id FROM maps WHERE name = ?", (name,)
            ).fetchone()
            return int(row["map_id"]) if row else None

    def get_map_by_id(self, map_id: int) -> Optional["Map"]:
        from models.map import Map
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM maps WHERE map_id = ?", (map_id,)
            ).fetchone()
            if not row:
                return None
            return Map(
                map_id=row["map_id"],
                name=row["name"],
                is_active_pool=bool(row["is_active_pool"]),
            )
    # =====================================================
    # FULL MATCH LOADER
    # =====================================================
    def create_match(self, opponent_name: str, map_name: str) -> int:
        match = Match(
            match_id=None,
            datetime_played=datetime.now(),
            opponent_name=opponent_name,
            map=map_name,
            result=None,
            recording_path="",
            rounds=[]
        )
        return self.insert_match(match)
    
    def get_match_full(self, match_id: int) -> Optional[Match]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchall()
            if not rows:
                return None
            return self._build_full_matches(conn, rows, strict=True)[0]

    def get_all_matches_full(self) -> list[Match]:
        """Every match with rounds and player stats, oldest first. A match
        whose rows are inconsistent is skipped rather than failing the lot."""
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM matches ORDER BY datetime").fetchall()
            return self._build_full_matches(conn, rows, strict=False)

    def _build_full_matches(self, conn, match_rows, strict: bool) -> list[Match]:
        """Loads the given matches with a fixed handful of queries in total,
        instead of one connection per operator and player per stat row (which
        took ~3 s for 37 matches)."""
        if not match_rows:
            return []
        ids = [r["match_id"] for r in match_rows]
        marks = ",".join("?" * len(ids))

        round_rows = conn.execute(
            f"SELECT * FROM rounds WHERE match_id IN ({marks}) ORDER BY match_id, round_number", ids
        ).fetchall()
        round_ids = [r["round_id"] for r in round_rows]
        resources: dict[int, object] = {}
        stats_by_round: dict[int, list] = {}
        if round_ids:
            rmarks = ",".join("?" * len(round_ids))
            for r in conn.execute(f"SELECT * FROM round_resources WHERE round_id IN ({rmarks})", round_ids):
                resources.setdefault(r["round_id"], r)
            for s in conn.execute(
                f"SELECT * FROM player_round_stats WHERE round_id IN ({rmarks}) ORDER BY stat_id", round_ids
            ):
                stats_by_round.setdefault(s["round_id"], []).append(s)

        operators = {
            r["operator_id"]: Operator(
                operator_id=r["operator_id"], name=r["name"], side=r["side"],
                ability_name=r["ability_name"], ability_max_count=r["ability_max_count"],
            )
            for r in conn.execute("SELECT * FROM operators")
        }
        gadgets = {
            r["gadget_id"]: Gadget(gadget_id=r["gadget_id"], name=r["name"], category=r["category"])
            for r in conn.execute("SELECT * FROM gadgets")
        }
        players = {
            r["player_id"]: Player(
                player_id=r["player_id"], name=r["name"], is_team_member=bool(r["is_team_member"]),
            )
            for r in conn.execute("SELECT * FROM players")
        }

        rounds_by_match: dict[int, list] = {}
        for rr in round_rows:
            rounds_by_match.setdefault(rr["match_id"], []).append(rr)

        out: list[Match] = []
        for mr in match_rows:
            try:
                out.append(self._assemble_match(
                    mr, rounds_by_match.get(mr["match_id"], []), resources, stats_by_round,
                    operators, gadgets, players,
                ))
            except RuntimeError:
                if strict:
                    raise
        return out

    @staticmethod
    def _assemble_match(mr, round_rows, resources, stats_by_round, operators, gadgets, players) -> Match:
        match = Match(
            match_id=mr["match_id"],
            datetime_played=datetime.fromisoformat(mr["datetime"]),
            opponent_name=mr["opponent_name"],
            map=mr["map"],
            result=mr["result"],
            recording_path=mr["recording_path"],
            rounds=[],
        )
        for rr in round_rows:
            res = resources.get(rr["round_id"])
            if res is None:
                raise RuntimeError("Round missing resource entry.")
            round_obj = Round(
                round_id=rr["round_id"],
                match_id=rr["match_id"],
                round_number=rr["round_number"],
                side=rr["side"],
                site=rr["site"],
                outcome=rr["outcome"],
                resources=RoundResources(
                    resource_id=res["resource_id"],
                    round_id=res["round_id"],
                    side=rr["side"],
                    team_drones_start=res["team_drones_start"],
                    team_drones_lost=res["team_drones_lost"],
                    team_reinforcements_start=res["team_reinforcements_start"],
                    team_reinforcements_used=res["team_reinforcements_used"],
                ),
                player_stats=[],
            )
            for s in stats_by_round.get(rr["round_id"], []):
                operator = operators.get(s["operator_id"])
                if operator is None:
                    raise RuntimeError("Invalid operator reference in stats.")
                player = players.get(s["player_id"])
                if player is None:
                    raise RuntimeError("Invalid player reference in stats.")
                round_obj.player_stats.append(PlayerRoundStats(
                    stat_id=s["stat_id"],
                    round_id=s["round_id"],
                    player_id=s["player_id"],
                    player=player,
                    operator=operator,
                    kills=s["kills"],
                    deaths=s["deaths"],
                    assists=s["assists"],
                    engagements_taken=s["engagements_taken"],
                    engagements_won=s["engagements_won"],
                    ability_start=s["ability_start"],
                    ability_used=s["ability_used"],
                    secondary_gadget=gadgets.get(s["secondary_gadget_id"]) if s["secondary_gadget_id"] else None,
                    secondary_start=s["secondary_start"],
                    secondary_used=s["secondary_used"],
                    plant_attempted=bool(s["plant_attempted"]),
                    plant_successful=bool(s["plant_successful"]),
                ))
            match.rounds.append(round_obj)
        return match

    def data_signature(self) -> tuple:
        """Cheap fingerprint that changes whenever matches, rounds, stats,
        or the team roster change -- lets views skip reloading when nothing
        is new."""
        with self.db.get_connection() as conn:
            return tuple(conn.execute(
                """SELECT (SELECT COUNT(*) FROM matches), (SELECT MAX(match_id) FROM matches),
                          (SELECT COUNT(*) FROM rounds), (SELECT COUNT(*) FROM player_round_stats),
                          (SELECT COUNT(*) FROM players WHERE is_team_member = 1),
                          (SELECT COUNT(*) FROM player_aliases),
                          (SELECT group_concat(match_id || ':' || COALESCE(result, '') || ':' || COALESCE(map, ''))
                             FROM matches)"""
            ).fetchone())

    def get_frequent_unlinked_players(self, min_matches: int = 3, limit: int = 8) -> list[tuple[str, int]]:
        """Non-team players who show up in several different matches. The
        same opponents rarely meet you twice, so these are almost always
        teammates whose usernames aren't linked in Settings > Players yet."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT p.name, COUNT(DISTINCT r.match_id) AS n
                   FROM player_round_stats s
                   JOIN rounds r ON r.round_id = s.round_id
                   JOIN players p ON p.player_id = s.player_id
                   WHERE p.is_team_member = 0
                   GROUP BY p.player_id HAVING n >= ?
                   ORDER BY n DESC, p.name LIMIT ?""",
                (min_matches, limit),
            ).fetchall()
            return [(r["name"], r["n"]) for r in rows]

    def get_player_by_id(self, player_id: int) -> Optional[Player]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM players WHERE player_id = ?",
                (player_id,),
            ).fetchone()

            if not row:
                return None

            return Player(
                player_id=row["player_id"],
                name=row["name"],
                is_team_member=bool(row["is_team_member"]),
            )

    def get_team_players(self) -> list[Player]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM players WHERE is_team_member = 1"
            ).fetchall()

            return [
                Player(
                    player_id=row["player_id"],
                    name=row["name"],
                    is_team_member=True,
                )
                for row in rows
            ]

    # =====================================================
    # Player Aliases (Milestone 6 — "tie usernames to a name")
    # =====================================================

    def get_player_aliases(self, player_id: int) -> list[str]:
        """Known in-game usernames tied to this player, besides their
        canonical name."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT alias FROM player_aliases WHERE player_id = ? ORDER BY alias",
                (player_id,),
            ).fetchall()
            return [row["alias"] for row in rows]

    def get_team_players_with_aliases(self) -> list[dict]:
        """Team players plus their alias list, in one call — what the
        Settings > Players tab renders. Each item:
        {"player_id": int, "name": str, "aliases": [str, ...]}
        """
        players = self.get_team_players()
        out = []
        for p in players:
            out.append({
                "player_id": p.player_id,
                "name": p.name,
                "aliases": self.get_player_aliases(p.player_id),
            })
        return out

    def set_player_aliases(self, player_id: int, aliases: list[str]) -> None:
        """Replaces a player's full alias list. An alias already claimed by
        a *different* player is silently skipped (it stays with its real
        owner) rather than raising, so re-saving Settings never errors out
        over a stray leftover alias."""
        clean = sorted({a.strip() for a in aliases if a.strip()})
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM player_aliases WHERE player_id = ?", (player_id,)
            )
            for alias in clean:
                try:
                    conn.execute(
                        "INSERT INTO player_aliases (player_id, alias) VALUES (?, ?)",
                        (player_id, alias),
                    )
                except Exception:
                    # UNIQUE(alias) — already claimed by another player_id.
                    pass
            conn.commit()

    def resolve_player_by_username(self, username: str) -> Optional[Player]:
        """
        The single source of truth for "which player is this raw in-game
        username": checks the canonical name first, then every player's
        alias list, both case-insensitively. Returns None if nothing
        matches (caller falls back to auto-creating a guest/ghost player).
        """
        if not username:
            return None
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM players WHERE LOWER(name) = LOWER(?)",
                (username,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT p.* FROM players p
                    JOIN player_aliases a ON a.player_id = p.player_id
                    WHERE a.alias = ? COLLATE NOCASE
                    """,
                    (username,),
                ).fetchone()
            if row is None:
                return None
            return Player(
                player_id=row["player_id"],
                name=row["name"],
                is_team_member=bool(row["is_team_member"]),
            )

    def update_player_name(self, player_id: int, new_name: str) -> None:
        """Renames a player in place — never touches player_round_stats,
        so match history stays attached to the same player_id."""
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("Player name cannot be empty.")
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE players SET name = ? WHERE player_id = ?",
                (new_name, player_id),
            )
            conn.commit()

    def get_non_team_players(self) -> list[Player]:
        """Guest/opponent/ghost players — is_team_member = 0. Used to
        populate the 'merge into a team player' picker in Settings."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM players WHERE is_team_member = 0 ORDER BY name"
            ).fetchall()
            return [
                Player(player_id=row["player_id"], name=row["name"], is_team_member=False)
                for row in rows
            ]

    def merge_player(self, ghost_player_id: int, target_player_id: int) -> None:
        """
        Folds a duplicate/"ghost" player (usually created because a raw
        username didn't match any team player) into the correct team
        player: every stat row moves over, the ghost's name becomes a new
        alias of the target (so future imports of that same username match
        automatically), and the ghost row is deleted.

        Refuses to merge if both players have a stat row for the same
        round — that means they're genuinely two different people, not a
        duplicate, and merging would silently destroy one of their rows.
        """
        if ghost_player_id == target_player_id:
            raise ValueError("Cannot merge a player into itself.")

        with self.db.get_connection() as conn:
            ghost = conn.execute(
                "SELECT * FROM players WHERE player_id = ?", (ghost_player_id,)
            ).fetchone()
            target = conn.execute(
                "SELECT * FROM players WHERE player_id = ?", (target_player_id,)
            ).fetchone()
            if ghost is None or target is None:
                raise ValueError("Both players must exist to merge.")

            conflict = conn.execute(
                """
                SELECT g.round_id FROM player_round_stats g
                JOIN player_round_stats t
                  ON t.round_id = g.round_id AND t.player_id = ?
                WHERE g.player_id = ?
                LIMIT 1
                """,
                (target_player_id, ghost_player_id),
            ).fetchone()
            if conflict is not None:
                raise ValueError(
                    "These two players both have stats in the same round — "
                    "they can't be the same person, so the merge was cancelled."
                )

            conn.execute(
                "UPDATE player_round_stats SET player_id = ? WHERE player_id = ?",
                (target_player_id, ghost_player_id),
            )
            # Carry the ghost's own name, plus any aliases it had already
            # picked up (e.g. from an earlier merge into it), over to the
            # target — one at a time so a single UNIQUE clash (an alias
            # already claimed elsewhere) just gets skipped instead of
            # aborting the whole merge.
            ghost_aliases = [ghost["name"]] + [
                r["alias"] for r in conn.execute(
                    "SELECT alias FROM player_aliases WHERE player_id = ?",
                    (ghost_player_id,),
                ).fetchall()
            ]
            for alias in ghost_aliases:
                try:
                    conn.execute(
                        "INSERT INTO player_aliases (player_id, alias) VALUES (?, ?)",
                        (target_player_id, alias),
                    )
                except Exception:
                    pass  # already an alias of someone — leave it as-is

            # Cascades to any remaining player_aliases rows for the ghost.
            conn.execute("DELETE FROM players WHERE player_id = ?", (ghost_player_id,))
            conn.commit()

    def get_all_matches(self) -> list[Match]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM matches ORDER BY datetime").fetchall()
            return [
                Match(
                    match_id=row["match_id"],
                    datetime_played=datetime.fromisoformat(row["datetime"]),
                    opponent_name=row["opponent_name"],
                    map=row["map"],
                    result=row["result"],
                    recording_path=row["recording_path"],
                    rounds=[]
                )
                for row in rows
            ]

    def export_match_to_csv(self, match_id, path):
        import csv

        match = self.get_match_full(match_id)

        if match is None:
            raise ValueError(f"Match {match_id} not found")

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
                "Round", "Player", "Operator",
                "Kills", "Deaths", "Assists",
                "Engagements Taken", "Engagements Won"
            ])

            for r in match.rounds:
                for ps in r.player_stats:
                    writer.writerow([
                        r.round_number,
                        ps.player.name,
                        ps.operator.name,
                        ps.kills,
                        ps.deaths,
                        ps.assists,
                        ps.engagements_taken,
                        ps.engagements_won
                    ])

    # =====================================================
    # Transcripts
    # =====================================================

    def get_transcript_text(self, match_id: int) -> Optional[str]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT raw_text FROM transcripts WHERE match_id = ?",
                (match_id,),
            ).fetchone()

            if not row:
                return None

            return row["raw_text"]

    def get_transcript_processed_data(self, match_id: int) -> Optional[dict]:
        """The full parsed-transcript JSON blob for a match (callouts,
        speaker segments, etc.) — see analysis/transcript_parser.py
        to_storage_dict(). None if no transcript was ever stored."""
        import json
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT processed_segments_json FROM transcripts WHERE match_id = ?",
                (match_id,),
            ).fetchone()
        if not row or not row["processed_segments_json"]:
            return None
        try:
            return json.loads(row["processed_segments_json"])
        except Exception:
            return None

    # =====================================================
    # Speaker labels (Milestone 6 — manual speaker tagging)
    # =====================================================

    def get_speaker_labels(self, match_id: int) -> dict[str, Optional[int]]:
        """{"Speaker_1": player_id_or_None, ...} for every speaker tag that
        has been reviewed for this match (via the tagging dialog). A tag
        diarization produced but that hasn't been reviewed yet simply
        won't be a key here."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT speaker_tag, player_id FROM transcript_speaker_labels WHERE match_id = ?",
                (match_id,),
            ).fetchall()
            return {row["speaker_tag"]: row["player_id"] for row in rows}

    def set_speaker_label(
        self, match_id: int, speaker_tag: str, player_id: Optional[int]
    ) -> None:
        """Assigns (or clears, with player_id=None) which player a
        diarized speaker cluster is."""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO transcript_speaker_labels (match_id, speaker_tag, player_id)
                VALUES (?, ?, ?)
                ON CONFLICT(match_id, speaker_tag) DO UPDATE SET player_id = excluded.player_id
                """,
                (match_id, speaker_tag, player_id),
            )
            conn.commit()

    def get_named_speaker_map(self, match_id: int) -> dict[str, str]:
        """{"Speaker_1": "PlayerName", ...} — only for speakers that have
        actually been assigned a player. Used anywhere a transcript/comms
        summary is rendered (AI prompts, reports) so a tagged match shows
        real names instead of "Speaker_1, Speaker_2...".
        """
        labels = self.get_speaker_labels(match_id)
        out: dict[str, str] = {}
        for tag, pid in labels.items():
            if pid is None:
                continue
            player = self.get_player_by_id(pid)
            if player is not None:
                out[tag] = player.name
        return out


    # =====================================================
    # Simple Match Fetch
    # =====================================================

    def get_match(self, match_id: int) -> Optional[Match]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM matches WHERE match_id = ?",
                (match_id,),
            ).fetchone()

            if not row:
                return None

            return Match(
                match_id=row["match_id"],
                datetime_played=datetime.fromisoformat(row["datetime"]),
                opponent_name=row["opponent_name"],
                map=row["map"],
                result=row["result"],
                recording_path=row["recording_path"],
                rounds=[],
            )
        
    def get_gadget_options(self, operator_id: int):
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM operator_gadget_options
                WHERE operator_id = ?
                """,
                (operator_id,),
            ).fetchall()

            return rows  # simple for now (you can model it later)