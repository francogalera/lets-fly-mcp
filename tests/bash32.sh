#!/usr/bin/env bash
# Runs bin/flights under bash 3.2, as macOS ships it, with no sha256sum, a readlink without -f and docker stubbed:
# the image tag must match the host's, and a search with no terminal must reach docker run.
#   tests/bash32.sh IMAGE BASH32_IMAGE
set -euo pipefail
if [ "${1:-}" != --inside ]; then
  HERE=$(cd "$(dirname "$0")/.." && pwd)
  tar -c -C "$HERE" --exclude=__pycache__ bin src/letsfly docker tests/bash32.sh |
    docker run --rm -i --pull never --network none --read-only --tmpfs /tmp:size=64m,exec --cap-drop=ALL \
      --security-opt=no-new-privileges --memory=256m --pids-limit=128 --entrypoint sh "$2" \
      -c 'mkdir /tmp/r && tar -x -C /tmp/r && exec bash /tmp/r/tests/bash32.sh --inside "$0"' "$1"
  exit
fi

IMAGE=$2
export BB=$(command -v busybox)
mkdir /tmp/bin
for applet in $("$BB" --list); do
  case "$applet" in sha256sum | readlink) ;; *) ln -s "$BB" "/tmp/bin/$applet" ;; esac
done
stub() { printf '#!/bin/sh\n%s\n' "$2" > "/tmp/bin/$1" && chmod +x "/tmp/bin/$1"; }
stub readlink 'case "$1" in -f) echo "readlink: illegal option -- f" >&2; exit 1 ;; esac; exec "$BB" readlink "$@"'
stub shasum '[ "$1" = -a ] && shift 2; exec "$BB" sha256sum "$@"'
stub docker 'echo "$*" >> /tmp/docker.log'
stub python3 'cat > /dev/null'
export PATH=/tmp/bin:/usr/local/bin HOME=/tmp
ln -s /tmp/r/bin/flights /tmp/bin/flights

fail() { echo "FAIL bash 3.2: $1" >&2; exit 1; }
[ "$BASH_VERSION" != "${BASH_VERSION#3.2}" ] || fail "runs on bash $BASH_VERSION"
tag=$(flights image) && [ "$tag" = "$IMAGE" ] || fail "image tag $tag, the host has $IMAGE"
flights --help | grep -q '^usage: flights' || fail "flights --help"
flights search EZE GRU 2026-12-10 2> /tmp/err | cat || fail "search exited $?: $(cat /tmp/err)"
! grep -q 'unbound variable' /tmp/err || fail "$(cat /tmp/err)"
grep -q "^run --rm --init --pull never --name flights-.* $IMAGE python -I -m letsfly search EZE GRU 2026-12-10\$" \
  /tmp/docker.log || fail "no docker run for the search: $(cat /tmp/docker.log)"
echo "bash 3.2: ok"
