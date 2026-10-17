#!/usr/bin/env bash
# Validate the Weaver registry and check the generated docs region is current.
# Needs docker and the network (Weaver resolves the semconv dependency).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEAVER_IMAGE="otel/weaver:v0.28.0"
# HOME=/tmp: Weaver caches the semconv checkout under $HOME, and the image's
# user has no home when run as the caller's uid.
run_weaver() {
  bounded 600 docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$ROOT:/repo" -w /repo "$WEAVER_IMAGE" "$@"
}

# `timeout` is not on a stock macOS, so bound a command with a watchdog. A wedged
# docker-credential-desktop stalls `docker pull`/`docker run` at "load metadata"
# forever; the gate must fail with a message, not hang silently.
bounded() {
  local secs="$1"; shift
  "$@" &
  local pid=$!
  ( sleep "$secs"; kill "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  local watchdog=$!
  local rc=0
  wait "$pid" || rc=$?
  kill "$watchdog" 2>/dev/null || true
  wait "$watchdog" 2>/dev/null || true
  return "$rc"
}

if ! docker image inspect "$WEAVER_IMAGE" >/dev/null 2>&1; then
  echo "pulling ${WEAVER_IMAGE} (bounded to 300s)"
  if ! bounded 300 docker pull "$WEAVER_IMAGE"; then
    echo "Error: could not pull ${WEAVER_IMAGE} within 300s. A wedged docker-credential-desktop stalls pulls at 'load metadata'; retry with a throwaway DOCKER_CONFIG that has no credsStore, or pull the image by hand." >&2
    exit 1
  fi
fi

echo "weaver registry check"
run_weaver registry check -r telemetry/registry

echo "generated docs region is current"
rm -rf "$ROOT/.demo/weaver-docs"; mkdir -p "$ROOT/.demo/weaver-docs"
run_weaver registry generate -r telemetry/registry -t telemetry/templates markdown /repo/.demo/weaver-docs
python3 "$ROOT/scripts/weaver_docs_region.py" --check "$ROOT/docs/telemetry-contract.md" "$ROOT/.demo/weaver-docs"
echo "ok"
