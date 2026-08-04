#!/usr/bin/env bash
# Start AeroSim Lab. Double-click it, or run ./run-flightsim.sh
set -euo pipefail
cd "$(dirname "$0")"

# Prefer python3; fall back to python where that is the only name available.
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 3.10+ is not installed, or is not on PATH." >&2
    echo "Get it from https://www.python.org/downloads/" >&2
    exit 1
fi

exec "$PY" play.py "$@"
