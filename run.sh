#!/usr/bin/env bash
# NFC Spiral Antenna Generator - Linux / macOS launcher.
#   ./run.sh            native window (falls back to the browser without pywebview)
#   ./run.sh server.py  local browser application
#   ./run.sh nfc_spiral_gui.py   classic Tkinter interface
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c "import ezdxf, shapely" 2>/dev/null || "$PY" -m pip install --quiet "ezdxf>=1.0" "shapely>=2.0"
exec "$PY" "${1:-desktop.py}"
