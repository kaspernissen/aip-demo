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

echo -e "${BLUE}=== Northstar M13 smoke test ===${NC}"

echo -e "${BLUE}--- Checking prerequisites ---${NC}"
command_exists go || { echo -e "${RED}go is required${NC}" >&2; exit 1; }
command_exists npx || { echo -e "${RED}npx (Node.js) is required${NC}" >&2; exit 1; }
[[ -x "$ROOT/.venv/bin/python" ]] || {
  echo -e "${RED}Error: $ROOT/.venv not found.${NC}" >&2
  echo "Create it: uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium" >&2
  exit 1
}
# Every service below needs its own venv before this gate can run at all, and
# this loop is what turns a missing one into a clear message here rather than
# a confusing failure deep inside a later step:
#
# * `platform/policy-decision-service` -- `agents/settlement-agent`'s
#   `tests/test_policy_deny_contract.py` imports `pds.rules` and `pds.server`,
#   which import the git-ignored generated stub `ext_mcp_pb2`, so the gate
#   cannot run without the PDS being set up regardless of this check; the
#   stubs themselves are generated below, and the PDS's own suite is run
#   alongside the rest.
# * the others -- the gate runs each service's own suite below.
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui apps/customer-ui/server; do
  [[ -x "$ROOT/$svc/.venv/bin/python" ]] || {
    echo -e "${RED}Error: $ROOT/$svc/.venv not found.${NC}" >&2
    echo "Create it: (cd $svc && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)" >&2
    [[ -f "$ROOT/$svc/requirements-dev.txt" ]] && echo "  (this one carries its test packages in requirements-dev.txt: install that instead)" >&2
    exit 1
  }
done
# `platform/backstage`'s jest suites need `node_modules` before
# `backstage-cli repo test` can run.
[[ -x "$ROOT/platform/backstage/node_modules/.bin/backstage-cli" ]] || {
  echo -e "${RED}Error: $ROOT/platform/backstage/node_modules not found.${NC}" >&2
  echo "Create it: (cd platform/backstage && yarn install)" >&2
  exit 1
}
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
kubectl cluster-info --context kind-northstar >/dev/null 2>&1 || {
  echo -e "${RED}Error: cannot reach the 'kind-northstar' cluster.${NC}" >&2
  echo "Run ./scripts/setup-kind.sh --stage 7 first." >&2
  exit 1
}
# The gate must PROVE it is running on a stage-7 cluster rather than be told
# so by a README: tests/e2e/test_keystone.py skips when Kyverno's CRD is absent (a
# coherently-below-stage-7 cluster), and a skip is invisible in a green gate --
# the zero-skips check at the end would catch it, but only after ten minutes.
# Ask the cluster now: the keystone policy must exist and be Ready, and Kyverno's
# resource webhook must be REGISTERED. The second is not redundant: a graceful
# stop of the admission controller removes the registration while the policy
# object stays Ready-looking, and an agent's write then passes (measured).
[[ "$(kubectl --context kind-northstar get validatingpolicy agents-cannot-edit-their-own-authority -o jsonpath='{.status.conditionStatus.ready}' 2>/dev/null)" == "true" ]] || {
  echo -e "${RED}Error: validatingpolicy/agents-cannot-edit-their-own-authority is missing or not Ready.${NC}" >&2
  echo "This cluster is below stage 7, or Kyverno is not up. The gate runs scripts/demo keystone and tests/e2e/test_keystone.py." >&2
  echo "Run ./scripts/setup-kind.sh --stage 7 first." >&2
  exit 1
}
[[ -n "$(kubectl --context kind-northstar get validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg --ignore-not-found -o name 2>/dev/null)" ]] || {
  echo -e "${RED}Error: Kyverno's resource webhook configuration is not registered, so the keystone is not enforcing.${NC}" >&2
  echo "Scale deploy/kyverno-admission-controller in the kyverno namespace back to 1 and wait for it." >&2
  exit 1
}
# The e2e/browser assertions below need all five ports up. Four are
# `kubectl port-forward`s that die after any redeploy and simply need
# re-establishing; the fifth (reviewer-ui) is normally kept alive by kind's
# own host-port mapping instead (deploy/kind/stage3.yaml), but the check
# below asks the same liveness question of it regardless — check for that
# now, so a stale/missing port fails in a second, not after minutes of test
# timeouts that look like a broken chain.
#
# TWO checks per port, and the second one is the one that matters.
# `lsof -sTCP:LISTEN` answers "is a socket bound", which is NOT the same
# question: a `kubectl port-forward` whose target pod has been replaced
# keeps its listening socket open forever -- measured directly: three of four
# forwards were dead while `lsof` called all four healthy. `lsof` alone would
# pass on a set of dead forwards, and the gate would then spend minutes in
# e2e timeouts — the precise outcome the paragraph above exists to prevent.
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
# reviewer-ui joins this loop as the fifth port. Unlike the four
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
    # scripts/start-demo.sh owns every port-forward this demo needs (and
    # re-verifies each one with this same lsof-then-curl shape), so the
    # remedy the echo below names is one command, not a line an operator has
    # to assemble by hand and get right five times. This file still creates
    # no port-forwards itself; only the remedy text below points at the
    # script that does.
    #
    # A comment must not be able to satisfy a guard: the remedy at the end of
    # the `curl` branch below also names the script, and
    # tests/unit/test_stage4_config.py strips whole-line comments and
    # requires BOTH remedies to name it by their own text, so reverting
    # either one goes red on its own.
    echo "Port-forwards die after a redeploy and need re-establishing: ./scripts/start-demo.sh (or re-run ./scripts/setup-kind.sh --stage 7 first if the cluster itself is gone)." >&2
    exit 1
  }
  # -o /dev/null: the body is irrelevant, only whether one arrived.
  # -m 5: a live forward answers these paths in milliseconds; five seconds is
  # far past that and still a second per port in the worst case.
  curl -s -o /dev/null -m 5 "http://localhost:${port}${path}" || {
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request.${NC}" >&2
    echo "This is a dead kubectl port-forward: its target pod was replaced, the socket stayed bound, and lsof still calls it healthy. Nothing here is broken in the cluster." >&2
    echo "Re-establish every forward, verified live rather than merely bound: ./scripts/start-demo.sh" >&2
    exit 1
  }
done

echo ""
echo -e "${BLUE}--- Step 1/2: Unit tests ---${NC}"

# The telemetry registry (M9): `weaver registry check`, plus the generated docs
# region in docs/telemetry-contract.md being current. Needs docker and the
# network (Weaver resolves the semconv dependency), like the live-check pod.
echo -e "${YELLOW}telemetry registry (weaver registry check)${NC}"
"$ROOT/scripts/check-registry.sh"

# Generate the ExtMCP gRPC stubs FIRST, before any suite runs. Two suites below
# need them and neither says so today:
#   * platform/policy-decision-service/tests/ refuses to import without them
#     (its conftest.py raises rather than skipping);
#   * agents/settlement-agent/tests/test_policy_deny_contract.py imports
#     pds.server, which imports ext_mcp_pb2, so it aborts at collection.
# They are git-ignored on purpose: proto/ext_mcp.proto is the single source of
# truth and the Dockerfile runs this same script at image build time, so
# committing the stubs would let the image and the repo disagree. Generating
# them here, rather than committing them, is what keeps the two from drifting.
echo -e "${YELLOW}policy-decision-service: ExtMCP stubs (gen-stubs.sh)${NC}"
(cd "$ROOT/platform/policy-decision-service" && ./gen-stubs.sh)

echo -e "${YELLOW}payments-api (Go)${NC}"
# `GOTOOLCHAIN=auto`, not `local`: `local` means "whatever `go` PATH resolves
# to and nothing else", which would make the toolchain a property of the
# operator's machine rather than of this repository -- the same coupling
# docs/upstream-findings.md Finding 11 records as the blocker to taking
# otel-go's fix ("adopting the fix is a Go toolchain bump, not a module
# bump"). `go.mod` carries an explicit `toolchain go1.27.1` line, so `auto`
# resolves to that one exact release on every machine; it does not float.
# The first run on a cold machine fetches that toolchain once into the
# module cache and every run after is offline (measured: `GOTOOLCHAIN=auto
# GOPROXY=off go test ./... -list '.*'` exits 0 once it is cached).
(cd "$ROOT/systems/payments-api" && GOTOOLCHAIN=auto go test ./...)

echo -e "${YELLOW}payments-mcp${NC}"
(cd "$ROOT/tools/payments-mcp" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}settlement-agent${NC}"
(cd "$ROOT/agents/settlement-agent" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}claims-agent${NC}"
(cd "$ROOT/agents/claims-agent" && .venv/bin/python -m pytest tests -q)

# The suite covering the component that makes the policy decision. See the
# venv loop's comment above and limitation 10 in docs/telemetry-contract.md.
echo -e "${YELLOW}policy-decision-service${NC}"
(cd "$ROOT/platform/policy-decision-service" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}customer-ui${NC}"
(cd "$ROOT/apps/customer-ui" && npx vitest run)

# customer-ui's Python server (relay, token handling, the static export).
echo -e "${YELLOW}customer-ui server${NC}"
(cd "$ROOT/apps/customer-ui/server" && .venv/bin/python -m pytest tests -q)

echo -e "${YELLOW}reviewer-ui${NC}"
(cd "$ROOT/apps/reviewer-ui" && .venv/bin/python -m pytest tests -q)

# backstage's own suite: six autonomy-contract plugin test files (evaluate,
# k8sClient, router, telemetry, index, AutonomyContractPage) carry, among
# other things, the jest guards for its review-round correctness fixes -- each
# was proven GREEN-to-RED once, by hand, and this line is what makes that a
# standing regression guard instead of a one-time check. `CI=true` is NOT
# optional: `backstage-cli repo test` is a thin wrapper over jest, and jest
# defaults to watch mode without it, which would hang the gate forever rather
# than fail it.
echo -e "${YELLOW}backstage (jest)${NC}"
(cd "$ROOT/platform/backstage" && CI=true node_modules/.bin/backstage-cli repo test --silent)

echo ""
echo -e "${BLUE}--- The guard and keystone beats, as the demo runs them ---${NC}"
# `scripts/demo guard` and `scripts/demo keystone` exit non-zero if a refusal
# does NOT fire, and `set -e` fails the gate. A throwaway state dir keeps the
# gate's run out of an operator's rehearsal state. keystone removes its
# over-grant on every exit path. tests/e2e/test_keystone.py runs in Step 2.
DEMO_STATE_DIR="$(mktemp -d -t northstar-gate-demo-XXXXXX)" "$ROOT/scripts/demo" guard
DEMO_STATE_DIR="$(mktemp -d -t northstar-gate-demo-XXXXXX)" "$ROOT/scripts/demo" keystone

echo ""
echo -e "${BLUE}--- Step 2/2: End-to-end trace assertions, browser hop included ---${NC}"
# Weaver live-check (M9) is emptied HERE, after the unit suites and the two
# beats and before the e2e run, so its report covers exactly the traffic
# Step 2 drives: every span, metric and log the collector copies to it. The
# restart is `kubectl rollout restart` plus a wait for Ready, done by the
# harness, which also opens its own port-forward to read the admin API -- a
# rollout kills the operator forward on localhost:18400, and this file creates
# no port-forwards (tests/harness/livecheck.py says why). An unreachable
# live-check fails the gate.
echo -e "${YELLOW}weaver live-check: restarting for an empty report${NC}"
PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m tests.harness.livecheck restart || {
  echo -e "${RED}Error: weaver live-check is unreachable or did not restart.${NC}" >&2
  echo "It needs deploy/weaver-live-check in the observability namespace: kubectl -n observability get pods -l app=weaver-live-check (or ./scripts/setup-kind.sh --stage 7)." >&2
  exit 1
}
# This system's headline claim is that ONE trace spans the browser down
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
# quiet-window timing the multi-service trace-completeness wait depends on).
# A narrower `tests/e2e`-only invocation would silently drop harness coverage
# the e2e assertions themselves rely on being correct.
#
# A skip is invisible in a green gate. The stage-7 refusal above covers
# Kyverno, but the pytest invocation itself carries nothing that fails on a
# skip, while several modules running inside it hold reachable `pytest.skip`
# calls on conditions that refusal does not cover (test_egress_policy.py,
# test_gateway_is_the_only_route.py, test_gateway_config_drift.py,
# test_human_in_the_loop.py, test_settlement_llm_trace.py and the cluster-reachability
# skips in the Prometheus module). Each is defensible on its own;
# collectively, nothing would stop a green gate that had silently skipped an
# assertion it exists to run. So "zero skips" is checked below as a property
# of the gate, not merely verified once by hand after the fact.
#
# `tee` rather than a variable so the operator still watches it live, and
# PIPESTATUS so pytest's own exit code survives the pipe -- under `set -e` a
# pipeline's status is its LAST command's, which would be `tee`'s, and a
# failing suite would sail past.
SKIP_LOG="$(mktemp -t northstar-gate-XXXXXX)"
trap 'rm -f "$SKIP_LOG"' EXIT
set +e
# tests/e2e/test_registry_live_check.py is NOT in this run (`--ignore`): it reads
# the report of everything this run emitted, so it runs as its own step after it.
RUN_BROWSER_TESTS=1 "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -v \
  --ignore="$ROOT/tests/e2e/test_registry_live_check.py" 2>&1 | tee "$SKIP_LOG"
PYTEST_STATUS=${PIPESTATUS[0]}
set -e
if [[ $PYTEST_STATUS -ne 0 ]]; then
  echo -e "${RED}The gate's test run failed (exit $PYTEST_STATUS).${NC}" >&2
  exit "$PYTEST_STATUS"
fi

# The live-check verdict, AFTER everything above has emitted its telemetry. Its
# own pytest run, with its own skip accounting: a skipped live-check is a
# failure here, like every other skip in this gate. The test saves the report it
# read to .demo/m9-live-check-report.json; the gate fails if that file is missing.
LIVE_CHECK_REPORT="$ROOT/.demo/m9-live-check-report.json"
rm -f "$LIVE_CHECK_REPORT"
LIVE_CHECK_LOG="$(mktemp -t northstar-live-check-XXXXXX)"
trap 'rm -f "$SKIP_LOG" "$LIVE_CHECK_LOG"' EXIT
set +e
RUN_BROWSER_TESTS=1 "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests/e2e/test_registry_live_check.py" -v 2>&1 | tee "$LIVE_CHECK_LOG"
LIVE_CHECK_STATUS=${PIPESTATUS[0]}
set -e
if [[ $LIVE_CHECK_STATUS -ne 0 ]]; then
  echo -e "${RED}The registry live-check failed (exit $LIVE_CHECK_STATUS): the platform emitted telemetry the registry does not describe.${NC}" >&2
  echo "The findings are printed above; the full report is $LIVE_CHECK_REPORT" >&2
  exit "$LIVE_CHECK_STATUS"
fi
if [[ ! -s "$LIVE_CHECK_REPORT" ]]; then
  echo -e "${RED}The live-check test passed but $LIVE_CHECK_REPORT was not written.${NC}" >&2
  exit 1
fi
if grep -qE "[0-9]+ skipped" "$LIVE_CHECK_LOG"; then
  echo -e "${RED}The registry live-check was skipped, which is a failure.${NC}" >&2
  exit 1
fi

# Counted off pytest's own summary line ("N skipped"), not by grepping for
# per-test verdict lines ("^path::test SKIPPED"). The per-test form misses a
# collection-time module skip -- which prints no `path::test` line at all,
# the largest possible instance of what this check exists for -- and any
# parametrised id containing a space, which this tree already has
# (test_reach_policy.py::test_ingress_reach_matrix[agentgateway->payments-mcp:8000 POST /mcp = allow]).
# The old objection to a bare `grep SKIPPED` -- that it also matches the word
# inside a test name -- does not apply to the summary line: pytest's own
# tally is the one place that word means what this check needs it to mean.
SKIPPED=$(sed -n 's/.*=\{2,\}.*[^0-9]\([0-9][0-9]*\) skipped.*/\1/p' "$SKIP_LOG" | tail -1)
SKIPPED=${SKIPPED:-0}
if [[ "$SKIPPED" -ne 0 ]]; then
  echo "" >&2
  echo -e "${RED}The gate passed with ${SKIPPED} skipped test(s), which is a failure.${NC}" >&2
  echo "A skip inside this gate is invisible in a green run: the gate's" >&2
  echo "assertions simply do not execute and the summary still says 'passed'." >&2
  echo "Every gated module is unskipped here by RUN_BROWSER_TESTS=1, and the" >&2
  echo "stage-7 refusal above covers the cluster, so a skip means a condition" >&2
  echo "nobody accounted for. The skipped tests and their reasons:" >&2
  grep -E "^[^ ]+::[^ ]+ SKIPPED" "$SKIP_LOG" >&2 || true
  echo "" >&2
  echo "Fix the condition, or -- if the skip is genuinely correct -- change this" >&2
  echo "check deliberately." >&2
  exit 1
fi

echo ""
echo -e "${GREEN}Northstar M13 smoke test passed.${NC}"
