"""
What each operator is FOR, in plain words, so the debrief model can read "Elijah played Lesion and Frost"
as "Elijah anchored the site with traps" instead of guessing. One role per operator, the one most teams
use it for; good enough to describe a player's night, not a tier list.
"""
from __future__ import annotations

from typing import Iterable

ATTACK = {
    "hard breach": {"thermite", "hibana", "ace", "maverick"},
    "breach support / anti-gadget": {"thatcher", "kali", "twitch", "iq", "jackal", "lion", "dokkaebi", "gridlock",
                                     "flores", "brava", "mute", "zero", "deimos", "solid snake", "rauora"},
    "entry / fragger": {"ash", "zofia", "iana", "buck", "sledge", "nomad", "amaru", "striker", "ram", "osa"},
    "soft breach / vertical": {"sledge", "buck", "ram", "fuze", "ash"},
    "shield / frontline": {"montagne", "blitz", "blackbeard", "clash"},
    "flank / pressure": {"finka", "glaz", "capitao", "ying", "nokk", "nøkk", "sens", "grim"},
}
DEFENSE = {
    "anti-breach": {"bandit", "kaid", "mute", "maestro"},
    "anchor / trap": {"lesion", "frost", "kapkan", "ela", "melusi", "thorn", "goyo", "tubarao", "aruni", "azami",
                      "fenrir", "skopos", "sentry", "rook", "doc", "smoke", "castle", "warden", "denari"},
    "anti-drone / intel": {"mozzie", "pulse", "valkyrie", "maestro", "echo", "vigil", "solis", "jager", "jäger",
                           "wamai", "thunderbird", "oryx", "mira"},
    "roam": {"caveira", "vigil", "oryx", "alibi", "ela", "jager", "jäger", "nokk", "nøkk"},
}
# One answer per operator (earlier groups win), e.g. Sledge is "entry / fragger", not "soft breach".
ROLE: dict[tuple[str, str], str] = {}
for side, table in (("attack", ATTACK), ("defense", DEFENSE)):
    for role, ops in table.items():
        for op in ops:
            ROLE.setdefault((side, op), role)


def role_of(operator: str, side: str) -> str:
    return ROLE.get((side, str(operator or "").strip().lower()), "")


def roles_text(played: Iterable[tuple[str, str]]) -> str:
    """'anchor / trap x6 (def), breach support x3 (att)' for a player's (operator, side) list."""
    counts: dict[tuple[str, str], int] = {}
    for op, side in played:
        role = role_of(op, side)
        if role:
            counts[(role, side)] = counts.get((role, side), 0) + 1
    return ", ".join(f"{role} x{n} ({'att' if side == 'attack' else 'def'})"
                     for (role, side), n in sorted(counts.items(), key=lambda kv: -kv[1]))


# What the numbers in the debrief mean, for the model. Kept short: llama3.1:8b follows short rules better.
READING_GUIDE = """HOW TO READ THESE NUMBERS (Siege basics, true for this team's own history too):
- The first kill of a round matters, but it does not decide it: this team usually wins about 3 in 4 rounds where it gets the first kill and about 2 in 5 where it doesn't. Winning a round after losing the first kill is a real achievement worth naming.
- A "traded" death is one avenged within seconds by a teammate; it keeps the fight even. An untraded death leaves the team a player down. This team trades fewer than 1 in 10 deaths, so a night with more trades is a real change.
- On attack, getting the plant down wins most rounds; on defense, rounds where the enemy plants are usually lost, so denying the plant matters more than retaking.
- Roles: anchors and trappers (Lesion, Frost, Kapkan...) are SUPPOSED to survive and kill late, so a low kill count is not a weakness for them; entry players (Ash, Zofia, Kali, Buck, Sledge...) are SUPPOSED to take early fights, so first deaths are part of the job and what matters is whether they were traded.
- POSITIONING facts come from where every player actually was (read from the replay): a death with no teammate within 10 m could not be traded, and the last two players far apart fight two separate 1-vs-many fights.
- Only call a difference big, small, better or worse when the facts above already say so (WELL ABOVE, BELOW and so on). Do not call 86% "significantly above" 82%. Small samples (a handful of rounds) are not patterns."""
