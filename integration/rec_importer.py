import subprocess
import json
import time
import tempfile
import os
import sys
from pathlib import Path
from typing import Optional, Callable

from app.config import R6_DISSECT_PATH
from models.import_result import ImportResult, ImportStatus
from models.round import Round


# Suppress CMD windows on Windows
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

MAX_RETRIES     = 5
RETRY_DELAY     = 2.0
DISSECT_TIMEOUT = 90


class RecImporter:
    """
    Parses match replay folders using r6-dissect.

    For each round, extracts:
    - Round metadata (side, site, outcome, map)
    - Per-player stats (kills, deaths, assists — joined from the
      top-level "stats" array onto each player by username; see
      _build_stats_by_username)
    - Kill feed events from matchFeedback
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
        map_game_id:   Optional[int] = None
        last_scored_round = 0
        played_at:     Optional[str] = None
        score_us:      Optional[int] = None
        score_them:    Optional[int] = None

        round_timestamps: list[str] = []
        timeline_rounds: list[dict] = []

        # r6-dissect is a separate process per file, so the files are parsed
        # in parallel; results are still handled in round order below.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(rec_files))) as pool:
            dissected = list(pool.map(self._run_dissect_with_retry, rec_files))

        for rec_file, (raw, err) in zip(rec_files, dissected):
            self._log(f"  Parsing {rec_file.name}...")

            if raw is None:
                self._log(f"  ✗ {rec_file.name} — all {MAX_RETRIES} attempts failed: {err}")
                failed_files.append(rec_file.name)
                continue

            try:
                round_obj, meta = self._parse_round(raw)
                parsed_rounds.append(round_obj)
                self._attach_gadget_usage(rec_file, round_obj)
                try:
                    timeline_rounds.append(self.timeline_round(raw, round_obj.round_number))
                except Exception as tl_err:
                    self._log(f"  (comms timeline data unavailable for {rec_file.name}: {tl_err})")

                if map_name is None and meta.get("map_name"):
                    map_name = meta["map_name"]
                    map_game_id = meta.get("map_game_id")
                    self._log(f"  Map (as the replay reports it): {map_name}")
                # A replay's team scores are the running score after that
                # round, so the match score is the LAST round's. Taking the
                # first file's (as this used to) made every match 1-0 or 0-1.
                ts = meta.get("timestamp")
                if ts:
                    round_timestamps.append(str(ts))
                if ts and (played_at is None or str(ts) < played_at):
                    played_at = str(ts)
                if meta.get("score_us") is not None and round_obj.round_number >= last_scored_round:
                    last_scored_round = round_obj.round_number
                    score_us = meta["score_us"]
                    score_them = meta.get("score_them")

                our_stats   = [p for p in round_obj.raw_player_stats if p.get("is_our_team")]
                their_stats = [p for p in round_obj.raw_player_stats if not p.get("is_our_team")]
                our_k = sum(p["kills"]  for p in our_stats)
                our_d = sum(p["deaths"] for p in our_stats)

                # Log kill feed summary
                ev = round_obj.round_events
                fb_note = ""
                if ev:
                    fb_note = f" | feed: {len(ev.kills)}K"
                    if ev.first_blood_killer:
                        fb_note += f" | FB:{ev.first_blood_killer}"
                    if ev.clutch_player:
                        fb_note += f" | clutch:{ev.clutch_player}"

                # The game sometimes only writes part of a round's replay
                # (a few hundred KB instead of ~9 MB): the round header, and so
                # the win/loss taken from its score, is intact, but no kills
                # were recorded. Worth saying, since it reads as a round
                # where nobody did anything.
                if round_obj.raw_player_stats and not any(
                    p["kills"] or p["deaths"] for p in round_obj.raw_player_stats
                ) and not (ev and ev.kills):
                    fb_note += " | ⚠ incomplete replay -- no kills recorded, result from score header"

                self._log(
                    f"  ✓ R{round_obj.round_number} "
                    f"| {round_obj.side} | {round_obj.outcome}"
                    f" | site: {round_obj.site or '?'}"
                    f" | K/D: {our_k}/{our_d}"
                    f" | {len(our_stats)} ours, {len(their_stats)} theirs"
                    f"{fb_note}"
                )

            except Exception as parse_err:
                self._log(f"  ✗ {rec_file.name} — parse error: {parse_err}")
                import traceback
                self._log(f"    {traceback.format_exc()}")
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
                map_game_id=map_game_id,
                dissect_map_name=map_name,
            )

        if failed_files:
            msg    = f"{len(parsed_rounds)}/{len(rec_files)} rounds parsed. Failed: {', '.join(failed_files)}"
            status = ImportStatus.PARTIAL_FAILURE
            self._log(f"PARTIAL: {msg}")
        else:
            msg    = None
            status = ImportStatus.SUCCESS
            self._log(f"SUCCESS: {len(parsed_rounds)} rounds from {folder.name}")

        return ImportResult(
            status=status,
            map_name=map_name,
            map_game_id=map_game_id,
            dissect_map_name=map_name,
            score_us=score_us,
            score_them=score_them,
            rounds=parsed_rounds,
            error_message=msg,
            played_at=played_at,
            round_timestamps=round_timestamps,
            timeline_rounds=timeline_rounds,
        )

    def _attach_gadget_usage(self, rec_file: Path, round_obj: Round) -> None:
        """Adds each player's operator-gadget charges and uses, which r6-dissect
        doesn't read, from the replay's own state stream. Nothing is added when
        the replay can't be read (so 'not measured' never looks like 'not used')."""
        try:
            from integration.replay_utility import analyze_rec
            usage = analyze_rec(rec_file, [p["username"] for p in round_obj.raw_player_stats if p.get("username")])
        except Exception as e:                                       # noqa: BLE001 - optional enrichment
            self._log(f"  (gadget usage unavailable for {rec_file.name}: {e})")
            return
        if not usage:
            return
        for p in round_obj.raw_player_stats:
            u = usage.get(p.get("username"))
            p["gadget_start"] = u.start if u else 0      # 0: this operator has no countable gadget
            p["gadget_used"] = u.used if u else 0
        if round_obj.round_events is not None:
            round_obj.round_events.utility_tracked = True

    @classmethod
    def timeline_round(cls, data: dict, round_number: int) -> dict:
        """
        What the comms timeline needs from one round's replay, small enough
        to ship in a package's metadata: when the round started, who was on
        our team, and every kill-feed event with its elapsedSeconds (seconds
        since prep began -- see r6-dissect's readTime).
        """
        players = data.get("players") or []
        recording_id = data.get("recordingPlayerID")
        recorder = next((p for p in players if p.get("id") == recording_id), None)
        our_team = recorder.get("teamIndex") if recorder else None
        ours = [str(p.get("username") or "") for p in players
                if our_team is not None and p.get("teamIndex") == our_team]
        theirs = [str(p.get("username") or "") for p in players
                  if our_team is not None and p.get("teamIndex") != our_team]
        events = []
        for m in cls._extract_match_feedback(data):
            t = m.get("type")
            name = t.get("name") if isinstance(t, dict) else t
            if name in (None, "OperatorSwap", "Other"):
                continue
            events.append({
                "type": str(name),
                "username": m.get("username") or "",
                "target": m.get("target") or "",
                "headshot": bool(m.get("headshot")),
                "clock": m.get("time") or "",
                "elapsed": float(m.get("elapsedSeconds") or 0.0),
            })
        return {
            "round_number": int(round_number),
            "timestamp": str(data.get("timestamp") or ""),
            "recording_username": str(recorder.get("username") or "") if recorder else "",
            "ours": ours,
            "theirs": theirs,
            "events": events,
        }

    def import_multiple_folders(
        self,
        folders: list[Path],
        max_workers: int = 1,
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

            summary = f"Folder {i+1} done: {result.status.value} — {len(result.rounds)} rounds"
            self._log(summary)
            if log_callback:
                log_callback(summary)

        return results

    # =====================================================
    # INTERNAL: Run r6-dissect (no CMD window)
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
                    [str(self.dissect_path), str(rec_file),
                     "--format", "json", "--output", str(tmp_path)],
                    capture_output=True, text=True,
                    timeout=DISSECT_TIMEOUT,
                    cwd=str(self.dissect_path.parent),
                    creationflags=_CREATE_NO_WINDOW,  # ← no CMD window
                )

                stderr = proc.stderr.strip() if proc.stderr else ""

                if "panic:" in stderr or "goroutine" in stderr:
                    panic_lines = [
                        line for line in stderr.splitlines()
                        if line.startswith("panic:") or "unknown" in line.lower()
                    ]
                    panic_msg = panic_lines[0] if panic_lines else stderr[:150]
                    if "role unknown for operator" in panic_msg.lower():
                        self._log(f"    r6-dissect crashed: {panic_msg}")
                        return None, f"r6-dissect outdated: {panic_msg}"
                    else:
                        last_error = f"r6-dissect panic: {panic_msg}"
                        self._log(f"    Attempt {attempt}: {last_error}")
                        continue

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

    def _parse_json_safe(self, text: str, filename: str) -> Optional[dict]:
        text = text.strip()
        if not text:
            return None
        json_start = text.find("{")
        if json_start == -1:
            self._log(f"    No JSON object found in output for {filename}")
            return None
        try:
            return json.loads(text[json_start:])
        except json.JSONDecodeError as e:
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
    # STATIC HELPERS
    # =====================================================

    @staticmethod
    def _safe_int(value: object) -> Optional[int]:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _determine_win_type(our_team: dict, their_team: dict) -> str:
        def norm(x: object) -> str:
            return str(x or "").strip()
        our_wc   = norm(our_team.get("winCondition"))
        their_wc = norm(their_team.get("winCondition"))
        wc = our_wc or their_wc
        if not wc:
            return "unknown"
        if wc == "KilledOpponents":
            return "kill"
        if wc in ("DefusedBomb", "DisabledDefuser"):
            return "plant"
        if wc == "Time":
            return "time"
        if wc in ("ExtractedHostage", "ProtectedHostage"):
            return "hostage"
        if wc == "SecuredArea":
            return "secure"
        return "other"

    @staticmethod
    def _determine_outcome(our_team: dict, their_team: dict, round_num: object) -> str:
        def norm(x: object) -> str:
            return str(x or "").strip()

        our_score   = RecImporter._safe_int(our_team.get("score"))
        their_score = RecImporter._safe_int(their_team.get("score"))
        our_start   = RecImporter._safe_int(our_team.get("startingScore")) \
                      if our_team.get("startingScore") is not None else our_score
        their_start = RecImporter._safe_int(their_team.get("startingScore")) \
                      if their_team.get("startingScore") is not None else their_score

        if (our_score is not None and their_score is not None
                and our_start is not None and their_start is not None):
            our_gained   = our_score   - our_start
            their_gained = their_score - their_start
            # Exactly one point changes hands per round. startingScore only
            # exists from Y9S4 on; in older replays it reads 0, so these
            # "gains" are really cumulative match scores and would call any
            # round a loss while the team trails overall (seen on a real
            # replay: round won, recorded as a loss at 2-3). Only trust the
            # delta when it is a genuine single-round delta.
            if our_gained + their_gained == 1:
                return "win" if our_gained == 1 else "loss"

        our_won   = our_team.get("won")
        their_won = their_team.get("won")
        if our_won is True and their_won is not True:
            return "win"
        if their_won is True and our_won is not True:
            return "loss"

        our_wc   = norm(our_team.get("winCondition"))
        their_wc = norm(their_team.get("winCondition"))
        role_raw      = norm(our_team.get("role")).lower()
        our_is_attack = role_raw in ("attack", "1")

        ATTACK_WIN  = {"KilledOpponents", "DefusedBomb", "ExtractedHostage", "SecuredArea"}
        DEFENSE_WIN = {"DisabledDefuser", "KilledOpponents", "Time", "ProtectedHostage"}

        if our_wc and not their_wc:
            return "win" if our_wc in (ATTACK_WIN if our_is_attack else DEFENSE_WIN) else "loss"
        if their_wc and not our_wc:
            return "loss" if their_wc in (DEFENSE_WIN if our_is_attack else ATTACK_WIN) else "win"
        if our_wc and their_wc:
            if our_is_attack:
                if our_wc in ATTACK_WIN and their_wc not in ATTACK_WIN:
                    return "win"
                if their_wc in ATTACK_WIN and our_wc not in ATTACK_WIN:
                    return "loss"
            else:
                if our_wc in DEFENSE_WIN and their_wc not in DEFENSE_WIN:
                    return "win"
                if their_wc in DEFENSE_WIN and our_wc not in DEFENSE_WIN:
                    return "loss"

        print(
            f"[RecImporter] Warning: ambiguous outcome for round {round_num}. "
            f"our_wc={our_wc!r} their_wc={their_wc!r} "
            f"our_won={our_won} their_won={their_won} → defaulting to loss"
        )
        return "loss"

    # =====================================================
    # INTERNAL: Parse one round
    # =====================================================

    @staticmethod
    def _build_stats_by_username(data: dict) -> dict[str, dict]:
        """
        The real per-round kill/death numbers, confirmed against r6-dissect's
        actual Go source (main.go's `output` struct and the `dissect`
        package's `PlayerRoundStats`) rather than guessed: they live in a
        *separate, top-level* "stats" array — one entry per player — joined
        back to `players` by username. They are NOT nested inside each
        player object (that's what _extract_player_stats used to assume,
        which is why kills/deaths were silently always zero).

        PlayerRoundStats also uses a `died` bool, not a `deaths` int, so
        that gets converted here on the way in.
        """
        by_username: dict[str, dict] = {}
        for entry in data.get("stats", []) or []:
            if not isinstance(entry, dict):
                continue
            username = str(entry.get("username") or "").strip()
            if not username:
                continue
            by_username[username.lower()] = {
                "kills":     entry.get("kills", 0),
                "deaths":    1 if entry.get("died") else 0,
                "assists":   entry.get("assists", 0),
                "headshots": entry.get("headshots", 0),
            }
        return by_username

    @staticmethod
    def _extract_player_stats(player: dict) -> dict:
        """
        Defensive fallback only — used when a player has no matching entry
        in the top-level "stats" array (older/unexpected r6-dissect output).
        Tries the same nested-key guesses this method always has; on
        current r6-dissect output these never match anything real, but
        keeping them costs nothing and avoids a hard regression if a future
        dissect version nests stats differently again.
        """
        # Primary: player.stats
        stats = player.get("stats")
        if isinstance(stats, dict) and stats:
            return stats

        # Some versions use player.roundStats or player.playerStats
        for key in ("roundStats", "playerStats", "stat"):
            alt = player.get(key)
            if isinstance(alt, dict) and alt:
                return alt

        # Fallback: check if kills/deaths are top-level on the player dict
        if "kills" in player or "deaths" in player:
            return {
                "kills":     player.get("kills", 0),
                "deaths":    player.get("deaths", 0),
                "assists":   player.get("assists", 0),
                "headshots": player.get("headshots", 0),
            }

        return {}

    @staticmethod
    def _extract_operator_name(player: dict) -> str:
        """
        r6-dissect's name for the operator -- "Operator(<id>)" when its
        built-in table doesn't know it yet. That placeholder is fine here:
        database/game_catalog.py resolves operators by game ID (and by the
        game's own roleName), not by this string.
        """
        op_data = player.get("operator")
        if isinstance(op_data, dict):
            name = op_data.get("name") or op_data.get("operatorName") or ""
            if name:
                return str(name).strip()

        for key in ("operatorName", "operator_name", "operatorname"):
            val = player.get(key)
            if val:
                return str(val).strip()

        return ""

    @staticmethod
    def _extract_operator_game_id(player: dict) -> Optional[int]:
        op_data = player.get("operator")
        if isinstance(op_data, dict):
            try:
                gid = int(op_data.get("id") or 0)
                return gid or None
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _extract_match_feedback(data: dict) -> list:
        """
        r6-dissect can store matchFeedback at the top level or
        nested inside rounds[0] or similar. Find it.
        """
        # Top-level (most common)
        fb = data.get("matchFeedback")
        if isinstance(fb, list) and fb:
            return fb

        # Sometimes nested under "rounds" array
        rounds_data = data.get("rounds")
        if isinstance(rounds_data, list):
            for r in rounds_data:
                if isinstance(r, dict):
                    fb2 = r.get("matchFeedback")
                    if isinstance(fb2, list) and fb2:
                        return fb2

        return []

    def _parse_round(self, data: dict) -> tuple[Round, dict]:
        recording_player_id           = data.get("recordingPlayerID")
        our_team_index: Optional[int] = None

        for player in data.get("players", []):
            if player.get("id") == recording_player_id:
                our_team_index = player.get("teamIndex")
                break

        teams = data.get("teams", [])
        score_us:   Optional[int] = None
        score_them: Optional[int] = None
        our_side:   Optional[str] = None
        outcome:    str           = "loss"

        if our_team_index is not None and len(teams) >= 2:
            our_team   = teams[our_team_index]
            other_idx  = 1 - our_team_index
            their_team = teams[other_idx]

            role_raw = str(our_team.get("role", "")).lower()
            our_side = "attack" if role_raw in ("attack", "1") else "defense"

            score_us   = RecImporter._safe_int(our_team.get("score"))
            score_them = RecImporter._safe_int(their_team.get("score"))

            outcome = RecImporter._determine_outcome(
                our_team, their_team, data.get("roundNumber", "?")
            )
        else:
            if len(teams) == 2:
                t0_won   = teams[0].get("won", False)
                our_side = "attack"
                outcome  = "win" if t0_won else "loss"
                print(
                    f"[RecImporter] Warning: could not find recording player "
                    f"in round {data.get('roundNumber','?')} — guessing from team 0"
                )

        # ── Build raw player stats ──────────────────────────────────
        # Real kills/deaths/assists live in the top-level "stats" array,
        # joined by username (see _build_stats_by_username). Fall back to
        # the old nested-key guesses only if a player has no entry there.
        stats_by_username = self._build_stats_by_username(data)
        raw_player_stats: list[dict] = []
        for player in data.get("players", []):
            team_idx   = player.get("teamIndex", -1)
            username   = str(player.get("username") or "").strip()
            op_name    = self._extract_operator_name(player)

            stats_raw = stats_by_username.get(username.lower())
            if stats_raw is None:
                stats_raw = self._extract_player_stats(player)

            kills    = int(stats_raw.get("kills",     0) or 0)
            deaths   = int(stats_raw.get("deaths",    0) or 0)
            assists  = int(stats_raw.get("assists",   0) or 0)
            headshots = int(stats_raw.get("headshots", 0) or 0)

            team_role = ""
            if isinstance(team_idx, int) and 0 <= team_idx < len(teams):
                team_role = str(teams[team_idx].get("role") or "").lower()

            raw_player_stats.append({
                "username":    username,
                "operator":    op_name,
                # What the game catalog learns from. roleName is the game's
                # own name for the operator ("FROST"); it's only recorded for
                # one team per replay, so it is often empty.
                "operator_game_id": self._extract_operator_game_id(player),
                "role_name":   str(player.get("roleName") or "").strip(),
                "side":        team_role if team_role in ("attack", "defense") else None,
                "kills":       kills,
                "deaths":      deaths,
                "assists":     assists,
                "headshots":   headshots,
                "teamIndex":   team_idx,
                "is_our_team": (team_idx == our_team_index),
            })

        # ── Parse kill feed — try multiple locations in JSON ───────
        round_events = None
        try:
            from analysis.event_parser import parse_round_events
            if our_team_index is not None:
                # Inject extracted feedback into a copy of data for the parser
                data_with_fb = dict(data)
                fb = self._extract_match_feedback(data)

                # Only a malformed replay is worth a log line here; an empty
                # feed is a truncated replay, which the round summary flags.
                if not fb:
                    raw_mf = data.get("matchFeedback")
                    if raw_mf is None:
                        print(f"[RecImporter] 'matchFeedback' missing from this round's JSON. "
                              f"Top-level keys: {sorted(data.keys())}")
                    elif not isinstance(raw_mf, list):
                        print(f"[RecImporter] 'matchFeedback' is not a list -- "
                              f"type={type(raw_mf).__name__}, value={str(raw_mf)[:200]!r}")

                data_with_fb["matchFeedback"] = fb
                round_events = parse_round_events(
                    data_with_fb,
                    our_team_index=our_team_index,
                    round_outcome=outcome,
                )
        except Exception as ev_err:
            print(f"[RecImporter] Event parse warning: {ev_err}")

        map_data = data.get("map", {}) or {}
        try:
            map_game_id: Optional[int] = int(map_data.get("id") or 0) or None
        except (TypeError, ValueError):
            map_game_id = None
        dissect_map_name = str(map_data.get("name") or "").strip() or None

        round_number = data.get("roundNumber", 0)
        if isinstance(round_number, int):
            round_number = round_number + 1

        round_obj = Round(
            round_id=None,
            match_id=None,
            round_number=max(1, round_number),
            side=our_side or "attack",
            site=str(data.get("site") or ""),
            outcome=outcome,
            resources=None,
            player_stats=[],
            raw_player_stats=raw_player_stats,
            round_events=round_events,
        )

        return round_obj, {
            "timestamp":        data.get("timestamp"),
            "map_name":         dissect_map_name,
            "map_game_id":      map_game_id,
            "dissect_map_name": dissect_map_name,
            "score_us":         score_us,
            "score_them":       score_them,
        }