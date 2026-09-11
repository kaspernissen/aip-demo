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

# M3 Task 5B: renamed from "M2.2" here and on the success line at the bottom,
# following the precedent 8c0e980 set for the same reason -- the gate now
# covers M3's refusal record, reviewer UI and approved-execution assertions, so
# a banner and a sign-off naming M2.2 misreported what a passing run had
# proven. That commit's own message called it "the same stale-prose class the
# final review found thirteen times". No test pins either string; the only
# references are this file's own two echoes (re-checked with grep across the
# repo, not assumed).
echo -e "${BLUE}=== Northstar M3 smoke test ===${NC}"

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
#
# `apps/reviewer-ui` joins the loop in M3 Task 5B for the same reason, and it is
# the same kind of fix: an existing undeclared dependency, not a new one.
# `tests/unit/test_telemetry_contract_counts.py` (which Step 2/2 below runs,
# because it runs the whole `tests/` tree) calls
# `_pytest_collected_count(apps/reviewer-ui)`, and that helper asserts
# `<service>/.venv/bin/python` exists before it will count anything. So without
# this venv the gate already failed -- just four minutes later, inside a unit
# test, with a message about a contract count rather than about a missing
# prerequisite. Measured, not assumed: the helper is at
# tests/unit/test_telemetry_contract_counts.py:143-148 and the reviewer-ui row
# at :296. Its own 50-test suite is run below alongside the other six.
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui; do
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
  # guard would then refuse, but only after a second delete. `--stage 1` and
  # `--stage 2` are both still supported; they are just no longer what this
  # repo's own instructions should point a reader at.
  #
  # M3 Task 5B: and it said `--stage 2` until now, which is the SAME defect
  # one stage later and with a worse failure. Stage 3 is the current cluster,
  # and the port loop immediately below requires five ports including
  # reviewer-ui's 8081 — a workload no stage-2 cluster has. So an operator who
  # followed this line rebuilt at stage 2 and then failed the very next check,
  # on a component this script had just told them not to install.
  # tests/unit/test_stage2_cilium_config.py's
  # `test_no_operator_facing_instruction_still_points_at_an_older_stage` now
  # pins both numbers, so the M4 recurrence is caught by a test rather than by
  # a reader.
  echo "Run ./scripts/setup-kind.sh --stage 3 first." >&2
  exit 1
}
# The e2e/browser assertions below need all five ports up. Four are
# `kubectl port-forward`s that die after any redeploy and simply need
# re-establishing (A9); the fifth (reviewer-ui, M3 Task 5A) is normally kept
# alive by kind's own host-port mapping instead (deploy/kind/stage3.yaml),
# but the check below asks the same liveness question of it regardless —
# check for that now, so a stale/missing port fails in a second, not after
# minutes of test timeouts that look like a broken chain.
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
#
# M3 Task 5A: reviewer-ui joins this loop as the fifth port. Unlike the four
# above (plain ClusterIP Services, each reached only through an ephemeral
# `kubectl port-forward`), reviewer-ui's own Service is NodePort and
# deploy/kind/stage3.yaml maps host port 8081 straight to it, so in the
# ordinary case this finds it already alive without anyone having started a
# forward for it. The probe asks the same two questions either way, and the
# remedy line below still names a `kubectl port-forward` as the fallback fix
# — reaching the Service that way (`kubectl -n northstar-agents port-forward
# svc/reviewer-ui 8081:8080`) works regardless of whether the NodePort mapping
# is what's actually missing.
for probe in \
  "19001:claims-agent:/.well-known/agent-card.json" \
  "19002:settlement-agent:/.well-known/agent-card.json" \
  "18000:payments-mcp:/mcp" \
  "18080:payments-api:/healthz" \
  "8081:reviewer-ui:/healthz"; do
  port="${probe%%:*}"
  rest="${probe#*:}"
  name="${rest%%:*}"
  path="${rest#*:}"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1 || {
    echo -e "${RED}Error: nothing is listening on port ${port} (${name}).${NC}" >&2
    echo "Port-forwards die after a redeploy and need re-establishing: kubectl -n <namespace> port-forward svc/${name} ${port}:<container-port> (or re-run ./scripts/setup-kind.sh --stage 3)." >&2
    exit 1
  }
  # -o /dev/null: the body is irrelevant, only whether one arrived.
  # -m 5: a live forward answers these paths in milliseconds; five seconds is
  # far past that and still a second per port in the worst case.
  curl -s -o /dev/null -m 5 "http://localhost:${port}${path}" || {
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request.${NC}" >&2
    echo "This is a dead kubectl port-forward: its target pod was replaced, the socket stayed bound, and lsof still calls it healthy (ruling R20). Nothing here is broken in the cluster." >&2
    echo "Kill that forward and re-establish it: lsof -nP -iTCP:${port} -sTCP:LISTEN  # then kill that PID" >&2
    echo "  kubectl -n <namespace> port-forward svc/${name} ${port}:<container-port>   (or re-run ./scripts/setup-kind.sh --stage 3)" >&2
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
# 2026-09-11 (M3): was `GOTOOLCHAIN=local`. Retired, not weakened. `local`
# means "whatever `go` PATH resolves to and nothing else", which made the
# toolchain a property of the operator's machine rather than of this
# repository -- and that coupling is exactly what docs/upstream-findings.md
# Finding 11 recorded as the blocker to taking otel-go's fix ("adopting the
# fix is a Go toolchain bump, not a module bump"). `go.mod` now carries an
# explicit `toolchain go1.27.1` line, so `auto` resolves to that one exact
# release on every machine; it does not float. The first run on a cold
# machine fetches that toolchain once into the module cache and every run
# after is offline (measured: `GOTOOLCHAIN=auto GOPROXY=off go test ./...
# -list '.*'` exits 0 once it is cached).
(cd "$ROOT/systems/payments-api" && GOTOOLCHAIN=auto go test ./...)

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

# M3 Task 5B: the seventh per-service suite, and the last one still outside the
# gate. Same shape as the PDS row above -- the venv is required in the
# prerequisite loop, so reaching here means it exists. 50 tests, 0.36s measured,
# so this costs the gate nothing. docs/telemetry-contract.md's per-service row
# publishes all seven figures and tests/unit/test_telemetry_contract_counts.py
# re-measures each of them, which means this suite's count was already gating
# before its assertions were.
echo -e "${YELLOW}reviewer-ui${NC}"
(cd "$ROOT/apps/reviewer-ui" && .venv/bin/python -m pytest tests -q)

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
echo -e "${GREEN}M3 smoke test passed.${NC}"
