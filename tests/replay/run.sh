#!/usr/bin/env bash
# Runs harness.py inside an image. record goes out only through the flights proxy and writes
# fixtures.json.gz and live.json; replay runs offline on the recordings and writes OUT.
#   tests/replay/run.sh record IMAGE
#   tests/replay/run.sh replay IMAGE OUT
set -euo pipefail
MODE=$1 IMAGE=$2 OUT=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)
PROXY_IMAGE=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E -m1 '^lets-fly-proxy:')
RUN=(docker run --rm -i --init --pull never --read-only --tmpfs /tmp:size=256m --cap-drop=ALL
     --security-opt=no-new-privileges --memory=1g --pids-limit=128 --entrypoint sh)
CMD="mkdir /tmp/t && tar -x -C /tmp/t && exec python -I /tmp/t/harness.py $MODE"

if [ "$MODE" = record ]; then
  S="lets-fly-record-$$"
  trap 'docker rm -f "$S-proxy" >/dev/null 2>&1; docker network rm "$S" >/dev/null 2>&1' EXIT
  docker network create --internal "$S" >/dev/null
  docker run -d --rm --init --pull never --name "$S-proxy" --cap-drop=ALL --security-opt=no-new-privileges \
    --read-only --memory=128m --pids-limit=64 "$PROXY_IMAGE" 3128 www.google.com,promociones-aereas.com.ar "$S" >/dev/null
  docker network connect --alias proxy "$S" "$S-proxy"
  tar -c -C "$HERE" harness.py | "${RUN[@]}" --network "$S" -e HTTPS_PROXY=http://proxy:3128 \
    -e https_proxy=http://proxy:3128 "$IMAGE" -c "$CMD" > "$HERE/.record.out"
  docker logs "$S-proxy" 2>&1 | grep -v LISTEN | awk '{print $2, $3}' | sort | uniq -c
  python3 -I - "$HERE" <<'EOF'
import base64, json, sys
here = sys.argv[1]
lines = open(f"{here}/.record.out").read().splitlines()
outs = {json.loads(l[7:])["name"]: json.loads(l[7:])["output"] for l in lines if l.startswith("OUTPUT ")}
fx = next(l[9:] for l in lines if l.startswith("FIXTURES "))
open(f"{here}/fixtures.json.gz", "wb").write(base64.b64decode(fx))
json.dump(outs, open(f"{here}/live.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
print(f"{len(outs)} scenarios, fixtures {len(base64.b64decode(fx)) // 1024} KB")
EOF
  rm "$HERE/.record.out"
else
  tar -c -C "$HERE" harness.py fixtures.json.gz | "${RUN[@]}" --network none "$IMAGE" -c "$CMD" |
    python3 -I -c '
import json, sys
outs = {}
for l in sys.stdin:
    if l.startswith("OUTPUT "):
        r = json.loads(l[7:]); outs[r["name"]] = r["output"]
json.dump(outs, open(sys.argv[1], "w"), ensure_ascii=False, indent=1, sort_keys=True)
print(f"{len(outs)} scenarios")' "$OUT"
fi
