#!/usr/bin/env bash
# macOS / Linux counterpart of start.ps1.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    PY=""
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    done
    if [ -z "$PY" ]; then
        echo "YT Desk needs Python 3.11+ (brew install python)." >&2
        exit 1
    fi
    "$PY" -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "Warning: ffmpeg not found. Install it (brew install ffmpeg) for audio conversion and cover art." >&2
fi

if [ ! -d frontend/node_modules ]; then
    (cd frontend && npm install)
fi

if [ ! -f frontend/dist/index.html ]; then
    (cd frontend && npm run build)
fi

exec ./.venv/bin/python run.py
