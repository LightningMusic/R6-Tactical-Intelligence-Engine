"""
Did we make a plan, and did we stick to it?

Three things, all from data we already store -- the comms timeline's PREP phase
and each round's events (round_<n>_events):

  1. plan called  -- in prep, did anyone actually call a plan (a site/approach,
     a role or assignment, or an objective/plant intent), as opposed to banter?
  2. stuck to it  -- did the round then COMMIT to the objective: on attack, plant
     or at least start a plant (vs. players still alive who never went for it);
     on defense, go for the retake (vs. alive and never started a defuse).
  3. plan -> plant -- do the rounds where a plan was called actually plant more
     than the rounds where nobody called one?

This is the data behind the team's own read (James + Elijah): "we plan how to get
IN, never how to close, and we don't commit." The detector is deliberately
transparent -- it returns the lines it matched so the thresholds can be tuned
from real games. Enemy names never appear here (this is about us); teammates are
shown by name via the display map, per the naming convention.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

PREP_SEC = 45.0            # prep/buy window: timeline items with t < this are "before the action"
WIPED = 5                  # a full team dead

def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ── 1. was a plan called in prep? ─────────────────────────────────────────
# Tuned against real prep comms (2026-11-23). The signal is a tactical verb tied
# to a position or a teammate -- "I'm gonna hold RAPT", "I'll cover the window",
# "I'm just gonna play upstairs", "can we reinforce site", "drop myself in
# basement", "I'm roaming" -- not mic checks or "play the game".

# verbs that are almost always tactical on their own
_STRONG = re.compile(
    r"\b(reinforc\w*|electri\w*|band ?trick\w*|head ?hole\w*|set ?up site|roam\w*|anchor\w*|"
    r"rotat\w*|prefir\w*|hard breach\w*|soft breach\w*|default plant|rush\w*|"
    r"plant(?:ing|s)?\b|defus\w*)", re.I)
# an assignment: "I'm gonna / I'll / you want to / let's / we need to ... <verb>"
_ASSIGN = re.compile(
    r"\b(i'?m gonna|i'?ll|i am gonna|i'?m just gonna|i'?m going to|i'?m gon|gonna|"
    r"you want to|you wanna|you should|you gotta|you can|i got|let'?s|we gotta|we need to|we should|can you|can we)\b"
    r".{0,34}?\b(hold\w*|play\w*|cover\w*|watch\w*|rotat\w*|roam\w*|anchor\w*|set ?up\w*|"
    r"reinforc\w*|electri\w*|drop\w*|rush\w*|push\w*|plant\w*|defus\w*|take|taking|bring\w*|"
    r"put\w*|band ?trick\w*|head ?hole\w*|watch\w*|hit)\b", re.I)
# "I'm holding / roaming / anchoring / setting up ..." (progressive, no helper verb)
_PROG = re.compile(r"\bi'?m (hold\w*|roam\w*|anchor\w*|play\w*|cover\w*|rotat\w*|setting up|reinforc\w*|push\w*|rush\w*)", re.I)
# common verb + a place word on the same line ("play upstairs", "hold piano")
_PLACE = re.compile(
    r"\b(hold\w*|play\w*|cover\w*|watch\w*|take|taking|drop\w*|push\w*|rush\w*|rotat\w*|set ?up\w*|go)\b"
    r".{0,24}?\b(site|basement|upstairs|downstairs|stairs|window|hallway|balcony|rooftop|roof|piano|main|"
    r"cctv|hatch|wall|holes?|lobby|kitchen|garage|vault|server|archives?|penthouse|bedroom|bathroom|"
    r"blue|workshop|construction|tower|bar|cafe|freezer|office|master)\b", re.I)

_OBJECTIVE = re.compile(r"\b(plant\w*|defus\w*|default plant|get it down|rush\w*|push the site)\b", re.I)
_SETUP = re.compile(r"\b(reinforc\w*|electri\w*|band ?trick\w*|head ?hole\w*|set ?up site|anchor\w*)\b", re.I)
# "play the game" / "playing this game" is banter even though it trips the loose assignment rule
_BANTER = re.compile(r"play(?:ing)?\s+(?:the|this)\s+game", re.I)


def classify_line(text: str) -> Optional[str]:
    """'objective' | 'setup' | 'assignment' for a prep line that calls a plan, else None."""
    t = (text or "").strip()
    if len(t) < 4:
        return None
    obj, setup = bool(_OBJECTIVE.search(t)), bool(_SETUP.search(t))
    # a concrete tactical signal (not just a bare helper verb)
    concrete = obj or setup or bool(_STRONG.search(t)) or bool(_PLACE.search(t)) or bool(_PROG.search(t))
    if not (concrete or _ASSIGN.search(t)):
        return None
    if _BANTER.search(t) and not concrete:          # the only hit was "...play the game"
        return None
    if obj:
        return "objective"
    if setup:
        return "setup"
    return "assignment"


def plan_in_prep(items: Iterable[dict], window: float = PREP_SEC) -> dict[str, Any]:
    """Whether a plan was called in this round's prep, from its timeline items."""
    cats: set[str] = set()
    examples: list[tuple[str, str]] = []
    for it in items or []:
        if it.get("kind") != "speech" or it.get("t", 1e9) >= window:
            continue
        cat = classify_line(it.get("text", ""))
        if cat:
            cats.add(cat)
            if len(examples) < 4:
                examples.append((it.get("speaker") or "", (it.get("text") or "").strip()))
    return {"called": bool(cats), "categories": cats, "examples": examples}


# ── 2. did we commit to the objective? ────────────────────────────────────

def _our_deaths(e: dict, ours: Optional[set[str]]) -> int:
    victims = {_norm(k.get("victim")) for k in (e.get("kills") or []) if k.get("victim")}
    if ours is not None:
        victims &= ours
    return len(victims)


def round_state(e: dict, ours: Optional[set[str]]) -> Optional[dict[str, Any]]:
    """One round's commitment, from its events. None when the side isn't known.
    attack: planted / attempted / wiped / no_commit (alive, never went).
    defense (after an enemy plant): retook / retake_tried / wiped / no_retake; else held."""
    role = e.get("our_role")
    if role not in ("attack", "defense"):
        return None
    deaths = _our_deaths(e, ours)
    if role == "attack":
        if e.get("planted_by_us") or e.get("plant_completed"):
            state = "planted"
        elif e.get("plant_attempted"):
            state = "attempted"
        elif deaths >= WIPED:
            state = "wiped"
        else:
            state = "no_commit"
        committed = state in ("planted", "attempted")
    else:
        if not e.get("bomb_planted"):
            state, committed = "held", True            # denied the plant: that IS the defensive objective
        elif e.get("defuse_completed"):
            state, committed = "retook", True
        elif e.get("defuse_attempted"):
            state, committed = "retake_tried", True
        elif deaths >= WIPED:
            state, committed = "wiped", False
        else:
            state, committed = "no_retake", False
    return {"side": role, "state": state, "committed": committed,
            "planted": state == "planted", "deaths": deaths}


# ── summary across a match ────────────────────────────────────────────────

def plan_commit_summary(events: dict[int, dict], timeline: Optional[dict],
                        ours: Optional[set[str]]) -> dict[str, Any]:
    """Join each round's prep comms with its events. Rounds without voice are still
    counted for commitment; the plan-call rate is reported over rounds that HAVE voice."""
    items_by_round: dict[int, list] = {}
    for rnd in ((timeline or {}).get("rounds") or []):
        rn = rnd.get("round_number")
        if rn is not None:
            items_by_round[int(rn)] = rnd.get("items") or []

    rounds = []
    for rn in sorted(events):
        st = round_state(events[rn], ours)
        if st is None:
            continue
        has_voice = rn in items_by_round
        plan = plan_in_prep(items_by_round.get(rn, [])) if has_voice else {"called": False, "categories": set(), "examples": []}
        rounds.append({"round": rn, "has_voice": has_voice, **st, "plan": plan})

    atk = [r for r in rounds if r["side"] == "attack"]
    voiced_atk = [r for r in atk if r["has_voice"]]
    called = [r for r in voiced_atk if r["plan"]["called"]]
    uncalled = [r for r in voiced_atk if not r["plan"]["called"]]
    return {
        "rounds": rounds,
        "attack_rounds": len(atk),
        "voiced_attack_rounds": len(voiced_atk),
        "plan_called": len(called),
        "committed": sum(1 for r in atk if r["committed"]),
        "no_commit": [r["round"] for r in atk if r["state"] == "no_commit"],
        "planted": sum(1 for r in atk if r["planted"]),
        # the cross-tab (item 3): plant rate when a plan was called vs not
        "called_planted": sum(1 for r in called if r["planted"]),
        "called_total": len(called),
        "uncalled_planted": sum(1 for r in uncalled if r["planted"]),
        "uncalled_total": len(uncalled),
        # defense side, reported lightly
        "defense_rounds": sum(1 for r in rounds if r["side"] == "defense"),
        "no_retake": [r["round"] for r in rounds if r["state"] == "no_retake"],
    }


def plan_commit_lines(summary: dict[str, Any]) -> list[str]:
    s = summary
    atk = s["attack_rounds"]
    if not atk:
        return []
    out: list[str] = []
    if s["voiced_attack_rounds"]:
        out.append(f"- A plan was called in prep in {s['plan_called']} of {s['voiced_attack_rounds']} attack "
                   f"round(s) that had voice (a site/approach, a role, or a plant call).")
    out.append(f"- We committed to the objective (planted or started a plant) in {s['committed']} of {atk} "
               f"attack round(s).")
    if s["no_commit"]:
        rs = ", ".join(f"R{n:02d}" for n in s["no_commit"])
        out.append(f"- Had players alive but never went for the plant in {len(s['no_commit'])} round(s): {rs}. "
                   "This is the no-commit pattern -- a default plant plan (who plants, who covers) would fix it.")
    # item 3: did calling a plan actually convert to plants?
    if s["called_total"]:
        cp, ct = s["called_planted"], s["called_total"]
        line = f"- Plan -> plant: we planted {cp}/{ct} of the rounds where a plan was called"
        if s["uncalled_total"]:
            line += f", vs {s['uncalled_planted']}/{s['uncalled_total']} where none was"
        out.append(line + ".")
    if s["no_retake"]:
        rs = ", ".join(f"R{n:02d}" for n in s["no_retake"])
        out.append(f"- On defense, enemy planted and we never started a defuse with players alive in "
                   f"{len(s['no_retake'])} round(s): {rs}.")
    return out
