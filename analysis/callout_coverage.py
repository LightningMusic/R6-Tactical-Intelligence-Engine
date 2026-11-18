"""Roam / outside-callout coverage: was the enemy who got the opening pick
called out beforehand?

The question James keeps asking -- "did anyone call the flanker before he
punished us?" -- answered from data we already have: the comms timeline (speech
+ kill feed on one clock) plus each round's operator picks.

v1 scope, deliberately tractable:
- The threat = the enemy who lands the round's FIRST kill against us (the
  opening pick). That's unambiguous in the replay, so no fragile "is this
  position outside the site" geometry yet.
- "Called" = in the LOOKBACK seconds before that pick, a teammate named the
  killer's operator (e.g. "Lion on the roof" before the Lion opening-picks).
  Position-only callouts ("he's on the roof" with no operator) are recorded as
  a weaker signal (an alert fired) but don't count as a named call yet.

Pure function; the caller supplies `operator_of(round_number, username)` from
the replay. Tested in tests/test_callout_coverage.py.
"""
from __future__ import annotations

import collections
from typing import Any, Callable, Iterable, Optional

LOOKBACK_SEC = 20.0


def _opening_pick(items: list[dict]) -> Optional[dict]:
    """The round's first kill where an enemy killed one of ours."""
    enemy_on_us = [it for it in items
                   if it.get("kind") == "Kill" and it.get("victim_ours") and not it.get("killer_ours")]
    if not enemy_on_us:
        return None
    return min(enemy_on_us, key=lambda k: k.get("t", 0.0))


def analyze(rounds: list[dict], operator_of: Callable[[int, str], str],
            lookback_sec: float = LOOKBACK_SEC) -> dict[str, Any]:
    """Per-round opening-pick callout coverage + a summary.

    `rounds`: the comms timeline's rounds (each with round_number + items, where
    items are speech {speaker,text,t,alert} and Kill {killer,victim,t,clock,
    killer_ours,victim_ours}).
    `operator_of(round_number, killer_username) -> operator name` ("" if unknown).
    """
    findings: list[dict] = []
    for rnd in rounds:
        rn = rnd.get("round_number")
        items = rnd.get("items") or []
        pick = _opening_pick(items)
        if pick is None:
            continue
        pt = float(pick.get("t", 0.0))
        killer = pick.get("killer", "") or ""
        op = (operator_of(rn, killer) or "").strip()
        opl = op.lower()

        named_by = None
        named_text = ""
        secs_before = None
        alert_before = False
        for sp in items:
            if sp.get("kind") != "speech":
                continue
            st = float(sp.get("t", 0.0))
            if not (pt - lookback_sec <= st <= pt):
                continue
            if sp.get("alert"):
                alert_before = True
            text = (sp.get("text") or "")
            if opl and opl in text.lower() and named_by is None:
                named_by = sp.get("speaker")
                named_text = text.strip()
                secs_before = round(pt - st, 1)

        findings.append({
            "round": rn,
            "clock": pick.get("clock"),
            "killer": killer,
            "operator": op,
            "victim": pick.get("victim"),
            "called": named_by is not None,
            "caller": named_by,
            "secs_before": secs_before,
            "snippet": named_text[:100],
            "alert_before": alert_before,            # any flagged callout in the window, even if not by operator
        })

    total = len(findings)
    called = sum(1 for f in findings if f["called"])
    with_op = sum(1 for f in findings if f["operator"])
    uncalled = [f for f in findings if not f["called"] and f["operator"]]
    # Who keeps getting the opening pick on us (named by their most common op):
    by_killer = collections.Counter(f["killer"] for f in findings if f["killer"])
    op_of_killer = {f["killer"]: f["operator"] for f in findings if f["killer"] and f["operator"]}
    repeat = [{"killer": k, "operator": op_of_killer.get(k, ""), "times": n}
              for k, n in by_killer.most_common() if n >= 2]
    return {
        "version": 1,
        "lookback_sec": lookback_sec,
        "total_opening_picks": total,
        "with_known_operator": with_op,
        "called": called,
        "coverage": (round(called / with_op, 3) if with_op else None),
        "uncalled_rounds": [f["round"] for f in uncalled],
        "repeat_entry_fraggers": repeat,     # enemies who opened >= 2 rounds on us
        "findings": findings,
        "summary": (f"{called}/{with_op} enemy opening picks were called out by operator beforehand"
                    if with_op else "no enemy opening picks with a known operator"),
    }


def operator_map_from_rows(rows: Iterable[dict]) -> dict[tuple[int, str], str]:
    """Build an operator_of lookup from replay rows [{round, username, operator}].
    Returns a dict keyed by (round_number, lowered-username); wrap with
    `lambda rn, u: m.get((rn, u.lower()), "")`."""
    out: dict[tuple[int, str], str] = {}
    for r in rows:
        rn, user, op = r.get("round"), (r.get("username") or ""), (r.get("operator") or "")
        if rn is not None and user:
            out[(rn, user.lower())] = op
    return out
