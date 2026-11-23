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


# ---------------------------------------------------------------------------
# Recognizer priming -- the preventive side of this module
# ---------------------------------------------------------------------------
# correct() above repairs mis-spellings AFTER transcription, and only the ones
# that can't be an ordinary English word. The bigger win is biasing Whisper to
# hear our vocabulary correctly in the FIRST place -- "Twitch"/"Buck"/"Fuze"
# instead of "with"/"back"/"fuse", the common-word cases correct() deliberately
# refuses to touch. This text is fed to the recognizer as preceding context:
# openai-whisper takes it as initial_prompt (biases the first window of each
# chunk); faster-whisper takes it as hotwords (biases every window, so it holds
# across a whole match). Keep it well under Whisper's ~224-token prompt window.
# Operators first (the module's own note: names are where ASR misses); tune the
# list from real games as the meta/our picks change.
_PRIMING_OPERATORS = (
    "Fuze, Thatcher, Thermite, Hibana, Ace, Flores, Twitch, Lion, Nøkk, Zofia, "
    "Buck, Kali, Dokkaebi, Maverick, Sledge, Montagne, "
    "Jäger, Bandit, Mute, Smoke, Doc, Rook, Frost, Lesion, Valkyrie, Mira, Kaid, "
    "Fenrir, Thorn, Aruni, Wamai, Mozzie, Warden, Maestro, Castle, Caveira, Solis, Oryx"
)

INITIAL_PROMPT = (
    "Rainbow Six Siege team comms. Operators: " + _PRIMING_OPERATORS + ". "
    "Callouts: plant the defuser, defuse, drone it, hard breach, soft breach, "
    "roam, roamer, anchor, rotate, rappel, vault, prefire, spawn peek, reinforce, "
    "hatch, Fuze charge, site, CCTV."
)


def initial_prompt() -> str:
    """Priming text biasing the recognizer toward our operators and callouts.

    Pass as openai-whisper's ``initial_prompt`` or faster-whisper's ``hotwords``.
    """
    return INITIAL_PROMPT
