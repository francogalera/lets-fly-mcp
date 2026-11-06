#!/usr/bin/env bash
# Runs the tests in tests/ inside the core image, with no network and nothing mounted. They read the scripts in bin/.
#   tests/run.sh IMAGE [unittest args]
set -euo pipefail
IMAGE=$1; shift
HERE=$(cd "$(dirname "$0")" && pwd)
tar -c -C "$HERE" --exclude=__pycache__ . -C "$HERE/.." bin |
  docker run --rm -i --init --pull never --network none --read-only --tmpfs /tmp:size=256m --cap-drop=ALL \
    --security-opt=no-new-privileges --memory=1g --pids-limit=128 --entrypoint sh "$IMAGE" \
    -c "mkdir /tmp/t && tar -x -C /tmp/t && cd /tmp/t && exec python -I -m unittest discover -s /tmp/t -t /tmp/t $*"
