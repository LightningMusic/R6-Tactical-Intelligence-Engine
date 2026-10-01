"""
R6 Voice -- records your own mic during practice so the team's comms
analysis knows exactly who said what.

For teammates: run it, put in your in-game name once, press Start when
practice starts and Stop when it ends. It uploads as it goes; if the
internet drops, it catches up by itself later.
"""
from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "R6Voice"
SETTINGS = APP_DIR / "settings.json"
PENDING = APP_DIR / "pending"

sys.path.insert(0, str(Path(__file__).parent))
import credentials                                    # noqa: E402
from recorder import Recorder, input_devices           # noqa: E402
from uploader import Uploader                          # noqa: E402

BG, CARD, TEXT, MUTED, ACCENT, RED, GREEN = "#14161a", "#1e2127", "#e8eaed", "#9aa0a6", "#4c8dff", "#ef5350", "#43a047"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = load_settings()
        self.recorder: Recorder | None = None
        self.devices = input_devices()

        root.title("R6 Voice")
        root.configure(bg=BG)
        root.resizable(False, False)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 15))
        style.configure("Big.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 20))
        style.configure("Level.Horizontal.TProgressbar", troughcolor="#2a2e36", background=GREEN,
                        bordercolor=CARD, lightcolor=GREEN, darkcolor=GREEN)
        style.configure("TButton", font=("Segoe UI", 10))

        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="R6 Voice", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Records only your mic, for the team's comms review.",
                  foreground=MUTED).pack(anchor="w", pady=(0, 10))

        setup = ttk.Frame(outer, style="Card.TFrame", padding=12)
        setup.pack(fill="x")
        ttk.Label(setup, text="In-game name", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=self.settings.get("username", ""))
        self.name_entry = ttk.Entry(setup, textvariable=self.name_var, width=30)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=3)
        ttk.Label(setup, text="Microphone", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        names = ["Windows default"] + [n for _, n in self.devices]
        self.mic_var = tk.StringVar(value=self.settings.get("device", "Windows default"))
        if self.mic_var.get() not in names:
            self.mic_var.set("Windows default")
        self.mic_box = ttk.Combobox(setup, textvariable=self.mic_var, values=names, state="readonly", width=38)
        self.mic_box.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=3)
        self.server_rows: list = []
        if not credentials.SERVER_URL:
            self.url_var = tk.StringVar(value=self.settings.get("server_url", ""))
            self.key_var = tk.StringVar(value=self.settings.get("voice_token", ""))
            for r, (label, var) in enumerate((("Server address", self.url_var), ("Voice key", self.key_var)), start=2):
                ttk.Label(setup, text=label, style="Card.TLabel").grid(row=r, column=0, sticky="w")
                e = ttk.Entry(setup, textvariable=var, width=30, show="*" if "key" in label else "")
                e.grid(row=r, column=1, sticky="ew", padx=(10, 0), pady=3)
                self.server_rows.append(e)
        setup.columnconfigure(1, weight=1)

        rec = ttk.Frame(outer, style="Card.TFrame", padding=12)
        rec.pack(fill="x", pady=(10, 0))
        self.time_label = ttk.Label(rec, text="Not recording", style="Big.TLabel")
        self.time_label.pack(anchor="w")
        self.level = ttk.Progressbar(rec, style="Level.Horizontal.TProgressbar", maximum=1.0, length=360)
        self.level.pack(fill="x", pady=(6, 8))
        buttons = ttk.Frame(rec, style="Card.TFrame")
        buttons.pack(fill="x")
        self.start_btn = tk.Button(buttons, text="Start recording", command=self.toggle, bg=ACCENT, fg="white",
                                   activebackground=ACCENT, relief="flat", font=("Segoe UI Semibold", 11),
                                   padx=14, pady=6)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(buttons, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=8)

        status = ttk.Frame(outer, style="Card.TFrame", padding=12)
        status.pack(fill="x", pady=(10, 0))
        self.server_label = ttk.Label(status, text="Server: checking...", style="Card.TLabel")
        self.server_label.pack(anchor="w")
        self.upload_label = ttk.Label(status, text="", style="Muted.TLabel")
        self.upload_label.pack(anchor="w")
        ttk.Label(status, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Only what you say while unmuted in Discord ends up in the review -- "
                       "anything else your mic hears is thrown away on the server. "
                       "Pause any time you want nothing recorded.").pack(anchor="w", pady=(6, 0))

        self.uploader = Uploader(PENDING, self.config, on_change=lambda: root.after(0, self.refresh))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(300, lambda: self._bg(self.uploader.ping))
        self.tick()

    # ── config ──────────────────────────────────────────────────────

    def config(self) -> dict:
        url = credentials.SERVER_URL or self.settings.get("server_url", "")
        key = credentials.VOICE_TOKEN or self.settings.get("voice_token", "")
        return {"server_url": url.rstrip("/"), "voice_token": key}

    def _bg(self, fn) -> None:
        import threading
        threading.Thread(target=fn, daemon=True).start()

    def _save_form(self) -> bool:
        name = self.name_var.get().strip()
        if len(name) < 2:
            messagebox.showwarning("R6 Voice", "Put in your in-game (Ubisoft) name first -- "
                                               "that's how your lines are matched to you in the kill feed.")
            return False
        self.settings["username"] = name
        self.settings["device"] = self.mic_var.get()
        if not credentials.SERVER_URL:
            self.settings["server_url"] = self.url_var.get().strip()
            self.settings["voice_token"] = self.key_var.get().strip()
        save_settings(self.settings)
        return True

    # ── recording ───────────────────────────────────────────────────

    def toggle(self) -> None:
        if self.recorder is None:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        if not self._save_form():
            return
        device = None
        if self.mic_var.get() != "Windows default":
            device = next((i for i, n in self.devices if n == self.mic_var.get()), None)
        username = self.settings["username"]
        rec = Recorder(out_dir=PENDING, device=device,
                       on_chunk=lambda c: self.uploader.enqueue(c, username))
        try:
            rec.start()
        except Exception as e:
            messagebox.showerror("R6 Voice", f"Couldn't open the microphone:\n{e}")
            return
        self.recorder = rec
        for w in (self.name_entry, self.mic_box, *self.server_rows):
            w.configure(state="disabled")
        self.start_btn.configure(text="Stop recording", bg=RED, activebackground=RED)
        self.pause_btn.configure(state="normal", text="Pause")
        self._bg(self.uploader.ping)

    def stop(self) -> None:
        rec, self.recorder = self.recorder, None
        if rec is not None:
            rec.stop()
        self.name_entry.configure(state="normal")
        self.mic_box.configure(state="readonly")
        for w in self.server_rows:
            w.configure(state="normal")
        self.start_btn.configure(text="Start recording", bg=ACCENT, activebackground=ACCENT)
        self.pause_btn.configure(state="disabled", text="Pause")
        self.level["value"] = 0
        self.refresh()

    def toggle_pause(self) -> None:
        if self.recorder is not None:
            self.recorder.paused = not self.recorder.paused
            self.pause_btn.configure(text="Resume" if self.recorder.paused else "Pause")

    # ── status ──────────────────────────────────────────────────────

    def tick(self) -> None:
        if self.recorder is not None:
            secs = int(time.time() - self.recorder.started_at)
            state = "Paused" if self.recorder.paused else "Recording"
            self.time_label.configure(text=f"{state}  {secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}")
            self.level["value"] = 0 if self.recorder.paused else self.recorder.level
        else:
            self.time_label.configure(text="Not recording")
        self.root.after(100, self.tick)

    def refresh(self) -> None:
        up = self.uploader
        if up.connected:
            text, color = "Server: connected", GREEN
            if up.clock_offset is not None and abs(up.clock_offset) > 60:
                text += f"  (your PC clock is {abs(up.clock_offset):.0f}s off -- turn on 'Set time automatically')"
                color = "#ffb300"
        elif up.connected is False:
            text, color = f"Server: {up.last_error or 'not reachable'} -- recording still works, uploads will catch up", RED
        else:
            text, color = "Server: checking...", MUTED
        self.server_label.configure(text=text, foreground=color)
        waiting = up.waiting()
        self.upload_label.configure(
            text=f"Uploaded {up.sent} chunk(s) this session" + (f", {waiting} waiting" if waiting else "")
            + (f"  -- {up.last_error}" if up.last_error and up.connected else ""))

    def on_close(self) -> None:
        if self.recorder is not None:
            if not messagebox.askyesno("R6 Voice", "Stop recording and close?\n"
                                                   "Anything not uploaded yet goes up next time you open the app."):
                return
            self.stop()
        waiting = self.uploader.waiting()
        if waiting and self.uploader.connected:
            # Give the last chunk a moment to go up before closing.
            deadline = time.time() + 20
            while self.uploader.waiting() and time.time() < deadline:
                self.root.update()
                time.sleep(0.2)
        self.uploader.stop()
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
