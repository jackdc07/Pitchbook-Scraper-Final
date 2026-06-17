"""Simple desktop app for the PitchBook scraper.

A small window where you add PitchBook PDFs (or a folder), then either export a
formatted Excel file or sync the results straight into an Airtable base. Built
with tkinter (part of Python) so it packages into a single .exe with no extra
runtime needed.

Run with:  python -m pitchbook_scraper.gui
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .parser import parse_company
from .excel import write_xlsx
from . import airtable

CONFIG_PATH = Path.home() / ".pitchbook_scraper.json"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PitchBook PDF Scraper")
        self.geometry("760x620")
        self.minsize(680, 560)

        self.pdfs: list[Path] = []
        self.companies: list = []
        self.cfg = _load_config()

        self._build_ui()

    # ----- UI -------------------------------------------------------------- #
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        # 1. Files
        files_frame = ttk.LabelFrame(self, text="1. PitchBook PDFs")
        files_frame.pack(fill="both", expand=False, **pad)

        btns = ttk.Frame(files_frame)
        btns.pack(fill="x", padx=8, pady=6)
        ttk.Button(btns, text="Add PDFs…", command=self.add_pdfs).pack(side="left")
        ttk.Button(btns, text="Add Folder…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear", command=self.clear_pdfs).pack(side="left")
        self.count_lbl = ttk.Label(btns, text="0 files")
        self.count_lbl.pack(side="right")

        self.file_list = tk.Listbox(files_frame, height=6)
        self.file_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 2. Output
        out_frame = ttk.LabelFrame(self, text="2. Where to send the data")
        out_frame.pack(fill="x", **pad)

        # Excel
        excel_row = ttk.Frame(out_frame)
        excel_row.pack(fill="x", padx=8, pady=6)
        ttk.Button(excel_row, text="Export to Excel…", command=self.export_excel).pack(side="left")
        ttk.Label(excel_row, text="Save a .xlsx spreadsheet (one row per company).").pack(side="left", padx=8)

        # Airtable
        at = ttk.Frame(out_frame)
        at.pack(fill="x", padx=8, pady=6)
        self.at_token = self._field(at, "Airtable token:", self.cfg.get("token", ""), show="•", width=44)
        self.at_base = self._field(at, "Base ID (app…):", self.cfg.get("base_id", ""), width=44)
        self.at_table = self._field(at, "Table name:", self.cfg.get("table", "Companies"), width=44)

        at_btns = ttk.Frame(out_frame)
        at_btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(at_btns, text="Sync to Airtable", command=self.sync_airtable).pack(side="left")
        ttk.Button(at_btns, text="Show required Airtable columns", command=self.show_columns).pack(side="left", padx=6)

        # 3. Log
        log_frame = ttk.LabelFrame(self, text="Activity")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 10))

        self._log("Add some PitchBook PDFs, then export to Excel or sync to Airtable.")

    def _field(self, parent, label, value, show=None, width=40):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=16).pack(side="left")
        var = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=var, show=show, width=width).pack(side="left", fill="x", expand=True)
        return var

    # ----- helpers --------------------------------------------------------- #
    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def _refresh_files(self) -> None:
        self.file_list.delete(0, "end")
        for p in self.pdfs:
            self.file_list.insert("end", p.name)
        self.count_lbl.configure(text=f"{len(self.pdfs)} file(s)")

    def add_pdfs(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select PitchBook PDFs", filetypes=[("PDF files", "*.pdf")]
        )
        for p in paths:
            pp = Path(p)
            if pp not in self.pdfs:
                self.pdfs.append(pp)
        self._refresh_files()

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select a folder of PDFs")
        if not folder:
            return
        for pp in sorted(Path(folder).rglob("*.pdf")):
            if pp not in self.pdfs:
                self.pdfs.append(pp)
        self._refresh_files()

    def clear_pdfs(self) -> None:
        self.pdfs.clear()
        self.companies.clear()
        self._refresh_files()

    def _scrape_all(self) -> bool:
        """Scrape every PDF into self.companies. Returns True if any succeeded."""
        if not self.pdfs:
            messagebox.showwarning("No files", "Add at least one PitchBook PDF first.")
            return False
        self.companies = []
        self.progress.configure(maximum=len(self.pdfs), value=0)
        for i, pdf in enumerate(self.pdfs, start=1):
            self._log(f"Scraping {pdf.name} …")
            try:
                self.companies.append(parse_company(pdf))
            except Exception as exc:  # noqa: BLE001
                self._log(f"   ! failed: {exc}")
            self.progress.configure(value=i)
        self._log(f"Done. Parsed {len(self.companies)} of {len(self.pdfs)} file(s).")
        return bool(self.companies)

    # ----- actions (threaded) --------------------------------------------- #
    def _run_in_thread(self, fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def export_excel(self) -> None:
        out = filedialog.asksaveasfilename(
            title="Save Excel file",
            defaultextension=".xlsx",
            initialfile="pitchbook_companies.xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not out:
            return

        def job():
            if not self._scrape_all():
                return
            try:
                write_xlsx(self.companies, out)
                self._log(f"Saved Excel: {out}")
                messagebox.showinfo("Done", f"Saved {len(self.companies)} companies to:\n{out}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"Excel error: {exc}")
                messagebox.showerror("Excel error", str(exc))

        self._run_in_thread(job)

    def sync_airtable(self) -> None:
        token = self.at_token.get().strip()
        base = self.at_base.get().strip()
        table = self.at_table.get().strip()
        if not (token and base and table):
            messagebox.showwarning(
                "Missing Airtable details",
                "Enter your Airtable token, Base ID, and Table name first.",
            )
            return
        # Remember (token saved locally only, on this machine).
        self.cfg.update({"token": token, "base_id": base, "table": table})
        _save_config(self.cfg)

        def job():
            if not self._scrape_all():
                return
            self._log(f"Sending {len(self.companies)} record(s) to Airtable…")
            try:
                n = airtable.sync_companies(self.companies, token, base, table)
                self._log(f"Synced {n} record(s) to Airtable table '{table}'.")
                messagebox.showinfo("Done", f"Added {n} companies to Airtable.")
            except Exception as exc:  # noqa: BLE001
                self._log(f"Airtable error: {exc}")
                messagebox.showerror("Airtable error", str(exc))

        self._run_in_thread(job)

    def show_columns(self) -> None:
        cols = "\n".join(f"  • {c}" for c in airtable.COLUMNS)
        messagebox.showinfo(
            "Airtable columns to create",
            "Create a table with these columns (names must match exactly).\n"
            "Single line text works for all; Team Size can be a Number.\n\n" + cols,
        )


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
