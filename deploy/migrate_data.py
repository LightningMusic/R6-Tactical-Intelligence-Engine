"""Prepares a copy of the server's databases for the Docker deployment.

    python deploy/migrate_data.py <old server_data dir> <empty staging dir>

The databases hold absolute Windows paths (voice chunks); inside the container
the data lives at /data, so those are rewritten. Each database is copied with
SQLite's online-backup API, so it is consistent even if the old server is still
running. r6ctl migrate then streams the staged databases plus the uploads,
models, voice recordings and server_config.json (tokens) into the volume.
"""
import re
import sqlite3
import sys
from pathlib import Path

DATABASES = ("server_matches.db", "matches.db")
# C:\...\server_data\voice\x\y  ->  /data/voice/x/y
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/].*?server_data[\\/]", re.IGNORECASE)


def rewrite(value: str) -> str:
    return WINDOWS_PATH.sub("/data/", value, count=1).replace("\\", "/")


def stage_database(src: Path, dst: Path) -> int:
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)
    source.close()

    changed = 0
    tables = [r[0] for r in target.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    for table in tables:
        for col in [r[1] for r in target.execute(f'pragma table_info("{table}")')]:
            rows = target.execute(
                f'select rowid, "{col}" from "{table}" '
                f'where typeof("{col}")=\'text\' and ("{col}" like \'_:\\%\' or "{col}" like \'_:/%\')'
            ).fetchall()
            for rowid, value in rows:
                if not WINDOWS_PATH.match(value):
                    print(f"  warning: {table}.{col} row {rowid} has a path outside server_data: {value}")
                    continue
                target.execute(f'update "{table}" set "{col}"=? where rowid=?', (rewrite(value), rowid))
                changed += 1
    target.commit()
    target.execute("vacuum")
    target.close()
    return changed


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src_dir, stage = Path(sys.argv[1]), Path(sys.argv[2])
    stage.mkdir(parents=True, exist_ok=True)
    for name in DATABASES:
        src = src_dir / name
        if not src.exists():
            print(f"{name}: not present, skipped")
            continue
        dst = stage / name
        if dst.exists():
            dst.unlink()
        n = stage_database(src, dst)
        print(f"{name}: staged, {n} path(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
