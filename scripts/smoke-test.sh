#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

echo -e "${BLUE}=== Northstar M1.1 smoke test ===${NC}"

echo -e "${BLUE}--- Checking prerequisites ---${NC}"
command_exists go || { echo -e "${RED}go is required${NC}" >&2; exit 1; }
command_exists npx || { echo -e "${RED}npx (Node.js) is required${NC}" >&2; exit 1; }
[[ -x "$ROOT/.venv/bin/python" ]] || {
  echo -e "${RED}Error: $ROOT/.venv not found.${NC}" >&2
  echo "Create it: uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium" >&2
  exit 1
}
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent; do
  [[ -x "$ROOT/$svc/.venv/bin/python" ]] || {
    echo -e "${RED}Error: $ROOT/$svc/.venv not found.${NC}" >&2
    echo "Create it: (cd $svc && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)" >&2
    exit 1
  }
done
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
kubectl cluster-info --context kind-northstar >/dev/null 2>&1 || {
  echo -e "${RED}Error: cannot reach the 'kind-northstar' cluster.${NC}" >&2
  echo "Run ./scripts/setup-kind.sh --stage 1 first." >&2
  exit 1
}
# The e2e/browser assertions below need all four port-forwards up. They die
# after any redeploy and simply need re-establishing (A9) — check for that
# now, so a stale/missing forward fails in a second, not after minutes of
# test timeouts that look like a broken chain.
for port_name in "19001:claims-agent" "19002:settlement-agent" "18000:payments-mcp" "18080:payments-api"; do
  port="${port_name%%:*}"
  name="${port_name##*:}"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1 || {
    echo -e "${RED}Error: nothing is listening on port ${port} (${name}).${NC}" >&2
    echo "Port-forwards die after a redeploy and need re-establishing: kubectl -n <namespace> port-forward svc/${name} ${port}:<container-port> (or re-run ./scripts/setup-kind.sh --stage 1)." >&2
    exit 1
  }
done

echo ""
echo -e "${BLUE}--- Step 1/2: Unit tests ---${NC}"

echo -e "${YELLOW}payments-api (Go)${NC}"
(cd "$ROOT/systems/payments-api" && GOTOOLCHAIN=local go test ./...)

echo -e "${YELLOW}payments-mcp${NC}"
(cd "$ROOT/tools/payments-mcp" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}settlement-agent${NC}"
(cd "$ROOT/agents/settlement-agent" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}claims-agent${NC}"
(cd "$ROOT/agents/claims-agent" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}customer-ui${NC}"
(cd "$ROOT/apps/customer-ui" && npx vitest run)

echo ""
echo -e "${BLUE}--- Step 2/2: End-to-end trace assertions, browser hop included ---${NC}"
# A22: the milestone's headline claim is that ONE trace spans the browser down
# to payments-api. The default pytest run skips tests/e2e/test_browser_root_span.py
# entirely (it is gated behind RUN_BROWSER_TESTS=1, since it needs a real
# Chromium via Playwright) — which would verify nothing about the top hop of
# the chain. This gate always sets RUN_BROWSER_TESTS=1, unconditionally, for
# exactly one reason: a gate that can be run without exercising the claim it
# exists to verify is worse than no gate, because it reports success. There is
# no environment variable that skips this from here.
#
# This also runs the whole `tests/` tree, not just `tests/e2e` — tests/unit/
# pins the harness's own correctness (single-root/orphan detection, and the
# A20a quiet-window timing the multi-service trace-completeness wait depends
# on). A narrower `tests/e2e`-only invocation would silently drop harness
# coverage the e2e assertions themselves rely on being correct.
RUN_BROWSER_TESTS=1 "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -v

echo ""
echo -e "${GREEN}M1.1 smoke test passed.${NC}"
