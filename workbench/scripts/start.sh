#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; fi
if [ ! -f web/assets/workbench.js ]; then
  printf '%s\n' 'Frontend is not built. In workbench/frontend run npm install --ignore-scripts, then npm run build.' >&2
  exit 2
fi
"$PYTHON" scripts/init_local.py
export PYTHONPATH="$PWD/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port "${PORT:-8000}" --no-access-log
