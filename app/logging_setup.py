"""
Centralized logging + stdout/stderr capture setup.

Why this exists: the client is built with console=False (see
R6Analyzer.spec) -- a genuinely windowed app with no console attached at
all, not just a hidden one. In that mode sys.stdout/sys.stderr are None,
and every print() call across the codebase (there are many -- OBS control,
the AI backend, .rec import, transcription, Discord capture all report
their own errors this way) would either crash on write or, after the
None-guards already in place in analysis/intel_engine.py and
integration/whisper_transcriber.py, silently disappear into a throwaway
io.StringIO() that nothing ever reads. The broad except-Exception handling
throughout those modules already does the right thing by not letting a
failure crash the whole app -- but a contained failure and a silent one
currently look identical from the outside. If something goes wrong on an
unfamiliar computer, there is no way to look at it afterward and see why.

configure_logging() fixes that WITHOUT touching any of those print() call
sites. It redirects sys.stdout/sys.stderr at the process level to a small
Tee object that always writes every line to a rotating log file, and --
when a real stream exists underneath (running from source, or the server's
own console=True window) -- also forwards to it, so nothing currently
visible becomes invisible. Call this exactly once, as early as possible in
each entry point (main.py, server_main.py), before anything else has a
chance to print.
"""
import logging
import logging.handlers
import subprocess
import sys
import threading
from pathlib import Path

_configured = False


class _TeeWriter:
    """File-like object standing in for sys.stdout/sys.stderr.

    Buffers partial writes until a newline, then logs the completed line
    through the given logger (which carries it into the rotating file via
    the root handler configure_logging() sets up) and, if an original
    stream was captured, forwards the raw text to it unchanged so a real
    console (dev runs, the server's visible window) keeps working exactly
    as before.

    Every method swallows its own errors. This object's entire purpose is
    diagnosing failures elsewhere in the app -- it must never itself become
    a new way for the app to crash.
    """

    def __init__(self, logger: logging.Logger, level: int, original=None):
        self._logger = logger
        self._level = level
        self._original = original
        self._buffer = ""

    def write(self, text: str) -> int:
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                pass
        try:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self._logger.log(self._level, line)
        except Exception:
            pass
        return len(text)

    def flush(self) -> None:
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        if self._original is not None:
            try:
                return bool(self._original.isatty())
            except Exception:
                pass
        return False

    def reconfigure(self, *args, **kwargs) -> None:
        # Some callers (server_main.py's line-buffering fix) probe for this.
        # Forward it if there's a real stream underneath; otherwise it's a
        # no-op, same as it would be against the None stdout this replaces.
        if self._original is not None:
            try:
                self._original.reconfigure(*args, **kwargs)
            except Exception:
                pass


def configure_logging(logs_dir: Path, filename: str = "app.log") -> None:
    """Set up a rotating file log and route sys.stdout/sys.stderr into it.

    Safe to call multiple times -- only the first call does anything.
    Never raises: if the log file itself can't be created (read-only USB,
    out of space, permissions), this quietly does nothing rather than
    taking down the app over its own diagnostics.
    """
    global _configured
    if _configured:
        return
    _configured = True

    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / filename,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except Exception:
        return

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    sys.stdout = _TeeWriter(logging.getLogger("stdout"), logging.INFO, original_stdout)
    sys.stderr = _TeeWriter(logging.getLogger("stderr"), logging.ERROR, original_stderr)

    logging.getLogger(__name__).info(
        "Logging started -> %s", str(logs_dir / filename)
    )


def pipe_process_to_log(process: "subprocess.Popen", tag: str) -> None:
    """
    2026-09-11: the other half of "have a log you can actually look over" --
    configure_logging() above captures every print() this app's own code
    makes, but a long-running background process this app *launches and
    then leaves running unattended* is a different case. Right now that's
    exactly one thing: the portable Ollama server analysis/intel_engine.py
    starts (`ollama.exe serve`), which used to run with
    stdout=DEVNULL/stderr=DEVNULL -- meaning if it failed to bind its port,
    crashed mid-generation, or hit any other real problem after startup,
    there was no way to ever see why, from the log or anywhere else.

    Starts a daemon thread that reads that process's own output line by
    line and feeds it into the SAME rotating log file configure_logging()
    already set up -- tagged with `tag` (e.g. "ollama") so it's easy to
    tell apart from this app's own log lines when reading the file.

    Requires the process to have been started with
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True -- merging
    stderr into stdout so nothing is missed, since most CLI tools (Ollama
    included) log to stderr by default.

    This is for a background process left running (a server), NOT a
    one-shot subprocess.run() call whose result the caller already
    inspects and reports on itself (ffmpeg, r6-dissect) -- those already
    say what happened; this is for the case where nothing else ever would.

    Never raises. If configure_logging() was never called (or failed),
    this just logs into the void the same way any other stray logging
    call would -- it doesn't need configure_logging() to have succeeded
    to be safe to call.
    """
    if process.stdout is None:
        return

    logger = logging.getLogger(tag)

    def _pump() -> None:
        try:
            for line in process.stdout:
                line = line.rstrip("\n")
                if line:
                    logger.info(line)
        except Exception:
            pass

    threading.Thread(target=_pump, daemon=True, name=f"log-pump-{tag}").start()
