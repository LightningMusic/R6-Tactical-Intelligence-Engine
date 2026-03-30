import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import shutil


class ExportView(ttk.Frame):
    """
    Export Center

    Handles all data extraction:
    - CSV (match stats)
    - TXT (transcripts)
    - HTML (reports)
    - Recording files
    """

    def __init__(self, parent, controller):
        super().__init__(parent)

        self.controller = controller
        self.match_var = tk.StringVar()

        self._build_layout()
        self.load_matches()

    # --------------------------------------------------
    # UI Layout
    # --------------------------------------------------

    def _build_layout(self):

        title = ttk.Label(self, text="Export Data", font=("Arial", 18))
        title.pack(pady=10)

        frame = ttk.Frame(self)
        frame.pack(pady=10)

        ttk.Label(frame, text="Select Match:").pack(side="left")

        self.dropdown = ttk.Combobox(
            frame,
            textvariable=self.match_var,
            state="readonly",
            width=40
        )
        self.dropdown.pack(side="left", padx=10)

        # Buttons
        ttk.Button(self, text="Export CSV", command=self.export_csv).pack(pady=5)
        ttk.Button(self, text="Export Transcript (TXT)", command=self.export_txt).pack(pady=5)
        ttk.Button(self, text="Export Report (HTML)", command=self.export_html).pack(pady=5)
        ttk.Button(self, text="Export Recording", command=self.export_recording).pack(pady=5)

    # --------------------------------------------------
    # Load Matches
    # --------------------------------------------------

    def load_matches(self):

        try:
            matches = self.controller.get_all_matches()

            values = [
                f"{m.match_id} - {m.map} vs {m.opponent_name}"
                for m in matches
            ]

            self.dropdown["values"] = values

        except Exception as e:
            messagebox.showerror("Error", str(e))

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def _get_match_id(self):

        if not self.match_var.get():
            messagebox.showwarning("Warning", "Select a match first.")
            return None

        return int(self.match_var.get().split(" - ")[0])

    def _choose_path(self, default_name):

        return filedialog.asksaveasfilename(
            initialfile=default_name,
            defaultextension="",
            filetypes=[("All Files", "*.*")]
        )

    # --------------------------------------------------
    # EXPORT: CSV
    # --------------------------------------------------

    def export_csv(self):

        match_id = self._get_match_id()
        if match_id is None:
            return

        try:
            path = self._choose_path(f"match_{match_id}.csv")
            if not path:
                return

            self.controller.export_match_csv(match_id, path)

            messagebox.showinfo("Success", f"CSV exported to:\n{path}")

        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    # --------------------------------------------------
    # EXPORT: TXT (Transcript)
    # --------------------------------------------------

    def export_txt(self):

        match_id = self._get_match_id()
        if match_id is None:
            return

        try:
            path = self._choose_path(f"match_{match_id}_transcript.txt")
            if not path:
                return

            text = self.controller.get_transcript_text(match_id)

            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

            messagebox.showinfo("Success", f"Transcript exported to:\n{path}")

        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    # --------------------------------------------------
    # EXPORT: HTML Report
    # --------------------------------------------------

    def export_html(self):

        match_id = self._get_match_id()
        if match_id is None:
            return

        try:
            path = self._choose_path(f"match_{match_id}_report.html")
            if not path:
                return

            report_path = self.controller.generate_match_report(match_id)

            shutil.copy(report_path, path)

            messagebox.showinfo("Success", f"Report exported to:\n{path}")

        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    # --------------------------------------------------
    # EXPORT: Recording
    # --------------------------------------------------

    def export_recording(self):

        match_id = self._get_match_id()
        if match_id is None:
            return

        try:
            path = self._choose_path(f"match_{match_id}_recording.mp4")
            if not path:
                return

            recording_path = self.controller.get_recording_path(match_id)

            if not os.path.exists(recording_path):
                raise FileNotFoundError("Recording file not found.")

            shutil.copy(recording_path, path)

            messagebox.showinfo("Success", f"Recording exported to:\n{path}")

        except Exception as e:
            messagebox.showerror("Export Error", str(e))