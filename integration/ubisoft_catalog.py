"""
Keeps operators, gadgets and abilities current from Ubisoft's official
operator pages -- the part of the catalog replays can't provide, because
replays contain no loadout data at all.

Each operator's page carries a structured loadout whose items are tagged
primary / secondary / gadget / unique-ability, plus the operator's name and
side. This sync:
  * adds operators Ubisoft lists that the database doesn't have yet,
  * fills in missing ability names,
  * corrects the display name of operators first learned from a replay
    (a replay only gives "NOOR"; Ubisoft gives "Noor"),
  * adds new gadgets, and replaces an operator's gadget options when a
    rework changes them.
Charge counts aren't published, so new gadget links take the count that
gadget has everywhere else in the data (Barbed Wire is always 2, etc.).

Fail-safe by design. Scraping a marketing page is fragile -- Ubisoft
renamed a key in this exact payload (ContentfulGraphQL -> ContentfulGraphQl)
at some point, which broke r6-dissect's copy of this scraper. So: nothing
is ever deleted except an operator's own stale gadget links, and only when
that operator's page parsed cleanly with at least one gadget; a failed or
implausibly small operator list aborts the sync without touching anything.
"""
from __future__ import annotations

import json
import re
import time
import threading
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from database.game_catalog import PLACEHOLDER_OPERATOR_PREFIX, fold

LIST_URL = "https://www.ubisoft.com/en-us/game/rainbow-six/siege/game-info/operators"
DETAIL_URL = LIST_URL + "/{slug}"
USER_AGENT = "R6Analyzer catalog sync (+github.com/LightningMusic)"
REQUEST_GAP_SECONDS = 0.5
MIN_PLAUSIBLE_OPERATORS = 40

# "Latest" must never become "wrong". A real season adds a gadget now and
# then and reworks a handful of loadouts; a page whose structure shifted
# under the parser looks nothing like that (e.g. weapons suddenly tagged as
# gadgets would mint dozens of "gadgets" and rewire every operator). A sync
# whose changes exceed these limits is rolled back in full, logged, and
# retried next time -- stale data beats corrupted data.
MAX_NEW_GADGETS_PER_SYNC = 3
MAX_LOADOUT_CHANGES_PER_SYNC = 20
MAX_GADGETS_PER_OPERATOR = 8  # Striker/Sentry, who pick from all of them, have 7
SYNC_INTERVAL = timedelta(hours=24)
FULL_REFRESH_INTERVAL = timedelta(days=7)

_STATE_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});\s*</script>", re.S)


# =====================================================================
# Fetch + parse
# =====================================================================

def _fetch_state(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    html = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    m = _STATE_RE.search(html)
    if not m:
        raise ValueError("page no longer embeds window.__PRELOADED_STATE__")
    return json.loads(m.group(1))


def _key(d: dict, wanted: str):
    """Case-insensitive dict lookup -- the exact failure mode that already
    broke this scraper once was a key changing case."""
    if not isinstance(d, dict):
        return None
    if wanted in d:
        return d[wanted]
    low = wanted.lower()
    for k, v in d.items():
        if k.lower() == low:
            return v
    return None


def fetch_operator_list() -> list[dict]:
    st = _fetch_state(LIST_URL)
    content = _key(_key(_key(st, "ContentfulGraphQl"), "OperatorsListContainer"), "content") or []
    ops = []
    for o in content:
        slug, name = o.get("slug"), o.get("operatorName")
        if slug and name:
            # Ubisoft's list calls "is attacker" `side`; detail pages also
            # carry an explicit isAttacker, which wins when present.
            ops.append({"slug": slug, "name": name.strip(), "is_attacker": bool(o.get("side"))})
    return ops


def fetch_operator_detail(slug: str) -> Optional[dict]:
    st = _fetch_state(DETAIL_URL.format(slug=slug))
    cg = _key(st, "ContentfulGraphQl") or {}
    container = _key(cg, f"OperatorDetailsContainer-{slug}")
    content = _key(container, "content") if container else None
    if not isinstance(content, dict):
        return None
    header = _key(content, "header") or {}
    loadout = _key(content, "loadout") or []
    gadgets, ability = [], None
    for item in loadout:
        kind = str(item.get("weaponType") or "").lower()
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if kind == "gadget":
            gadgets.append(title)
        elif kind == "unique-ability":
            ability = title
    is_attacker = _key(header, "isAttacker")
    return {
        "name": str(_key(header, "operatorName") or "").strip() or None,
        "is_attacker": None if is_attacker is None else bool(is_attacker),
        "gadgets": gadgets,
        "ability": ability,
    }


def tidy_title(text: str) -> str:
    """Ubisoft writes some names in ALL CAPS ("BREACHING HAMMER",
    "CAPITÃO"). Title-case those, leaving acronyms and model numbers alone
    ("CCE SHIELD MK2" -> "CCE Shield MK2"). Mixed-case text is untouched."""
    text = (text or "").strip()
    if not text or text != text.upper():
        return text
    words = []
    for w in text.split(" "):
        keep = len(w) <= 3 or any(c.isdigit() or c in ".-'\"" for c in w)
        words.append(w if keep else w[:1] + w[1:].lower())
    return " ".join(words)


# =====================================================================
# Apply
# =====================================================================

@dataclass
class SyncReport:
    ok: bool = False
    skipped_reason: Optional[str] = None
    pages_fetched: int = 0
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_gadgets: int = 0
    loadouts_changed: set = field(default_factory=set)
    implausible_loadouts: int = 0


def _meta_get(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO metadata (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _older_than(stamp: Optional[str], age: timedelta) -> bool:
    if not stamp:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(stamp) > age
    except ValueError:
        return True


def _typical_counts(conn) -> dict[int, int]:
    counts: dict[int, Counter] = {}
    for gid, n in conn.execute("SELECT gadget_id, max_count FROM operator_gadget_options"):
        counts.setdefault(gid, Counter())[n] += 1
    return {gid: c.most_common(1)[0][0] for gid, c in counts.items()}


def _gadget_id(conn, title: str, report: SyncReport) -> int:
    key = fold(title)
    for gid, name in conn.execute("SELECT gadget_id, name FROM gadgets"):
        if fold(name) == key:
            return gid
    gid = conn.execute(
        "INSERT INTO gadgets (name, category) VALUES (?, 'uncategorized')", (tidy_title(title),)
    ).lastrowid
    report.changes.append(f"NEW gadget: {tidy_title(title)}")
    report.new_gadgets += 1
    return gid


def _apply_operator(conn, listed: dict, detail: Optional[dict], report: SyncReport) -> None:
    name = tidy_title((detail or {}).get("name") or listed["name"])
    is_attacker = (detail or {}).get("is_attacker")
    if is_attacker is None:
        is_attacker = listed["is_attacker"]
    side = "attack" if is_attacker else "defense"
    ability = tidy_title((detail or {}).get("ability") or "")

    row = None
    for op_id, op_name, op_ability, op_source in conn.execute(
        "SELECT operator_id, name, ability_name, source FROM operators"
    ):
        if not op_name.startswith(PLACEHOLDER_OPERATOR_PREFIX) and fold(op_name) == fold(name):
            row = (op_id, op_name, op_ability, op_source)
            break

    if row is None:
        op_id = conn.execute(
            """INSERT INTO operators (name, side, ability_name, ability_max_count, source)
               VALUES (?, ?, ?, 0, 'ubisoft')""",
            (name, side, ability),
        ).lastrowid
        report.changes.append(f"NEW operator: {name} ({side}{', ' + ability if ability else ''})")
    else:
        op_id, op_name, op_ability, op_source = row
        current_side = conn.execute("SELECT side FROM operators WHERE operator_id = ?", (op_id,)).fetchone()[0]
        if current_side != side:
            # Never flipped automatically: a side learned from real replays
            # is evidence, and a wrong side corrupts every team-role guess.
            report.warnings.append(f"{op_name}: Ubisoft says {side}, database says {current_side} -- left as is")
        if op_source == "replay" and op_name != name:
            conn.execute("UPDATE operators SET name = ? WHERE operator_id = ?", (name, op_id))
            report.changes.append(f"Renamed {op_name!r} -> {name!r} (official spelling)")
        # Ability names follow the official ones: reworks rename them
        # (Dokkaebi's Logic Bomb became the Jegeo Payload). Only a real
        # difference counts, not casing or punctuation.
        if ability and fold(ability) != fold(op_ability or ""):
            conn.execute("UPDATE operators SET ability_name = ? WHERE operator_id = ?", (ability, op_id))
            was = f" (was {op_ability!r})" if (op_ability or "").strip() else ""
            report.changes.append(f"{name}: ability = {ability}{was}")

    gadgets = (detail or {}).get("gadgets") or []
    if not gadgets:
        return  # never strip an operator's gadgets on an empty/failed parse
    if len(gadgets) > MAX_GADGETS_PER_OPERATOR:
        report.warnings.append(f"{name}: page lists {len(gadgets)} gadgets -- implausible, loadout left unchanged")
        report.implausible_loadouts += 1
        return

    typical = _typical_counts(conn)
    wanted = {_gadget_id(conn, g, report) for g in gadgets}
    current = {gid for (gid,) in conn.execute(
        "SELECT gadget_id FROM operator_gadget_options WHERE operator_id = ?", (op_id,))}
    names = dict(conn.execute("SELECT gadget_id, name FROM gadgets"))

    if wanted != current:
        report.loadouts_changed.add(name)
    for gid in sorted(wanted - current):
        conn.execute(
            "INSERT INTO operator_gadget_options (operator_id, gadget_id, max_count) VALUES (?, ?, ?)",
            (op_id, gid, typical.get(gid, 1)),
        )
        report.changes.append(f"{name}: + {names.get(gid)}")
    for gid in sorted(current - wanted):
        conn.execute(
            "DELETE FROM operator_gadget_options WHERE operator_id = ? AND gadget_id = ?", (op_id, gid)
        )
        report.changes.append(f"{name}: - {names.get(gid)} (no longer in official loadout)")


def sync_from_ubisoft(
    db,
    log: Optional[Callable[[str], None]] = None,
    force: bool = False,
    force_full: bool = False,
) -> SyncReport:
    """
    One sync pass. Cheap when nothing is due: a no-op unless the last sync
    is older than SYNC_INTERVAL (or force). Operator pages are only fetched
    for operators that are new or missing gadgets/ability, except once per
    FULL_REFRESH_INTERVAL when every page is re-read to catch reworks.
    """
    log = log or (lambda m: print(f"[UbisoftSync] {m}"))
    report = SyncReport()

    with db.get_connection() as conn:
        last = _meta_get(conn, "ubisoft_last_sync")
        last_full = _meta_get(conn, "ubisoft_last_full_sync")
    if not force and not _older_than(last, SYNC_INTERVAL):
        report.ok, report.skipped_reason = True, "synced recently"
        return report
    full = force_full or _older_than(last_full, FULL_REFRESH_INTERVAL)

    try:
        listed = fetch_operator_list()
        report.pages_fetched += 1
    except Exception as e:
        report.skipped_reason = f"couldn't read Ubisoft's operator list ({e}) -- nothing changed"
        log(report.skipped_reason)
        return report
    if len(listed) < MIN_PLAUSIBLE_OPERATORS:
        report.skipped_reason = (f"Ubisoft's operator list parsed to only {len(listed)} operators -- "
                                 f"page layout probably changed; nothing changed")
        log(report.skipped_reason)
        return report

    with db.get_connection() as conn:
        known = {}
        for op_id, op_name, ability in conn.execute(
            "SELECT operator_id, name, ability_name FROM operators"
        ):
            known[fold(op_name)] = (op_id, ability)
        with_links = {oid for (oid,) in conn.execute(
            "SELECT DISTINCT operator_id FROM operator_gadget_options")}

    def needs_detail(op: dict) -> bool:
        if full:
            return True
        k = known.get(fold(op["name"]))
        return k is None or not (k[1] or "").strip() or k[0] not in with_links

    todo = [op for op in listed if needs_detail(op)]
    log(f"{len(listed)} operators listed; reading {len(todo)} operator page(s)"
        + (" (weekly full refresh)" if full else ""))

    details: dict[str, Optional[dict]] = {}
    for op in todo:
        try:
            time.sleep(REQUEST_GAP_SECONDS)
            details[op["slug"]] = fetch_operator_detail(op["slug"])
            report.pages_fetched += 1
        except Exception as e:
            details[op["slug"]] = None
            report.errors.append(f"{op['name']}: {e}")

    with db.get_connection() as conn:
        for op in listed:
            detail = details.get(op["slug"])
            if op["slug"] in details or fold(op["name"]) not in known:
                try:
                    _apply_operator(conn, op, detail, report)
                except Exception as e:
                    report.errors.append(f"{op['name']}: {e}")

        # Everything above is still uncommitted: decide whether it looks
        # like a game patch or a broken page before any of it is kept.
        suspicious = []
        if report.new_gadgets > MAX_NEW_GADGETS_PER_SYNC:
            suspicious.append(f"{report.new_gadgets} new gadgets at once (limit {MAX_NEW_GADGETS_PER_SYNC})")
        if report.implausible_loadouts > MAX_NEW_GADGETS_PER_SYNC:
            # One odd page is skipped on its own; many mean the whole
            # payload is off, ability names and all.
            suspicious.append(f"{report.implausible_loadouts} operator pages with implausible loadouts")
        if len(report.loadouts_changed) > MAX_LOADOUT_CHANGES_PER_SYNC:
            suspicious.append(f"{len(report.loadouts_changed)} operators' loadouts changing at once "
                              f"(limit {MAX_LOADOUT_CHANGES_PER_SYNC})")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if suspicious:
            conn.rollback()
            # Back off a day like a normal sync rather than refetching every
            # page hourly just to refuse it again; last_full_sync is left
            # alone so the next attempt is a full re-read.
            _meta_set(conn, "ubisoft_last_sync", now)
            report.skipped_reason = ("held back, nothing changed: " + "; ".join(suspicious)
                                     + " -- that looks like the page changed, not the game")
            log(report.skipped_reason)
            for c in report.changes[:10]:
                log(f"  would have: {c}")
            report.changes = []
        else:
            _meta_set(conn, "ubisoft_last_sync", now)
            if full and not report.errors:
                _meta_set(conn, "ubisoft_last_full_sync", now)
            report.ok = True
        # Kept either way, so what the sync did -- or refused to do -- can
        # be looked up afterwards instead of only scrolling past in a log.
        _meta_set(conn, "ubisoft_last_sync_report", json.dumps({
            "at": now,
            "applied": report.ok,
            "reason": report.skipped_reason,
            "changes": report.changes,
            "warnings": report.warnings,
            "errors": report.errors,
        }))
        conn.commit()

    for c in report.changes:
        log(c)
    for w in report.warnings:
        log(f"warning: {w}")
    log(f"{'done' if report.ok else 'not applied'}: {len(report.changes)} change(s), "
        f"{len(report.warnings)} warning(s), {len(report.errors)} page error(s)")
    for e in report.errors[:5]:
        log(f"  error: {e}")
    return report


def start_background_sync(db, log=None, repeat: bool = False) -> threading.Thread:
    """Runs sync_from_ubisoft off the calling thread. repeat=True keeps
    checking hourly for as long as the process lives (the server); each
    check is a no-op until SYNC_INTERVAL has passed."""
    def _run():
        while True:
            try:
                sync_from_ubisoft(db, log=log)
            except Exception as e:
                (log or print)(f"[UbisoftSync] sync failed (non-fatal): {e}")
            if not repeat:
                return
            time.sleep(3600)

    t = threading.Thread(target=_run, daemon=True, name="UbisoftCatalogSync")
    t.start()
    return t
