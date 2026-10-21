#!/usr/bin/env bash
# Only a synthetic, self-written platform test; no vendor code, model, or business data.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo 'CI-only entry'; exit 2; }
[[ "$(id -u)" != 0 ]] || { echo 'Refusing root GUI'; exit 2; }
BASE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
JDK=/usr/lib/jvm/java-21-openjdk-amd64
WORK=$(mktemp -d "${RUNNER_TEMP:?}/ddt-public-platform.XXXXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
mkdir -p "$WORK/home" "$WORK/cache" "$WORK/classes"
python3 "$BASE/ci/fetch_public_swt.py" "$WORK/swt.jar" "$JDK"
"$JDK/bin/javac" --release 17 -encoding UTF-8 -cp "$WORK/swt.jar" -d "$WORK/classes" "$BASE/ci/PublicPlatformProbe.java"
for n in 1 2 3 4 5; do
    RUN=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
    env -i HOME="$WORK/home" XDG_CACHE_HOME="$WORK/cache" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
      PATH="$JDK/bin:/usr/bin:/bin" /usr/bin/timeout 55 \
      /usr/bin/dbus-run-session -- /usr/bin/xvfb-run -a -s '-screen 0 1024x768x24 -nolisten tcp' \
      "$JDK/bin/java" -Dfile.encoding=UTF-8 -cp "$WORK/classes:$WORK/swt.jar" \
      PublicPlatformProbe "$RUN" "$WORK/result-$n.json"
    python3 - "$WORK/result-$n.json" "$RUN" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert r['run_id']==sys.argv[2] and r['status']=='passed'
assert r['gtk_cycles']==r['gtk_events']==r['webkit_cycles']==2
assert r['bridge_arguments'] is True and r['bridge_return'] is True
assert r['vendor_client_loaded'] is False and r['business_verified'] is False
print(json.dumps(r,ensure_ascii=False))
PY
done
printf '%s\n' 'Five public platform runs passed; NO original client/login/export acceptance.'
