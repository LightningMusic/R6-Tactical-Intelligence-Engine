"""
Fills in assists for matches that were imported before r6-dissect could read
them (every stored assist was 0 until 2026-09-28).

Re-reads the original replays with the current r6-dissect and updates
player_round_stats.assists for the matching rounds. A replay is matched to a
stored match by its first round's start time (what the importer records as
the match date), then by round number and username (or a linked alias).
Only rows whose assists are still 0 are touched, and the database is copied
to <db>.bak-assists first.

  python scripts/backfill_assists.py --db path/to/matches.db SOURCE [SOURCE ...]

SOURCE: .r6session packages, match folders holding .rec files, or folders
containing either (e.g. the game's MatchReplay folder or server uploads).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISSECT = REPO / "integration" / "bin" / "r6-dissect.exe"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def replay_sets(sources: list[Path], tmp: Path):
    """Yields (label, [rec files]) per match."""
    for src in sources:
        if src.is_file() and src.suffix == ".r6session":
            out = tmp / src.stem[:16]
            out.mkdir(exist_ok=True)
            with zipfile.ZipFile(src) as zf:
                for n in zf.namelist():
                    if n.endswith(".rec"):
                        (out / Path(n).name).write_bytes(zf.read(n))
            yield src.name, sorted(out.glob("*.rec"))
        elif src.is_dir():
            recs = sorted(src.glob("*.rec"))
            if recs:
                yield src.name, recs
            else:
                children = sorted(p for p in src.iterdir() if p.suffix == ".r6session" or p.is_dir())
                yield from replay_sets(children, tmp)


def dissect(rec: Path) -> dict | None:
    p = subprocess.run([str(DISSECT), str(rec), "--format", "json"],
                       capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("sources", nargs="+", type=Path)
    args = ap.parse_args()

    if not args.dry_run:
        backup = args.db.with_name(args.db.name + ".bak-assists")
        shutil.copy2(args.db, backup)
        print(f"backup: {backup}")

    conn = sqlite3.connect(args.db)
    matched = unmatched = rows_updated = assists_added = 0
    with tempfile.TemporaryDirectory() as tmp:
        for label, recs in replay_sets(args.sources, Path(tmp)):
            rounds = [d for d in (dissect(r) for r in recs) if d]
            stamps = [d["timestamp"] for d in rounds if d.get("timestamp")]
            if not stamps:
                continue
            start = datetime.fromisoformat(min(stamps).rstrip("Z")).isoformat()
            row = conn.execute("SELECT match_id, map FROM matches WHERE datetime = ?", (start,)).fetchone()
            if not row:
                unmatched += 1
                continue
            match_id, map_name = row
            matched += 1
            added_here = 0
            for d in rounds:
                rn = d.get("roundNumber")
                if not isinstance(rn, int):
                    continue
                r = conn.execute("SELECT round_id FROM rounds WHERE match_id = ? AND round_number = ?",
                                 (match_id, max(1, rn + 1))).fetchone()
                if not r:
                    continue
                for s in d.get("stats") or []:
                    a = int(s.get("assists") or 0)
                    if a <= 0:
                        continue
                    p = conn.execute(
                        """SELECT player_id FROM players WHERE LOWER(name) = LOWER(?)
                           UNION SELECT player_id FROM player_aliases WHERE alias = ? COLLATE NOCASE
                           LIMIT 1""",
                        (s["username"], s["username"]),
                    ).fetchone()
                    if not p:
                        continue
                    n = conn.execute(
                        "UPDATE player_round_stats SET assists = ? WHERE round_id = ? AND player_id = ? AND assists = 0",
                        (a, r[0], p[0]),
                    ).rowcount
                    rows_updated += n
                    added_here += a if n else 0
            assists_added += added_here
            print(f"  match {match_id:>3} ({map_name}, {start}): +{added_here} assists")
    if args.dry_run:
        conn.rollback()
        print("dry run -- nothing written")
    else:
        conn.commit()
    print(f"matches found: {matched}, replays with no stored match: {unmatched}, "
          f"stat rows updated: {rows_updated}, assists added: {assists_added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
