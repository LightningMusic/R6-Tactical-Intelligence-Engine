"""
Operator names that speech recognition spells wrongly.

Checked against last night's 1,229 transcribed lines: Whisper gets most operator
names right when they are said (Lion, Warden, Thorn, Thermite, Maverick, ...), and
its misses fall into two groups. Spellings that can only be an operator name
("Yeager" for Jäger, "Thorne" for Thorn) are fixed here. Misses that are ordinary
English words ("with" for Twitch, "back" for Buck, "good" for Goyo, "look" for Rook)
are NOT touched: fuzzy matching would corrupt far more transcript than it repairs,
so those stay as heard.

Extend ALIASES from real transcripts when a new recurring mis-spelling shows up.
"""
from __future__ import annotations

import re

ALIASES: dict[str, str] = {
    # Jäger
    "yeager": "Jäger", "yaeger": "Jäger", "jaeger": "Jäger", "jager": "Jäger", "yager": "Jäger",
    # Dokkaebi
    "dokkabi": "Dokkaebi", "dokabi": "Dokkaebi", "dokebi": "Dokkaebi", "dokaebi": "Dokkaebi",
    "dokkaby": "Dokkaebi", "dockabi": "Dokkaebi",
    # Tachanka
    "tchanka": "Tachanka", "tachanca": "Tachanka", "tachank": "Tachanka", "tachonka": "Tachanka",
    # Capitão, Montagne, Caveira, Tubarão, Skopós, Nøkk
    "capitan": "Capitão", "capitao": "Capitão", "capitaine": "Capitão",
    "montaigne": "Montagne",
    "cavera": "Caveira", "caveria": "Caveira",
    "tubarao": "Tubarão", "tuberao": "Tubarão",
    "skopos": "Skopós", "scopos": "Skopós",
    "nokk": "Nøkk", "nøk": "Nøkk",
    # Thorn, Denari, Wamai, Mozzie, Aruni, Iana, Hibana, Fenrir, Kapkan, Zofia
    "thorne": "Thorn",
    "denaria": "Denari",
    "wamay": "Wamai", "wamae": "Wamai", "wamai's": "Wamai's",
    "mozzy": "Mozzie", "mozie": "Mozzie",
    "arooni": "Aruni", "arunie": "Aruni",
    "ianna": "Iana",
    "hebana": "Hibana",
    "fenrear": "Fenrir",
    "kapcan": "Kapkan", "kapkin": "Kapkan",
    "zophia": "Zofia",
}

_PATTERN = re.compile(r"\b(" + "|".join(sorted((re.escape(a) for a in ALIASES), key=len, reverse=True)) + r")\b",
                      re.IGNORECASE)


def correct(text: str) -> str:
    """The text with unambiguous operator mis-spellings replaced by the operator's name."""
    if not text:
        return text
    return _PATTERN.sub(lambda m: ALIASES[m.group(1).lower()], text)
