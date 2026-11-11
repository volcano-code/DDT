#!/bin/sh
# Administrator-provisioned CI runner only. No installation, sudo, vendor login,
# policy editing, automatic upload or network fallback.
set -eu
[ "$(id -u)" -ne 0 ] || { echo 'Run native acceptance as the approved ordinary test user.' >&2; exit 2; }
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$#" -eq 2 ] || { echo 'Usage: ubuntu24-native-run.sh PREPARED_PRIVATE_ROOT NEW_REPORT_FILE' >&2; exit 2; }
command -v xvfb-run >/dev/null
command -v dbus-run-session >/dev/null
exec xvfb-run -a -s '-screen 0 1280x1024x24 -nolisten tcp' \
  dbus-run-session -- /usr/bin/python3 "$BASE/ci/native_gate.py" --root "$1" --output "$2" --original-client
