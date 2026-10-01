"""
R6 Companion -- runs from a USB stick on a teammate's PC during practice.

It records that player's mic with the portable OBS on the stick, starting
and stopping when the team's main R6Analyzer does (through the team
server), keeps OBS recording, and uploads the audio so the comms review
knows exactly who said what. Nothing is installed on the PC.
"""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "voice_recorder"))

import credentials                     # noqa: E402
from core import Companion             # noqa: E402

BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else HERE
BG, CARD, TEXT, MUTED, ACCENT, RED, GREEN, AMBER = (
    "#14161a", "#1e2127", "#e8eaed", "#9aa0a6", "#4c8dff", "#ef5350", "#43a047", "#ffb300")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.lines: list[str] = []
        self.comp = Companion(BASE, self.server_config, log=self.log)

        root.title("R6 Companion")
        root.configure(bg=BG)
        root.resizable(False, False)
        st = ttk.Style(root)
        st.theme_use("clam")
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        st.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 15))
        st.configure("Big.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 14))

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="R6 Companion", style="Title.TLabel").pack(anchor="w", pady=(0, 8))

        top = ttk.Frame(outer, style="Card.TFrame", padding=12)
        top.pack(fill="x")
        self.big = ttk.Label(top, text="Starting...", style="Big.TLabel")
        self.big.pack(anchor="w")
        self.why = ttk.Label(top, text="", style="Muted.TLabel")
        self.why.pack(anchor="w", pady=(2, 8))
        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill="x")
        for text, cmd in (("Start now", lambda: self.comp.set_manual(True)),
                          ("Stop now", lambda: self.comp.set_manual(False)),
                          ("Follow team", lambda: self.comp.set_manual(None))):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=(0, 6))

        setup = ttk.Frame(outer, style="Card.TFrame", padding=12)
        setup.pack(fill="x", pady=(10, 0))
        ttk.Label(setup, text="In-game name").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar(value=self.comp.settings.get("username", ""))
        ttk.Entry(setup, textvariable=self.name, width=26).grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(setup, text="Save", command=self.save_name).grid(row=0, column=2)
        ttk.Label(setup, text="Microphone").grid(row=1, column=0, sticky="w")
        self.mic = tk.StringVar()
        self.mic_box = ttk.Combobox(setup, textvariable=self.mic, state="readonly", width=34)
        self.mic_box.grid(row=1, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(setup, text="Use", command=self.save_mic).grid(row=1, column=2)
        self.mics: list[tuple[str, str]] = []
        if not credentials.SERVER_URL:
            self.url = tk.StringVar(value=self.comp.settings.get("server_url", ""))
            self.key = tk.StringVar(value=self.comp.settings.get("voice_token", ""))
            for r, (label, var) in enumerate((("Server address", self.url), ("Voice key", self.key)), start=2):
                ttk.Label(setup, text=label).grid(row=r, column=0, sticky="w")
                ttk.Entry(setup, textvariable=var, width=26, show="*" if "key" in label else "").grid(
                    row=r, column=1, sticky="ew", padx=8, pady=3)
            ttk.Button(setup, text="Save", command=self.save_server).grid(row=3, column=2)
        setup.columnconfigure(1, weight=1)

        info = ttk.Frame(outer, style="Card.TFrame", padding=12)
        info.pack(fill="x", pady=(10, 0))
        self.info = ttk.Label(info, text="", style="Muted.TLabel", justify="left")
        self.info.pack(anchor="w")
        self.logbox = ttk.Label(info, text="", style="Muted.TLabel", justify="left", wraplength=420)
        self.logbox.pack(anchor="w", pady=(6, 0))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.protocol("WM_SAVE_YOURSELF", self.on_close)     # Windows logging off / shutting down
        threading.Thread(target=self.comp.run_forever, daemon=True).start()
        self.refresh()

    # ── config ──────────────────────────────────────────────────────

    def server_config(self) -> dict:
        s = self.comp.settings if hasattr(self, "comp") else {}
        url = credentials.SERVER_URL or s.get("server_url", "")
        key = credentials.VOICE_TOKEN or s.get("voice_token", "")
        return {"server_url": url.rstrip("/"), "voice_token": key}

    def save_name(self) -> None:
        name = self.name.get().strip()
        if len(name) < 2:
            messagebox.showwarning("R6 Companion", "Type your in-game (Ubisoft) name exactly as it shows in the kill feed.")
            return
        self.comp.save_settings(username=name)
        self.log(f"Name saved: {name}")

    def save_mic(self) -> None:
        pick = next((d for d, n in self.mics if n == self.mic.get()), None)
        if pick is not None:
            self.comp.save_settings(mic_device=pick)
            self.log(f"Microphone: {self.mic.get()} (used from the next recording)")

    def save_server(self) -> None:
        self.comp.save_settings(server_url=self.url.get().strip(), voice_token=self.key.get().strip())
        self.log("Server settings saved.")

    def log(self, line: str) -> None:
        self.lines = (self.lines + [time.strftime("%H:%M  ") + line])[-6:]

    # ── status ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        c = self.comp
        if not c.settings.get("username"):
            big, why, color = "Put in your in-game name", "Then press Save -- that's the only setup.", AMBER
        elif c.recording:
            secs = int(time.time() - c.recording_since) if c.recording_since else 0
            big = f"● Recording  {secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
            why = "Started on this PC (Start now)." if c.manual else "Following the team -- stops when the main app stops."
            color = RED
        elif c.wanted():
            big, why, color = "Starting OBS...", c.obs_status, AMBER
        else:
            big = "Stopped on this PC" if c.manual is False else "Waiting for the team to start"
            why = ("Recording starts by itself when the main app starts a session."
                   if c.manual is None else "Press Follow team to go back to starting with the team.")
            color = TEXT
        self.big.configure(text=big, foreground=color)
        self.why.configure(text=why)
        server = {True: "connected", False: "not connected -- recording still works; uploads catch up later",
                  None: "checking..."}[c.server_ok]
        up = c.uploader
        self.info.configure(text=(
            f"Server: {server}\nOBS: {c.obs_status}"
            f"\nUploads: {up.sent} sent, {up.waiting()} waiting" + (f"   (preparing {c.exporting})" if c.exporting else "")))
        self.logbox.configure(text="\n".join(self.lines))
        if not self.mics and c.obs.ws is not None:
            self.mics = c.obs.microphones()
            if self.mics:
                self.mic_box.configure(values=[n for _, n in self.mics])
                cur = c.settings.get("mic_device", "default")
                self.mic.set(next((n for d, n in self.mics if d == cur), self.mics[0][1]))
        self.root.after(500, self.refresh)

    def on_close(self) -> None:
        # No "are you sure?": this also runs when Windows shuts down, and a
        # dialog there would hold the shutdown up. Closing = done for today;
        # anything not uploaded yet goes up next time the app runs.
        self.comp.shutdown()
        self.comp.uploader.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
