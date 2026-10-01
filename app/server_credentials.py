"""
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
by hand: run `python scripts\\embed_client_credentials.py --clear`.
"""

EMBEDDED_SERVER_URL = ''
EMBEDDED_API_KEY = ''
