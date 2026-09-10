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

echo -e "${BLUE}=== Northstar M2.2 smoke test ===${NC}"

echo -e "${BLUE}--- Checking prerequisites ---${NC}"
command_exists go || { echo -e "${RED}go is required${NC}" >&2; exit 1; }
command_exists npx || { echo -e "${RED}npx (Node.js) is required${NC}" >&2; exit 1; }
[[ -x "$ROOT/.venv/bin/python" ]] || {
  echo -e "${RED}Error: $ROOT/.venv not found.${NC}" >&2
  echo "Create it: uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium" >&2
  exit 1
}
# `platform/policy-decision-service` joined this loop in the M2.2 fix round
# (final-review finding 1, ruling R42). It is not a new dependency — it is an
# existing one finally being declared. `agents/settlement-agent`'s
# `tests/test_policy_deny_contract.py` imports `pds.rules` and `pds.server`,
# which import the git-ignored generated stub `ext_mcp_pb2`, so this gate
# already could not run without the PDS being set up: removing the stubs
# aborts the settlement-agent suite at collection (0 of 79 tests run) and
# reddens `tests/unit`'s per-service count guard. The gate was paying the whole
# cost of that dependency and collecting none of the PDS's own tests, while
# README told a new operator the PDS setup was not needed. Both halves are
# fixed: the venv is required here, the stubs are generated below, and the
# suite is run.
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service; do
  [[ -x "$ROOT/$svc/.venv/bin/python" ]] || {
    echo -e "${RED}Error: $ROOT/$svc/.venv not found.${NC}" >&2
    echo "Create it: (cd $svc && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)" >&2
    exit 1
  }
done
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
kubectl cluster-info --context kind-northstar >/dev/null 2>&1 || {
  echo -e "${RED}Error: cannot reach the 'kind-northstar' cluster.${NC}" >&2
  # M2.1 Task 1: this said `--stage 1` until Cilium arrived. Following that
  # after a cluster loss rebuilds on kindnet, silently reverting the CNI this
  # gate's own trace assertions exist to verify — and the stage-2 kindnet
  # guard would then refuse, but only after a second delete. Stage 2 is the
  # current cluster; `--stage 1` is still supported, it is just no longer what
  # this repo's own instructions should point a reader at.
  echo "Run ./scripts/setup-kind.sh --stage 2 first." >&2
  exit 1
}
# The e2e/browser assertions below need all four port-forwards up. They die
# after any redeploy and simply need re-establishing (A9) — check for that
# now, so a stale/missing forward fails in a second, not after minutes of
# test timeouts that look like a broken chain.
#
# TWO checks per port, and the second one is the one that matters.
# `lsof -sTCP:LISTEN` answers "is a socket bound", which is NOT the same
# question. RULING R20 measured exactly this: a `kubectl port-forward` whose
# target pod has been replaced keeps its listening socket open forever, and
# three of four forwards were dead while `lsof` called all four healthy. So
# until the M2.2 fix round (final-review finding 3) this block could pass on a
# set of dead forwards and the gate would then spend minutes in e2e timeouts —
# the precise outcome the paragraph above claims it prevents.
#
# The liveness check is therefore an actual HTTP request through the forward,
# which is what `tests/e2e/test_policy_denial.py::_claims_agent_alive` already
# does and documents ("a dead kubectl port-forward keeps its listening socket,
# so both lsof and a bare TCP connect report healthy against a forward that
# will refuse every request"). Any HTTP response counts as alive: this is a
# liveness probe, not a contract check. `payments-mcp`'s `/mcp` answers `400`
# to a bare GET with no MCP session, and that is a perfectly good proof that
# the forward carries a request. What fails is curl not getting an HTTP
# response at all — refused, reset, empty reply or timeout — which is what a
# dead forward produces.
#
# `lsof` is kept ahead of it only because it separates two operator actions:
# "start the forward" from "restart the dead forward". Both messages name the
# command.
command_exists curl || { echo -e "${RED}curl is required (the port-forward liveness check uses it)${NC}" >&2; exit 1; }
# port:service:path — the path is any endpoint that answers HTTP without
# authentication or a session. The two agents serve their A2A agent card; the
# ledger serves /healthz; payments-mcp answers /mcp with 400 and that is fine.
for probe in \
  "19001:claims-agent:/.well-known/agent-card.json" \
  "19002:settlement-agent:/.well-known/agent-card.json" \
  "18000:payments-mcp:/mcp" \
  "18080:payments-api:/healthz"; do
  port="${probe%%:*}"
  rest="${probe#*:}"
  name="${rest%%:*}"
  path="${rest#*:}"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1 || {
    echo -e "${RED}Error: nothing is listening on port ${port} (${name}).${NC}" >&2
    echo "Port-forwards die after a redeploy and need re-establishing: kubectl -n <namespace> port-forward svc/${name} ${port}:<container-port> (or re-run ./scripts/setup-kind.sh --stage 2)." >&2
    exit 1
  }
  # -o /dev/null: the body is irrelevant, only whether one arrived.
  # -m 5: a live forward answers these paths in milliseconds; five seconds is
  # far past that and still a second per port in the worst case.
  curl -s -o /dev/null -m 5 "http://localhost:${port}${path}" || {
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request.${NC}" >&2
    echo "This is a dead kubectl port-forward: its target pod was replaced, the socket stayed bound, and lsof still calls it healthy (ruling R20). Nothing here is broken in the cluster." >&2
    echo "Kill that forward and re-establish it: lsof -nP -iTCP:${port} -sTCP:LISTEN  # then kill that PID" >&2
    echo "  kubectl -n <namespace> port-forward svc/${name} ${port}:<container-port>   (or re-run ./scripts/setup-kind.sh --stage 2)" >&2
    exit 1
  }
done

echo ""
echo -e "${BLUE}--- Step 1/2: Unit tests ---${NC}"

# Generate the ExtMCP gRPC stubs FIRST, before any suite runs. Two suites below
# need them and neither says so today:
#   * platform/policy-decision-service/tests/ refuses to import without them
#     (its conftest.py raises rather than skipping — ruling R22);
#   * agents/settlement-agent/tests/test_policy_deny_contract.py imports
#     pds.server, which imports ext_mcp_pb2, so it aborts at collection.
# They are git-ignored on purpose (R22): proto/ext_mcp.proto is the single
# source of truth and the Dockerfile runs this same script at image build time,
# so committing the stubs would let the image and the repo disagree. Generating
# them here — rather than committing them, and rather than the previous
# arrangement of depending on them silently — is the whole of the fix for
# final-review finding 1.
echo -e "${YELLOW}policy-decision-service: ExtMCP stubs (gen-stubs.sh)${NC}"
(cd "$ROOT/platform/policy-decision-service" && ./gen-stubs.sh)

echo -e "${YELLOW}payments-api (Go)${NC}"
(cd "$ROOT/systems/payments-api" && GOTOOLCHAIN=local go test ./...)

echo -e "${YELLOW}payments-mcp${NC}"
(cd "$ROOT/tools/payments-mcp" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}settlement-agent${NC}"
(cd "$ROOT/agents/settlement-agent" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}claims-agent${NC}"
(cd "$ROOT/agents/claims-agent" && .venv/bin/python -m pytest tests -q)

# The suite covering the component that makes the decision this whole milestone
# is named for. Outside the gate until the M2.2 fix round; see the venv loop's
# comment above and limitation 10 in docs/telemetry-contract.md.
echo -e "${YELLOW}policy-decision-service${NC}"
(cd "$ROOT/platform/policy-decision-service" && .venv/bin/python -m pytest tests -q)

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
echo -e "${GREEN}M2.2 smoke test passed.${NC}"
