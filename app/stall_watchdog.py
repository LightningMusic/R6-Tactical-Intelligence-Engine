"""
Makes a frozen window explain itself.

A QTimer on the GUI thread stamps a heartbeat every second. A background thread
watches the stamp; if the GUI thread stops answering for STALL_SEC it writes
every thread's stack to the log (once per stall), so the log shows exactly which
call the window is stuck in instead of just going quiet.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

STALL_SEC = 8.0


def format_thread_stacks() -> str:
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for ident, frame in sys._current_frames().items():
        out.append(f"--- thread {names.get(ident, ident)} ---\n" + "".join(traceback.format_stack(frame)))
    return "\n".join(out)


def start_stall_watchdog(parent=None, stall_sec: float = STALL_SEC):
    from PySide6.QtCore import QTimer

    beat = {"t": time.monotonic()}
    timer = QTimer(parent)
    timer.setInterval(1000)
    timer.timeout.connect(lambda: beat.__setitem__("t", time.monotonic()))
    timer.start()

    def watch() -> None:
        dumped = False
        while True:
            time.sleep(2.0)
            stalled = time.monotonic() - beat["t"]
            if stalled > stall_sec and not dumped:
                dumped = True
                print(f"[Watchdog] The window has not responded for {stalled:.0f}s. Where every thread is:\n"
                      + format_thread_stacks())
            elif stalled <= stall_sec and dumped:
                dumped = False
                print("[Watchdog] The window is responding again.")

    threading.Thread(target=watch, daemon=True, name="StallWatchdog").start()
    return timer
