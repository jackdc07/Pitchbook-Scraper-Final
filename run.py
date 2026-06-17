#!/usr/bin/env python3
"""Friendly double-click launcher for the PitchBook scraper.

This is for people who don't want to use the command line. It will:
  1. make sure the one dependency (pdfplumber) is installed,
  2. ask you for a PDF file or a folder of PDFs (you can drag it onto the
     window and press Enter),
  3. scrape it and save a CSV next to your input,
  4. stay open so you can read the result.

Run it by double-clicking run.bat (Windows) or run.command (macOS), or just
`python run.py` from a terminal.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _pause_and_exit(code: int = 0) -> None:
    try:
        input("\nPress Enter to close this window...")
    except EOFError:
        pass
    sys.exit(code)


def _ensure_pdfplumber() -> None:
    try:
        import pdfplumber  # noqa: F401
        return
    except ImportError:
        pass
    print("First-time setup: installing the 'pdfplumber' library (needs internet)...\n")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pdfplumber"]
        )
        print("\nInstalled. Continuing...\n")
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not install pdfplumber automatically: {exc}")
        print("Please run this once in a terminal:  pip install pdfplumber")
        _pause_and_exit(1)


def main() -> None:
    # Run from the folder this script lives in so the package imports cleanly.
    here = Path(__file__).resolve().parent
    os.chdir(here)
    sys.path.insert(0, str(here))

    print("=" * 60)
    print("  PitchBook PDF Scraper")
    print("=" * 60)

    _ensure_pdfplumber()

    from pitchbook_scraper.cli import main as cli_main

    raw = input(
        "Drag a PitchBook PDF (or a folder of PDFs) here and press Enter:\n> "
    ).strip().strip('"').strip("'")
    if not raw:
        print("No file given.")
        _pause_and_exit(1)

    target = Path(raw).expanduser()
    if not target.exists():
        print(f"Path not found: {target}")
        _pause_and_exit(1)

    out_dir = target if target.is_dir() else target.parent
    out_csv = out_dir / "pitchbook_results.csv"

    print(f"\nScraping... results will be saved to:\n  {out_csv}\n")
    code = cli_main([str(target), "--format", "csv", "-o", str(out_csv)])

    # Also print a readable summary to the screen.
    if code == 0:
        print("\n----- Summary -----")
        cli_main([str(target), "--format", "text"])

    _pause_and_exit(code)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - keep the window open on any error
        print(f"\nError: {exc}")
        _pause_and_exit(1)
