"""
Milestone 5: embeds the server's self-provisioned API token and permanent
public address (a Tailscale Funnel URL, set once via
setup_tailscale_funnel.bat) directly into the R6Analyzer client build, so
nobody has to type a server URL or API key into Settings at all — the
client just works, from any network, the moment it's built against a
configured server. See app/server_credentials.py for how app/config.py
consumes what this script writes.

Called by build_and_deploy.bat in two modes, around the R6Analyzer.exe
PyInstaller step only (the server build needs none of this):

  --write   Right before building R6Analyzer.exe: overwrite
            app/server_credentials.py with the real values.
  --clear   Right after — success OR failure — restore the placeholder, so
            the real values never sit in the working tree longer than the
            build itself takes, and can never end up committed by accident.

Deliberately reuses server/config.py's own ServerSettings rather than
re-implementing token/URL lookup here: importing it both reads an existing
server_data/server_config.json and self-provisions one if it's missing,
exactly like starting R6Server.exe for the first time would. That keeps
there being exactly one place this logic lives.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = REPO_ROOT / "app" / "server_credentials.py"

_HEADER = '''"""
AUTO-GENERATED AT BUILD TIME -- DO NOT EDIT, DO NOT COMMIT REAL VALUES.

build_and_deploy.bat overwrites this file with the server's real API token
and permanent public address (read from server_data/server_config.json via
scripts/embed_client_credentials.py) immediately before building
R6Analyzer.exe, then restores it to this exact placeholder content
immediately afterward -- success or failure -- so the working tree never
sits with a real secret in it longer than the build itself takes.

app/config.py's SERVER_URL/API_KEY properties fall back to these values
ONLY when the user hasn't manually set their own server_url/api_key in
Settings -> Remote Sync, so a manually-configured LAN/dev server always
takes priority over whatever was embedded at build time.

If this file ever contains real values outside of an in-progress build
(e.g. a build was interrupted before cleanup ran), it's safe to restore it
by hand: run `python scripts\\\\embed_client_credentials.py --clear`.
"""'''


def _render(server_url: str, api_key: str) -> str:
    return f"{_HEADER}\n\nEMBEDDED_SERVER_URL = {server_url!r}\nEMBEDDED_API_KEY = {api_key!r}\n"


PLACEHOLDER = _render("", "")


def _display_path(path: Path) -> str:
    """Path relative to the repo root for a friendlier log line, falling
    back to the absolute path if it isn't actually under REPO_ROOT (e.g. a
    test pointing CREDENTIALS_FILE at a tmp_path)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def write_real_values() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from server.config import server_settings

    token = server_settings.API_TOKEN_PLAINTEXT
    url = server_settings.PUBLIC_URL

    if not token:
        print(
            "[embed_client_credentials] ERROR: No API token available "
            f"(token source: {server_settings.TOKEN_SOURCE}). If "
            "R6_SERVER_API_TOKEN_HASH is set, its plaintext can't be "
            "recovered for embedding -- unset it or set R6_SERVER_API_TOKEN "
            "instead, then rebuild. Otherwise, run the server once first "
            "(python server_main.py, or R6Server.exe) so it can "
            "self-provision a token in server_data/server_config.json.",
            flush=True,
        )
        return 1

    if not url:
        print(
            "[embed_client_credentials] WARNING: No public_url configured "
            "yet -- run setup_tailscale_funnel.bat on the machine that will "
            "run R6Server.exe first. Continuing to embed the API key with "
            "no server address; the client will still need a server URL "
            "typed into Settings -> Remote Sync manually until you set one.",
            flush=True,
        )

    CREDENTIALS_FILE.write_text(_render(url, token), encoding="utf-8")
    masked = f"***{token[-4:]}" if len(token) >= 4 else "***"
    print(
        f"[embed_client_credentials] Embedded server credentials into "
        f"{_display_path(CREDENTIALS_FILE)} "
        f"(public_url={'set' if url else 'NOT SET'}, api_key={masked}).",
        flush=True,
    )
    return 0


def clear() -> int:
    CREDENTIALS_FILE.write_text(PLACEHOLDER, encoding="utf-8")
    print(
        f"[embed_client_credentials] Cleared {_display_path(CREDENTIALS_FILE)} "
        f"back to its placeholder.",
        flush=True,
    )
    return 0


# ── R6Voice (teammates' recorder) ─────────────────────────────────────────
# Gets the server address and the narrow voice key only -- never the main
# API token, which can read every match on the server.

VOICE_CREDENTIALS_FILE = REPO_ROOT / "voice_recorder" / "credentials.py"
# R6Companion carries the same narrow voice key (it uploads the same chunks
# and checks in to the relay, which accepts that key and nothing more).
COMPANION_CREDENTIALS_FILE = REPO_ROOT / "companion" / "credentials.py"
_VOICE_HEADER = (
    "# Filled in at build time by scripts/embed_client_credentials.py (--voice)\n"
    "# from the server's server_config.json, then reset to these placeholders so\n"
    "# the key never sits in the source tree. A build with them empty asks for\n"
    "# the server address and key on first run instead.\n"
)


def _render_voice(url: str, token: str) -> str:
    return f"{_VOICE_HEADER}SERVER_URL = {url!r}\nVOICE_TOKEN = {token!r}\n"


def write_voice_values() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from server.config import server_settings

    token = server_settings.VOICE_TOKEN_PLAINTEXT or ""
    url = server_settings.PUBLIC_URL or ""
    if not token or not url:
        print("[embed_client_credentials] WARNING: voice key or public address missing -- "
              "R6Voice will ask for them on first run.", flush=True)
    for f in (VOICE_CREDENTIALS_FILE, COMPANION_CREDENTIALS_FILE):
        f.write_text(_render_voice(url, token), encoding="utf-8")
        print(f"[embed_client_credentials] Embedded voice credentials into {_display_path(f)} "
              f"(public_url={'set' if url else 'NOT SET'}, voice_key={'***' + token[-4:] if token else 'NOT SET'}).",
              flush=True)
    return 0


def clear_voice() -> int:
    for f in (VOICE_CREDENTIALS_FILE, COMPANION_CREDENTIALS_FILE):
        f.write_text(_render_voice("", ""), encoding="utf-8")
        print(f"[embed_client_credentials] Cleared {_display_path(f)} back to its placeholder.", flush=True)
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--write":
        sys.exit(write_real_values())
    elif mode == "--clear":
        sys.exit(clear())
    elif mode == "--write-voice":
        sys.exit(write_voice_values())
    elif mode == "--clear-voice":
        sys.exit(clear_voice())
    else:
        print("Usage: embed_client_credentials.py --write|--clear|--write-voice|--clear-voice", flush=True)
        sys.exit(2)
