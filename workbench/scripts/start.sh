#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-python3}"
if [ -x .venv/bin/python ]; then PYTHON="$PWD/.venv/bin/python"; fi
if [ ! -f web/assets/workbench.js ]; then
  printf '%s\n' 'Missing compiled UI. Run: cd frontend && npm ci && npm run build' >&2
  exit 2
fi
"$PYTHON" scripts/generate_public_fixtures.py
"$PYTHON" scripts/init_local.py
export PYTHONPATH="$PWD/backend"
exec "$PYTHON" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port "${PORT:-8000}" --no-access-log
