#!/usr/bin/env bash
# Validate the Weaver registry and check the generated docs region is current.
# Needs docker and the network (Weaver resolves the semconv dependency).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEAVER_IMAGE="otel/weaver:v0.28.0"
# HOME=/tmp: Weaver caches the semconv checkout under $HOME, and the image's
# user has no home when run as the caller's uid.
run_weaver() {
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$ROOT:/repo" -w /repo "$WEAVER_IMAGE" "$@"
}

echo "weaver registry check"
run_weaver registry check -r telemetry/registry

echo "generated docs region is current"
rm -rf "$ROOT/.demo/weaver-docs"; mkdir -p "$ROOT/.demo/weaver-docs"
run_weaver registry generate -r telemetry/registry -t telemetry/templates markdown /repo/.demo/weaver-docs
python3 "$ROOT/scripts/weaver_docs_region.py" --check "$ROOT/docs/telemetry-contract.md" "$ROOT/.demo/weaver-docs"
echo "ok"
