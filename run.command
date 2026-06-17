#!/bin/bash
# Double-click launcher for macOS (and works on Linux too).
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
    python3 run.py
else
    echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"
    read -r -p "Press Enter to close..."
fi
