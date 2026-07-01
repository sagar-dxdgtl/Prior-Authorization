#!/usr/bin/env bash
#
# start.sh — run the Network-Status Verification Probe (demo).
#
# This app is a single FastAPI service that serves BOTH:
#   • the backend API  (/api/*)          — network verdict, OON benefits, etc.
#   • the frontend UI   (/)              — the single-page app (static/index.html)
# so one process is the whole "fe + be". Open the printed URL in a browser.
#
# Usage:
#   ./start.sh                 # http://127.0.0.1:8000
#   PORT=9000 ./start.sh       # pick a port
#   ./start.sh --reload        # dev auto-reload (extra args pass through to uvicorn)
#
set -euo pipefail
cd "$(dirname "$0")"

# 1) Python venv
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "⚠  no .venv found — create it first:"
  echo "     python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# 2) Environment (STEDI_API_KEY, etc.) — optional; the UI + cached OON work without it.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# 3) Make sure the TiC/TIN crosswalk cache exists (offline, no network).
if [ ! -f .cache/tic_crosswalk.json ]; then
  echo "• building .cache/tic_crosswalk.json (UVC roster + TiC seed)…"
  python -m network_probe.tin_crosswalk >/dev/null
fi

# 4) Hint if OON benefits haven't been prefetched yet (live Stedi call — run it manually).
if [ ! -f .cache/oon_benefits.json ]; then
  echo "• OON benefits not cached yet — the OON tab will be empty until you run:"
  echo "     python -m network_probe.oon_benefits test-data/*.pdf"
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "▶ Network-Status Probe (API + UI) → http://${HOST}:${PORT}"
exec uvicorn network_probe.api:app --host "${HOST}" --port "${PORT}" "$@"
