"""
A teammate's own analyzer (2026-10-14, for Hector): they upload their own
replay folders from a browser (/solo), the server reads them on its own small
worker, and the numbers are worked out from THEIR point of view -- their kills
and deaths, opening duels, when and how they died, whether anyone could trade
them, drones, reinforcements -- plus trends across their matches, split by map,
side, operator and the setup (PC) they played on.

Kept apart from the team: solo matches never go into matches.db, so they can't
leak into team history, team metrics or voice profiles. Raw replay files are
deleted once read; only the numbers are kept.

Also the sensitivity plug-in's storage: settings snapshots from the player's
own tool, and the setups (name + mouse DPI) they play on.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import re
import shutil
import statistics
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Optional

from server.config import server_settings
from server.database import server_db

PREP_SEC = 45.0
SPAWN_PEEK_SEC = 20.0      # died this soon after the action started, on attack
TRADE_NEAR_M = 10.0        # a teammate closer than this (same floor) could trade
FLOOR_GAP_M = 2.0

MAX_FILES = 30             # a match is at most ~15 round files; overtime and spares fit easily
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_MATCH_BYTES = 500 * 1024 * 1024
MAX_PENDING_BYTES = 2 * 1024 * 1024 * 1024   # waiting to be read, per player
MAX_SNAPSHOT_SETTINGS = 400

FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,79}$")
REC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}\.rec$", re.IGNORECASE)
SETUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,31}$")
GAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,31}$")

_wake = threading.Event()
_started = False
_start_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _work_dir(owner: str, match_key: str) -> Path:
    safe = hashlib.sha1(f"{owner.lower()}\n{match_key}".encode()).hexdigest()[:16]
    return server_settings.DATA_DIR / "solo_work" / safe


# ── Uploads ──────────────────────────────────────────────────────────────────

class UploadError(ValueError):
    pass


def known(owner: str, folders: Iterable[str]) -> dict[str, str]:
    """{folder: status} for the folders this player has already uploaded (failed ones may be sent again)."""
    folders = [f for f in folders if isinstance(f, str)][:500]
    if not folders:
        return {}
    with server_db.get_connection() as conn:
        rows = conn.execute(
            f"SELECT match_key, status FROM solo_matches WHERE lower(owner) = ? AND match_key IN ({','.join('?' * len(folders))})",
            [owner.lower(), *folders]).fetchall()
    return {r["match_key"]: r["status"] for r in rows}


def store_upload(owner: str, folder: str, files: list[tuple[str, BinaryIO]], setup: Optional[str] = None,
                 tz_offset_min: Optional[int] = None) -> dict:
    """Saves one match folder's .rec files and queues them. Raises UploadError with a reason to show.
    tz_offset_min is the uploading browser's getTimezoneOffset(): the replay's clock is that PC's local time."""
    folder = (folder or "").strip()
    if tz_offset_min is not None and not -900 <= int(tz_offset_min) <= 900:
        raise UploadError("Time zone offset out of range.")
    if not FOLDER_RE.match(folder):
        raise UploadError("The folder name doesn't look like a match folder (Match-...).")
    if setup is not None:
        setup = setup.strip() or None
        if setup is not None and not SETUP_RE.match(setup):
            raise UploadError("Setup names are 1-32 letters, digits, spaces, . _ -")
    if not files:
        raise UploadError("No replay files were sent.")
    if len(files) > MAX_FILES:
        raise UploadError(f"Too many files for one match (at most {MAX_FILES}).")
    names = [n for n, _ in files]
    if len(set(n.lower() for n in names)) != len(names):
        raise UploadError("The same file was sent twice.")
    for n in names:
        if not REC_RE.match(n):
            raise UploadError(f"Only .rec replay files can be uploaded ({n!r} isn't one).")

    status = known(owner, [folder]).get(folder)
    if status in ("queued", "processing", "done"):
        return {"folder": folder, "status": "known"}

    with server_db.get_connection() as conn:
        pending = conn.execute("SELECT COALESCE(SUM(upload_bytes), 0) FROM solo_matches WHERE lower(owner) = ? "
                               "AND status IN ('queued', 'processing')", (owner.lower(),)).fetchone()[0]

    dest = _work_dir(owner, folder)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        for name, fh in files:
            size = 0
            with open(dest / name, "wb") as out:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise UploadError(f"{name} is larger than a replay file can be.")
                    if total > MAX_MATCH_BYTES:
                        raise UploadError("This match is larger than a match can be.")
                    if pending + total > MAX_PENDING_BYTES:
                        raise UploadError("Too much is still waiting to be read. Try again in a few minutes.")
                    out.write(chunk)
            if size == 0:
                raise UploadError(f"{name} is empty.")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    now = _now()
    with server_db.get_connection() as conn:
        conn.execute("""INSERT INTO solo_matches (owner, match_key, status, error, setup, upload_bytes, tz_offset_min,
                            uploaded_at, updated_at)
                        VALUES (?, ?, 'queued', NULL, ?, ?, ?, ?, ?)
                        ON CONFLICT(owner, match_key) DO UPDATE SET status = 'queued', error = NULL, setup = excluded.setup,
                            upload_bytes = excluded.upload_bytes, tz_offset_min = excluded.tz_offset_min,
                            uploaded_at = excluded.uploaded_at, updated_at = excluded.updated_at""",
                     (owner, folder, setup, total, None if tz_offset_min is None else int(tz_offset_min), now, now))
        conn.commit()
    start_worker()
    _wake.set()
    return {"folder": folder, "status": "queued", "bytes": total}


def delete_match(owner: str, match_key: str) -> bool:
    with server_db.get_connection() as conn:
        cur = conn.execute("DELETE FROM solo_matches WHERE lower(owner) = ? AND match_key = ? AND status != 'processing'",
                           (owner.lower(), match_key))
        conn.commit()
    shutil.rmtree(_work_dir(owner, match_key), ignore_errors=True)
    return cur.rowcount > 0


def set_match_setup(owner: str, match_key: str, setup: Optional[str]) -> bool:
    setup = (setup or "").strip() or None
    if setup is not None and not SETUP_RE.match(setup):
        raise UploadError("Setup names are 1-32 letters, digits, spaces, . _ -")
    with server_db.get_connection() as conn:
        cur = conn.execute("UPDATE solo_matches SET setup = ?, updated_at = ? WHERE lower(owner) = ? AND match_key = ?",
                           (setup, _now(), owner.lower(), match_key))
        conn.commit()
    return cur.rowcount > 0


# ── Worker ───────────────────────────────────────────────────────────────────

def start_worker() -> None:
    """One background thread for solo replays, separate from the team worker so a long transcription never
    holds them up (and they never hold one up: replay-only jobs take seconds)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    with server_db.get_connection() as conn:
        conn.execute("UPDATE solo_matches SET status = 'queued' WHERE status = 'processing'")
        conn.commit()
    threading.Thread(target=_loop, name="solo-worker", daemon=True).start()
    _wake.set()


def _loop() -> None:
    while True:
        _wake.wait(timeout=60)
        _wake.clear()
        while True:
            job = _claim()
            if job is None:
                break
            _process(job["owner"], job["match_key"], job["tz_offset_min"])


def _claim() -> Optional[dict]:
    with server_db.get_connection() as conn:
        row = conn.execute("SELECT owner, match_key, tz_offset_min FROM solo_matches WHERE status = 'queued' "
                           "ORDER BY uploaded_at LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE solo_matches SET status = 'processing', updated_at = ? WHERE owner = ? AND match_key = ?",
                     (_now(), row["owner"], row["match_key"]))
        conn.commit()
    return dict(row)


def _finish(owner: str, match_key: str, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    with server_db.get_connection() as conn:
        conn.execute(f"UPDATE solo_matches SET {sets}, updated_at = ? WHERE owner = ? AND match_key = ?",
                     [*cols.values(), _now(), owner, match_key])
        conn.commit()


def replay_time_utc(stamp: Optional[str], tz_offset_min: Optional[int]) -> Optional[str]:
    """The replay header's time as real UTC. r6-dissect labels it "Z" but the digits are the recording PC's
    local wall clock (see analysis/match_builder._played_at); the uploading browser's offset converts it. Without
    an offset the digits are returned without a zone, which a browser shows as its own local time."""
    if not stamp:
        return None
    try:
        local = datetime.fromisoformat(str(stamp).rstrip("Z").split("+")[0])
    except ValueError:
        return None
    if tz_offset_min is None:
        return local.isoformat(timespec="seconds")
    from datetime import timedelta
    return (local + timedelta(minutes=int(tz_offset_min))).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def _process(owner: str, match_key: str, tz_offset_min: Optional[int] = None) -> None:
    folder = _work_dir(owner, match_key)
    try:
        from integration.rec_importer import RecImporter
        result = RecImporter(dissect_path=server_settings.R6_DISSECT_PATH,
                             log_callback=lambda _m: None).import_match_folder(folder)
        if not result.rounds:
            _finish(owner, match_key, status="failed", error=result.error_message or "No rounds could be read.")
            return
        report = player_report(result, owner)
        if report is None:
            _finish(owner, match_key, status="failed", error=f"{owner} isn't in this match.")
            return
        report["played_at"] = replay_time_utc(report["played_at"], tz_offset_min)
        _finish(owner, match_key, status="done", error=None, played_at=report["played_at"], map=report["map"],
                rounds=report["totals"]["rounds"], won=report["totals"]["won"], report_json=json.dumps(report))
        print(f"[Solo] {owner}: {match_key} read ({report['map']}, {report['totals']['rounds']} rounds).")
    except Exception as e:                                       # noqa: BLE001 - a bad upload must not stop the worker
        print(f"[Solo] {owner}: {match_key} failed: {e}")
        _finish(owner, match_key, status="failed", error=str(e)[:300])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ── The numbers, from one player's point of view ─────────────────────────────

def _clock(time_str: str, t: Optional[float]) -> str:
    s = (time_str or "").strip()
    m = re.fullmatch(r"(?:0?0:)?0?(\d{1,2}):(\d{2})", s)
    if m:
        return f"{int(m.group(1))}:{m.group(2)}"
    if t is not None and t >= PREP_SEC:
        left = max(0, int(round(180 - (t - PREP_SEC))))
        return f"{left // 60}:{left % 60:02d}"
    return s


def _match_db():
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{server_settings.MATCHES_DB_PATH}?mode=ro", uri=True, timeout=10)
        return conn
    except Exception:
        return None


def _display(name: str) -> str:
    name = re.sub(r"Y\d+(S\d+)?$", "", name or "").strip()
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def _map_name(result) -> str:
    gid = getattr(result, "map_game_id", None)
    conn = _match_db()
    if conn is not None and gid:
        try:
            row = conn.execute("SELECT map FROM matches WHERE map_game_id = ? AND map NOT LIKE 'Map(%' "
                               "ORDER BY match_id DESC LIMIT 1", (gid,)).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
        finally:
            conn.close()
    return _display(getattr(result, "map_name", None) or "Unknown")


def _operator_names(game_ids: set) -> dict:
    conn = _match_db()
    if conn is None or not game_ids:
        return {}
    try:
        ids = [g for g in game_ids if g]
        rows = conn.execute(f"""SELECT g.game_id, o.name FROM operator_game_ids g JOIN operators o
                                ON o.operator_id = g.operator_id WHERE g.game_id IN ({','.join('?' * len(ids))})""",
                            ids).fetchall()
        return {r[0]: r[1] for r in rows if r[1] and not r[1].startswith("Operator(")}
    except Exception:
        return {}
    finally:
        conn.close()


def _op(p: dict, catalog: dict) -> str:
    """The operator this player had: the team catalog's name for the replay's operator ID, else r6-dissect's
    name. roleName comes last: on 2026-10-13's replays it named the wrong operator for Hector in 4 of 16 rounds
    (Sens / Rauora / Brava / Nomad for Lion, Lion, Lion and Nøkk)."""
    gid = p.get("operator_game_id")
    if gid in catalog:
        return catalog[gid]
    name = p.get("operator") or ""
    if name and not name.startswith("Operator("):
        return _display(name)
    role = (p.get("role_name") or "").strip()
    return " ".join(w.capitalize() for w in role.split()) if role else ""


def _at(pts: list, t: float):
    from integration.positions import at
    return at(pts, t)


def player_report(result, me: str) -> Optional[dict]:
    """Everything the solo pages show for one match, from `me`'s side. None when `me` isn't in it."""
    me_l = me.lower()
    rounds = sorted(getattr(result, "rounds", None) or [], key=lambda r: r.round_number)
    catalog = _operator_names({p.get("operator_game_id") for r in rounds for p in r.raw_player_stats})
    out: list[dict] = []
    my_name = None
    flip_score = False
    for r in rounds:
        stats = {(p.get("username") or "").lower(): p for p in r.raw_player_stats}
        mine = stats.get(me_l)
        if mine is None:
            continue
        my_name = mine["username"]
        ours = bool(mine.get("is_our_team", True))
        flip_score = flip_score or not ours
        side = mine.get("side") or (r.side if ours else ("defense" if r.side == "attack" else "attack"))
        won = (r.outcome == "win") == ours
        team = mine.get("teamIndex")
        mates = [p["username"] for p in r.raw_player_stats if p.get("teamIndex") == team and p["username"] != my_name]
        ev = r.round_events
        kills = list(ev.kills) if ev else []
        row: dict[str, Any] = {
            "n": r.round_number, "side": side, "won": won, "site": r.site or "", "operator": _op(mine, catalog),
            "kills": [{"victim": k.victim, "clock": _clock(k.time_str, k.time_sec), "headshot": bool(k.headshot),
                       "opening": bool(k.is_opening)} for k in kills if (k.killer or "").lower() == me_l],
            "assists": int(mine.get("assists") or 0), "death": None, "nearest_mate_m": None,
            "drones": None, "drones_before_first_death": None, "reinforced": None,
        }
        death = next((k for k in kills if (k.victim or "").lower() == me_l), None)
        if death is None and int(mine.get("deaths") or 0):
            row["death"] = {"killer": None, "clock": "", "into_action": None, "opening": False, "traded": False,
                            "spawn_peek": False}
        elif death is not None:
            into = round(death.time_sec - PREP_SEC, 1) if death.time_sec is not None else None
            row["death"] = {"killer": death.killer, "clock": _clock(death.time_str, death.time_sec), "into_action": into,
                            "opening": bool(death.is_opening), "traded": bool(death.is_trade),
                            "spawn_peek": side == "attack" and into is not None and 0 <= into <= SPAWN_PEEK_SEC}
        pos = r.positions or {}
        players = pos.get("players") or {}
        died = pos.get("died") or {}
        if my_name in players and died.get(my_name) is not None:
            t = died[my_name]
            here = _at(players[my_name]["pts"], t - 0.5)
            others = [_at(players[m]["pts"], t - 0.5) for m in mates if m in players and died.get(m, 1e9) > t]
            others = [o for o in others if o]
            if here and others:
                d = min(math.dist(here[1:3], o[1:3]) + (100 if abs(here[3] - o[3]) > FLOOR_GAP_M else 0) for o in others)
                row["nearest_mate_m"] = round(d, 1) if d < 100 else "other floor"
        if side == "attack" and pos.get("drones") is not None and my_name in players:
            first = min((v for v in died.values() if v >= PREP_SEC), default=None)
            mine_d = [d for d in pos["drones"] if not d.get("prep") and d.get("who") == my_name]
            row["drones"] = len(mine_d)
            row["drones_before_first_death"] = sum(1 for d in mine_d if first is None or d["t"] < first)
        walls = pos.get("walls")
        if side == "defense" and walls:
            row["reinforced"] = sum(1 for w in walls.get("reinforcements") or [] if w.get("who") == my_name)
        out.append(row)
    if not out:
        return None
    us, them = getattr(result, "score_us", None), getattr(result, "score_them", None)
    if flip_score:
        us, them = them, us
    nemesis = collections.Counter(r["death"]["killer"] for r in out if r["death"] and r["death"]["killer"])
    return {"v": 1, "player": my_name, "map": _map_name(result), "played_at": getattr(result, "played_at", None),
            "score": [us, them], "rounds": out, "totals": summarize(out), "killed_by": nemesis.most_common()}


def summarize(rounds: list[dict]) -> dict:
    """Totals over any list of round rows (one match, or every match on a map, a setup, ...)."""
    deaths = [r["death"] for r in rounds if r.get("death")]
    into = [d["into_action"] for d in deaths if d.get("into_action") is not None]
    checked = [r for r in rounds if r.get("death") and r.get("nearest_mate_m") is not None]
    attack = [r for r in rounds if r["side"] == "attack" and r.get("drones") is not None]
    defense = [r for r in rounds if r["side"] == "defense" and r.get("reinforced") is not None]
    kills = [k for r in rounds for k in r.get("kills") or []]
    return {
        "rounds": len(rounds), "won": sum(1 for r in rounds if r.get("won")),
        "kills": len(kills), "deaths": len(deaths), "headshots": sum(1 for k in kills if k.get("headshot")),
        "assists": sum(int(r.get("assists") or 0) for r in rounds),
        "survived": sum(1 for r in rounds if not r.get("death")),
        "opening_kills": sum(1 for k in kills if k.get("opening")),
        "opening_deaths": sum(1 for d in deaths if d.get("opening")),
        "spawn_peeked": sum(1 for d in deaths if d.get("spawn_peek")),
        "deaths_traded": sum(1 for d in deaths if d.get("traded")),
        "deaths_checked": len(checked),
        "deaths_isolated": sum(1 for r in checked if r["nearest_mate_m"] == "other floor" or r["nearest_mate_m"] > TRADE_NEAR_M),
        "typical_death_into_action": round(statistics.median(into)) if into else None,
        "attack_rounds_measured": len(attack),
        "drones": sum(r["drones"] for r in attack),
        "drones_before_first_death": sum(r["drones_before_first_death"] for r in attack),
        "defense_rounds_measured": len(defense),
        "reinforced": sum(r["reinforced"] for r in defense),
    }


# ── Reading back ─────────────────────────────────────────────────────────────

def list_matches(owner: str) -> list[dict]:
    with server_db.get_connection() as conn:
        rows = conn.execute("""SELECT match_key, status, error, setup, played_at, map, rounds, won, report_json, uploaded_at
                               FROM solo_matches WHERE lower(owner) = ?
                               ORDER BY COALESCE(played_at, uploaded_at) DESC""", (owner.lower(),)).fetchall()
    out = []
    for r in rows:
        rep = json.loads(r["report_json"]) if r["report_json"] else None
        out.append({"folder": r["match_key"], "status": r["status"], "error": r["error"],
                    "setup": r["setup"] or inferred_setup(owner, r["played_at"]), "setup_set_by_hand": bool(r["setup"]),
                    "played_at": r["played_at"], "map": r["map"], "rounds": r["rounds"], "won": r["won"],
                    "score": rep.get("score") if rep else None, "totals": rep.get("totals") if rep else None,
                    "uploaded_at": r["uploaded_at"]})
    return out


def get_match(owner: str, match_key: str) -> Optional[dict]:
    with server_db.get_connection() as conn:
        r = conn.execute("SELECT * FROM solo_matches WHERE lower(owner) = ? AND match_key = ?",
                         (owner.lower(), match_key)).fetchone()
    if r is None:
        return None
    return {"folder": r["match_key"], "status": r["status"], "error": r["error"],
            "setup": r["setup"] or inferred_setup(owner, r["played_at"]), "setup_set_by_hand": bool(r["setup"]),
            "report": json.loads(r["report_json"]) if r["report_json"] else None}


def trends(owner: str, since: Optional[str] = None) -> dict:
    """Totals across every read match, and split by map, side, operator and setup; who kills this player most."""
    groups: dict[str, dict[str, list]] = {"map": collections.defaultdict(list), "side": collections.defaultdict(list),
                                          "operator": collections.defaultdict(list), "setup": collections.defaultdict(list)}
    every: list[dict] = []
    killers: collections.Counter = collections.Counter()
    series = []
    for m in sorted((x for x in list_matches(owner) if x["status"] == "done"), key=lambda x: x["played_at"] or ""):
        if since and (m["played_at"] or "") < since:
            continue
        rep = get_match(owner, m["folder"])["report"] or {}
        rows = rep.get("rounds") or []
        every += rows
        groups["map"][rep.get("map") or "?"] += rows
        groups["setup"][m["setup"] or "not set"] += rows
        for r in rows:
            groups["side"][r["side"]].append(r)
            groups["operator"][r.get("operator") or "?"].append(r)
        killers.update(dict(rep.get("killed_by") or []))
        t = rep.get("totals") or {}
        series.append({"folder": m["folder"], "played_at": m["played_at"], "map": rep.get("map"),
                       "kills": t.get("kills"), "deaths": t.get("deaths"), "headshots": t.get("headshots"),
                       "rounds": t.get("rounds"), "won": t.get("won"), "setup": m["setup"]})
    return {"overall": summarize(every) if every else None,
            "by": {k: {name: summarize(rows) for name, rows in v.items()} for k, v in groups.items()},
            "killed_by": killers.most_common(10), "series": series}


# ── Setups and sensitivity snapshots (the plug-in) ───────────────────────────

def list_setups(owner: str) -> list[dict]:
    with server_db.get_connection() as conn:
        rows = conn.execute("SELECT name, mouse_dpi, note, updated_at FROM solo_setups WHERE lower(owner) = ? ORDER BY name",
                            (owner.lower(),)).fetchall()
        named = {r["name"]: dict(r) for r in rows}
        for r in conn.execute("""SELECT setup, mouse_dpi, settings_json, captured_at, game FROM sens_snapshots s
                                 WHERE lower(owner) = ? AND captured_at = (SELECT MAX(captured_at) FROM sens_snapshots
                                 WHERE lower(owner) = lower(s.owner) AND setup = s.setup AND game = s.game)""",
                              (owner.lower(),)):
            e = named.setdefault(r["setup"], {"name": r["setup"], "mouse_dpi": None, "note": "", "updated_at": None})
            e.setdefault("latest", {})[r["game"]] = {"captured_at": r["captured_at"], "mouse_dpi": r["mouse_dpi"],
                                                     "settings": json.loads(r["settings_json"])}
    out = list(named.values())
    for e in out:
        e["relative_speed"] = None
    _relative_speeds(out)
    return out


def _speed(dpi: Optional[float], settings: dict) -> Optional[float]:
    """Mouse DPI x in-game hip-fire sensitivity x the game's multiplier: proportional to how far the view turns
    per inch of mouse movement, so two setups can be compared even though the absolute figure isn't cm/360."""
    try:
        sens = float(settings.get("MouseYawSensitivity") or settings.get("MouseSensitivity"))
        mult = float(settings.get("MouseSensitivityMultiplierUnit") or 1.0)
        return float(dpi) * sens * mult if dpi else None
    except (TypeError, ValueError):
        return None


def _relative_speeds(setups: list[dict]) -> None:
    speeds = {}
    for e in setups:
        snap = (e.get("latest") or {}).get("r6siege")
        if snap:
            s = _speed(snap.get("mouse_dpi") or e.get("mouse_dpi"), snap.get("settings") or {})
            if s:
                speeds[e["name"]] = s
    if not speeds:
        return
    base = min(speeds.values())
    for e in setups:
        if e["name"] in speeds:
            e["relative_speed"] = round(speeds[e["name"]] / base, 2)


def save_setup(owner: str, name: str, mouse_dpi: Optional[float], note: str = "") -> dict:
    name = (name or "").strip()
    if not SETUP_RE.match(name):
        raise UploadError("Setup names are 1-32 letters, digits, spaces, . _ -")
    dpi = _dpi(mouse_dpi)
    with server_db.get_connection() as conn:
        conn.execute("""INSERT INTO solo_setups (owner, name, mouse_dpi, note, updated_at) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(owner, name) DO UPDATE SET mouse_dpi = excluded.mouse_dpi, note = excluded.note,
                        updated_at = excluded.updated_at""", (owner, name, dpi, (note or "")[:200], _now()))
        conn.commit()
    return {"name": name, "mouse_dpi": dpi}


def delete_setup(owner: str, name: str) -> bool:
    with server_db.get_connection() as conn:
        cur = conn.execute("DELETE FROM solo_setups WHERE lower(owner) = ? AND name = ?", (owner.lower(), name))
        conn.commit()
    return cur.rowcount > 0


def _dpi(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise UploadError("Mouse DPI must be a number.")
    if not 50 <= f <= 50000:
        raise UploadError("Mouse DPI should be between 50 and 50000.")
    return f


def _iso(v: str) -> str:
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise UploadError("captured_at must be an ISO 8601 time, e.g. 2026-10-14T23:02:11Z")
    if dt.tzinfo is None:
        raise UploadError("captured_at must say its time zone (end it with Z for UTC).")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _clean_settings(raw: Any) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise UploadError("settings must be an object of name: value pairs.")
    if len(raw) > MAX_SNAPSHOT_SETTINGS:
        raise UploadError(f"At most {MAX_SNAPSHOT_SETTINGS} settings per snapshot.")
    out = {}
    for k, v in raw.items():
        k = str(k).strip()
        if not k or len(k) > 80 or not re.fullmatch(r"[A-Za-z0-9_.\-\[\] ]+", k):
            raise UploadError(f"Setting name {k[:40]!r} isn't allowed (letters, digits, _ . - only).")
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise UploadError(f"Setting {k} must be a number or short text.")
        v = str(v).strip()
        if len(v) > 80 or "\\" in v or "/" in v or ":" in v:
            raise UploadError(f"Setting {k} doesn't look like a setting value (no paths, at most 80 characters).")
        out[k] = v
    return out


def add_snapshot(owner: str, body: dict) -> dict:
    if not isinstance(body, dict):
        raise UploadError("Send a JSON object.")
    setup = str(body.get("setup") or "").strip()
    if not SETUP_RE.match(setup):
        raise UploadError("setup must be a name of 1-32 letters, digits, spaces, . _ -")
    game = str(body.get("game") or "r6siege").strip().lower()
    if not GAME_RE.match(game):
        raise UploadError("game must be a short id like r6siege.")
    captured = _iso(body.get("captured_at") or "")
    dpi = _dpi(body.get("mouse_dpi"))
    settings = _clean_settings(body.get("settings"))
    derived = body.get("derived") or {}
    if not isinstance(derived, dict) or len(derived) > 50 or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in derived.values()):
        raise UploadError("derived must be an object of up to 50 numbers.")
    derived = {str(k)[:60]: v for k, v in derived.items()}
    tool = str(body.get("tool") or "")[:60]
    digest = hashlib.sha256(json.dumps([game, dpi, settings], sort_keys=True).encode()).hexdigest()
    with server_db.get_connection() as conn:
        last = conn.execute("""SELECT snap_id, digest FROM sens_snapshots WHERE lower(owner) = ? AND setup = ? AND game = ?
                               ORDER BY captured_at DESC LIMIT 1""", (owner.lower(), setup, game)).fetchone()
        if last is not None and last["digest"] == digest:
            # Same settings as last time: only the time moves, which still says "playing on this setup now".
            conn.execute("""INSERT INTO sens_snapshots (snap_id, owner, setup, game, captured_at, mouse_dpi, settings_json,
                            derived_json, tool, digest, received_at) SELECT ?, owner, setup, game, ?, mouse_dpi, settings_json,
                            derived_json, ?, digest, ? FROM sens_snapshots WHERE snap_id = ?""",
                         (f"snap_{uuid.uuid4().hex[:12]}", captured, tool, _now(), last["snap_id"]))
            conn.commit()
            return {"stored": False, "same_as": last["snap_id"]}
        snap_id = f"snap_{uuid.uuid4().hex[:12]}"
        conn.execute("""INSERT INTO sens_snapshots (snap_id, owner, setup, game, captured_at, mouse_dpi, settings_json,
                        derived_json, tool, digest, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (snap_id, owner, setup, game, captured, dpi, json.dumps(settings), json.dumps(derived), tool, digest, _now()))
        conn.commit()
    return {"id": snap_id, "stored": True}


def list_snapshots(owner: str, since: Optional[str] = None, limit: int = 200) -> list[dict]:
    q = "SELECT * FROM sens_snapshots WHERE lower(owner) = ?"
    args: list = [owner.lower()]
    if since:
        q += " AND captured_at >= ?"
        args.append(_iso(since))
    q += " ORDER BY captured_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    with server_db.get_connection() as conn:
        rows = conn.execute(q, args).fetchall()
    return [{"id": r["snap_id"], "setup": r["setup"], "game": r["game"], "captured_at": r["captured_at"],
             "mouse_dpi": r["mouse_dpi"], "settings": json.loads(r["settings_json"]),
             "derived": json.loads(r["derived_json"]), "tool": r["tool"]} for r in rows]


def inferred_setup(owner: str, played_at: Optional[str]) -> Optional[str]:
    """The setup of the player's latest Siege snapshot before the match started (their tool sends one when the
    game starts), if it's from the last 12 hours."""
    if not played_at:
        return None
    try:
        start = datetime.fromisoformat(str(played_at).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.astimezone()          # a naive time is this server's local time
        start = start.astimezone(timezone.utc)
    except ValueError:
        return None
    with server_db.get_connection() as conn:
        r = conn.execute("""SELECT setup, captured_at FROM sens_snapshots WHERE lower(owner) = ? AND game = 'r6siege'
                            AND captured_at <= ? ORDER BY captured_at DESC LIMIT 1""",
                         (owner.lower(), start.isoformat(timespec="seconds"))).fetchone()
    if r is None:
        return None
    age = (start - datetime.fromisoformat(r["captured_at"])).total_seconds()
    return r["setup"] if age <= 12 * 3600 else None


# ── The player's own lines from the team's matches ───────────────────────────

def _team_match_utc(match_id: int, local_text: Optional[str]) -> Optional[str]:
    """A team match's start in UTC. matches.datetime is the practice PC's wall clock; that PC's UTC offset rides
    in the session's audio metadata. Without it the local digits are returned with no zone."""
    try:
        local = datetime.fromisoformat(str(local_text))
    except (TypeError, ValueError):
        return None
    if local.tzinfo is not None:
        return local.astimezone(timezone.utc).isoformat(timespec="seconds")
    try:
        with server_db.get_connection() as conn:
            row = conn.execute("SELECT audio_meta_json FROM session_comms WHERE match_id = ? LIMIT 1", (match_id,)).fetchone()
        offset = json.loads(row[0]).get("utc_offset_sec") if row else None
    except Exception:
        offset = None
    if offset is None:
        return local.isoformat(timespec="seconds")
    from datetime import timedelta
    return (local - timedelta(seconds=float(offset))).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def team_match_lines(username: str, since: Optional[str] = None) -> list[dict]:
    """This player's numbers from every team match they played in (server matches.db), and nothing about
    anyone else."""
    conn = _match_db()
    if conn is None:
        return []
    out = []
    try:
        mids = [r[0] for r in conn.execute("""SELECT DISTINCT r.match_id FROM player_round_stats s
                     JOIN rounds r ON r.round_id = s.round_id JOIN players p ON p.player_id = s.player_id
                     WHERE lower(p.name) = ?""", (username.lower(),))]
        for mid in mids:
            m = conn.execute("SELECT datetime, map FROM matches WHERE match_id = ?", (mid,)).fetchone()
            if not m:
                continue
            played = _team_match_utc(mid, m[0])
            if since and played and played < since:
                continue
            rounds = conn.execute("SELECT COUNT(*), SUM(outcome = 'win') FROM rounds WHERE match_id = ?", (mid,)).fetchone()
            k = d = hs = ok = od = 0
            for (text,) in conn.execute("""SELECT metric_text FROM derived_metrics WHERE match_id = ?
                                           AND metric_name LIKE 'round_%_events'""", (mid,)):
                try:
                    ev = json.loads(text)
                except Exception:
                    continue
                for kill in ev.get("kills") or []:
                    if (kill.get("killer") or "").lower() == username.lower():
                        k += 1
                        hs += bool(kill.get("headshot"))
                        ok += bool(kill.get("opening"))
                    if (kill.get("victim") or "").lower() == username.lower():
                        d += 1
                        od += bool(kill.get("opening"))
            out.append({"source": "team", "match_id": mid, "played_at": played, "map": m[1], "rounds": rounds[0],
                        "won": rounds[1] or 0, "kills": k, "deaths": d, "headshots": hs, "opening_kills": ok,
                        "opening_deaths": od})
    finally:
        conn.close()
    return out


def performance(owner: str, since: Optional[str] = None) -> list[dict]:
    """The plug-in's results list: the player's team matches and their own uploads, newest first."""
    since = _iso(since) if since else None
    rows = team_match_lines(owner, since)
    for r in rows:
        r["setup"] = inferred_setup(owner, r["played_at"])
    for m in list_matches(owner):
        if m["status"] != "done" or (since and (m["played_at"] or "") < since):
            continue
        t = m["totals"] or {}
        rows.append({"source": "solo", "match_id": m["folder"], "played_at": m["played_at"], "map": m["map"],
                     "rounds": t.get("rounds"), "won": t.get("won"), "kills": t.get("kills"), "deaths": t.get("deaths"),
                     "headshots": t.get("headshots"), "opening_kills": t.get("opening_kills"),
                     "opening_deaths": t.get("opening_deaths"), "deaths_untradeable": t.get("deaths_isolated"),
                     "setup": m["setup"]})
    rows.sort(key=lambda r: r.get("played_at") or "", reverse=True)
    return rows
