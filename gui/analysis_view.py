import tkinter as tk
from tkinter import ttk, messagebox


class AnalysisView(ttk.Frame):
    """
    Analysis Dashboard

    Allows the user to:
    - Select a match
    - Run full analytics
    - View intelligence metrics
    """

    def __init__(self, parent, controller):
        super().__init__(parent)

        self.controller = controller

        self.match_var = tk.StringVar()

        self._build_layout()

        self.load_matches()

    # --------------------------------------------------
    # Layout
    # --------------------------------------------------

    def _build_layout(self):

        header = ttk.Label(self, text="Match Analysis", font=("Arial", 18))
        header.pack(pady=10)

        # Match selection
        selection_frame = ttk.Frame(self)
        selection_frame.pack(pady=10)

        ttk.Label(selection_frame, text="Select Match:").pack(side="left", padx=5)

        self.match_dropdown = ttk.Combobox(
            selection_frame,
            textvariable=self.match_var,
            state="readonly",
            width=40
        )
        self.match_dropdown.pack(side="left", padx=5)

        run_button = ttk.Button(
            selection_frame,
            text="Run Analysis",
            command=self.run_analysis
        )
        run_button.pack(side="left", padx=10)

        # Results table
        self.results_table = ttk.Treeview(
            self,
            columns=("metric", "value"),
            show="headings",
            height=15
        )

        self.results_table.heading("metric", text="Metric")
        self.results_table.heading("value", text="Value")

        self.results_table.column("metric", width=250)
        self.results_table.column("value", width=200)

        self.results_table.pack(pady=15, fill="both", expand=True)

        # Export button
        export_button = ttk.Button(
            self,
            text="Generate Report",
            command=self.generate_report
        )
        export_button.pack(pady=10)

    # --------------------------------------------------
    # Load Matches
    # --------------------------------------------------

    def load_matches(self):

        try:
            matches = self.controller.get_all_matches()

            values = [
                f"{m.match_id} - {m.map_name} vs {m.enemy_team}"
                for m in matches
            ]

            self.match_dropdown["values"] = values

        except Exception as e:
            messagebox.showerror("Error", str(e))

    # --------------------------------------------------
    # Run Analysis
    # --------------------------------------------------

    def run_analysis(self):

        if not self.match_var.get():
            messagebox.showwarning("Warning", "Select a match first.")
            return

        match_id = int(self.match_var.get().split(" - ")[0])

        try:

            metrics = self.controller.analyze_match(match_id)

            self.display_metrics(metrics)

        except Exception as e:
            messagebox.showerror("Analysis Error", str(e))

    # --------------------------------------------------
    # Display Results
    # --------------------------------------------------

    def display_metrics(self, metrics):

        for row in self.results_table.get_children():
            self.results_table.delete(row)

        for key, value in metrics.items():
            self.results_table.insert("", "end", values=(key, value))

    # --------------------------------------------------
    # Generate Report
    # --------------------------------------------------

    def generate_report(self):

        if not self.match_var.get():
            messagebox.showwarning("Warning", "Select a match first.")
            return

        match_id = int(self.match_var.get().split(" - ")[0])

        try:

            path = self.controller.generate_match_report(match_id)

            messagebox.showinfo(
                "Report Generated",
                f"Report saved to:\n{path}"
            )

        except Exception as e:
            messagebox.showerror("Report Error", str(e))