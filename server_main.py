"""
Standalone entry point for the packaged R6Server.exe (see R6Server.spec).

This is a separate script from server/main.py's own __main__ block on
purpose:
  - PyInstaller wants one simple top-level script to point Analysis() at.
  - A frozen exe must never use uvicorn's reload=True — it spawns a
    subprocess file-watcher that expects source files on disk, which don't
    exist once everything is bundled into a single .exe.
  - `uvicorn.run(app, ...)` (passing the app object directly) is used
    instead of the "server.main:app" import-string form, which sidesteps
    any ambiguity in how PyInstaller's frozen module loader resolves import
    strings at runtime.

Everything that actually matters — token provisioning, directory setup, the
startup banner, the background worker — lives in server/config.py and
server/main.py and is identical whether this runs from source
(`python server_main.py`) or from the packaged exe.
"""
import sys

# Frozen console apps (and any process whose stdout isn't a live TTY, e.g.
# redirected to a log file) default to fully block-buffered stdout instead
# of line-buffered. Without this, every print() below — including the
# first-run token banner, which is the entire point of self-provisioning a
# token instead of requiring an env var — can sit invisibly in a buffer for
# as long as the server keeps running, instead of showing up immediately.
# Must happen before importing server.config, since that import is what
# triggers the token-provisioning prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass  # Python < 3.7 or a stream that doesn't support reconfigure.

import uvicorn

from server.config import server_settings
from server.main import app

# Route stdout/stderr into a rotating log file under server_data/logs/ too
# (server.config.ServerSettings already defines LOGS_DIR, but nothing was
# ever writing to it). Runs after the line-buffering fix above and after
# server_settings exists on purpose -- it captures the now-line-buffered
# original console stream and keeps forwarding to it, so the visible
# console window is unaffected; only its output also gets a persistent
# copy from here on. (The handful of token-provisioning prints that fire
# during the server.config import above, before this line runs, only hit
# the console, not the file -- acceptable since this server already has a
# real, visible console window, unlike the client.) Useful for a machine
# that's meant to be "the home PC that stays on," usually unattended when
# something actually goes wrong.
from app.logging_setup import configure_logging
configure_logging(server_settings.LOGS_DIR, "r6server.log")


def main() -> None:
    try:
        uvicorn.run(app, host=server_settings.HOST, port=server_settings.PORT, log_level="info")
    except OSError as e:
        # Most common cause: something else is already bound to the port.
        print(f"\n[R6Server] Failed to start: {e}")
        print(f"[R6Server] Is another instance already running on port {server_settings.PORT}?")
        print("[R6Server] Set R6_SERVER_PORT to use a different port.")
        input("\nPress Enter to exit...")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[R6Server] Shutting down.")


if __name__ == "__main__":
    main()
