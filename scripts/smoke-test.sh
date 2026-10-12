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

# A banner and a sign-off naming an older milestone would misreport what a
# passing run proved, so neither string is hand-maintained:
# tests/unit/test_stage4_config.py::test_the_gates_banner_names_the_milestone_it_actually_proves
# DERIVES the expected milestone from the newest design spec in
# docs/superpowers/specs/ and checks both echoes against it. The next
# milestone does not have to remember to come here -- the guard reddens when
# the spec lands. Both strings share the word "Northstar" so they match one
# pattern instead of two.
echo -e "${BLUE}=== Northstar M7 smoke test ===${NC}"

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
# * `apps/reviewer-ui` and `platform/eval-scorer` -- `tests/unit/
#   test_telemetry_contract_counts.py` (which Step 2/2 below runs, since it
#   runs the whole `tests/` tree) calls `_pytest_collected_count` against
#   each service's directory, and that helper asserts
#   `<service>/.venv/bin/python` exists before it will count anything.
#   Without the venv the gate would fail anyway, minutes later, inside a unit
#   test about a contract count rather than about a missing prerequisite.
#   docs/telemetry-contract.md's per-service row publishes each suite's
#   count, and that same test module re-measures it -- not restated here.
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui platform/eval-scorer; do
  [[ -x "$ROOT/$svc/.venv/bin/python" ]] || {
    echo -e "${RED}Error: $ROOT/$svc/.venv not found.${NC}" >&2
    echo "Create it: (cd $svc && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)" >&2
    exit 1
  }
done
# `platform/backstage`'s own jest suites -- six autonomy-contract plugin test
# files plus the two scaffold-default suites -- need `node_modules` in place
# before `backstage-cli repo test` can run. `_jest_test_count` in
# tests/unit/test_telemetry_contract_counts.py re-measures that suite's row
# against `backstage-cli repo test`'s own count, the same contract every row
# above keeps, so this checks the same prerequisite the same way: fail here,
# at a clear missing-toolchain message, rather than later inside a unit test
# about a contract count.
[[ -x "$ROOT/platform/backstage/node_modules/.bin/backstage-cli" ]] || {
  echo -e "${RED}Error: $ROOT/platform/backstage/node_modules not found.${NC}" >&2
  echo "Create it: (cd platform/backstage && yarn install)" >&2
  exit 1
}
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
kubectl cluster-info --context kind-northstar >/dev/null 2>&1 || {
  echo -e "${RED}Error: cannot reach the 'kind-northstar' cluster.${NC}" >&2
  # Always names the current stage, never an older one: rebuilding at an
  # older stage would silently revert the CNI this gate's own trace
  # assertions exist to verify, and would come up without the workloads
  # (reviewer-ui, OpenSearch, ...) the checks below require -- so an operator
  # following an older instruction would just fail the very next check, on a
  # component this script had just told them not to install.
  # tests/unit/test_stage2_cilium_config.py's
  # `test_no_operator_facing_instruction_still_points_at_an_older_stage`
  # keeps this pinned to the current stage as a test rather than trusting a
  # reader to notice it drifted.
  echo "Run ./scripts/setup-kind.sh --stage 7 first." >&2
  exit 1
}
# The gate must PROVE it is running on a stage-4 cluster rather than be told
# so by a README.
#
# tests/e2e/test_opensearch_trace_ingest.py is what catches a span silently
# failing to reach OpenSearch. It SKIPS on a coherently stage-3 cluster,
# deliberately: OpenSearch is a stage-4 component, and a test that failed for
# its absence would cry wolf on every lower stage.
#
# But a skip is invisible in a green gate. Run this gate on a stage-3 cluster
# and it reports that file's every assertion as a skip, exit 0, with the checks
# this milestone rests on never executed and nothing anywhere going red. "A guard
# that skips on the object it guards" is this repo's most recurrent defect
# class; here the skip is the right behaviour for the test and the wrong
# behaviour for the GATE, so the gate refuses to run below stage 4 instead of
# running weaker and saying nothing about it.
#
# Asked of the cluster, not of a flag or an env var: the Service either exists
# or it does not. tests/unit/test_smoke_test_gate.py pins that this check is
# here and that it is not conditional.
kubectl --context kind-northstar -n observability get svc/opensearch-cluster-master >/dev/null 2>&1 || {
  echo -e "${RED}Error: svc/opensearch-cluster-master not found in the observability namespace.${NC}" >&2
  echo "This cluster is below stage 4. The gate runs tests/e2e/test_opensearch_trace_ingest.py -- the trace-ingest guard -- and on a cluster without OpenSearch every one of its assertions SKIPs, so the gate would pass while proving nothing about them." >&2
  echo "Bring the cluster to stage 4. No recreate is needed: stage 4 runs on stage 3's cluster config unchanged." >&2
  echo "Run ./scripts/setup-kind.sh --stage 7 first." >&2
  exit 1
}
# The gate must also PROVE it is running on a stage-7 cluster, for the same
# reason: tests/e2e/test_keystone.py skips when Kyverno's CRD is absent (a
# coherently-below-stage-7 cluster), and a skip is invisible in a green gate --
# the zero-skips check at the end would catch it, but only after ten minutes.
# Ask the cluster now: the keystone policy must exist and be Ready, and Kyverno's
# resource webhook must be REGISTERED. The second is not redundant: a graceful
# stop of the admission controller removes the registration while the policy
# object stays Ready-looking, and an agent's write then passes (measured).
[[ "$(kubectl --context kind-northstar get validatingpolicy agents-cannot-edit-their-own-authority -o jsonpath='{.status.conditionStatus.ready}' 2>/dev/null)" == "true" ]] || {
  echo -e "${RED}Error: validatingpolicy/agents-cannot-edit-their-own-authority is missing or not Ready.${NC}" >&2
  echo "This cluster is below stage 7, or Kyverno is not up. The gate runs the keystone beat and tests/e2e/test_keystone.py." >&2
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

# reviewer-ui. Same shape as the PDS row above -- the venv is required in the
# prerequisite loop, so reaching here means it exists, and the suite is
# hermetic and sub-second, so this costs the gate nothing.
# docs/telemetry-contract.md's per-service row publishes every figure and
# tests/unit/test_telemetry_contract_counts.py re-measures each of them, so
# no count is restated here.
echo -e "${YELLOW}reviewer-ui${NC}"
(cd "$ROOT/apps/reviewer-ui" && .venv/bin/python -m pytest tests -q)

# eval-scorer's own suite. Sub-second, and every test is either a pure
# function over a span list or a request to a handler on an ephemeral port --
# no cluster, no collector, no OpenSearch, no model. The count lives in
# docs/telemetry-contract.md's per-service row, which
# tests/unit/test_telemetry_contract_counts.py re-measures against the suite,
# so it is not restated here. The anti-vacuity half lives in
# tests/test_anti_vacuity.py: the same trajectory with one call moved scores
# 1.0 and 0.0, so a scorer that regressed to always-compliant reddens on
# every commit rather than at the next live demo.
echo -e "${YELLOW}eval-scorer${NC}"
(cd "$ROOT/platform/eval-scorer" && .venv/bin/python -m pytest tests -q)

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
echo -e "${BLUE}--- Stage 7: the two beat scripts, exactly as an operator runs them ---${NC}"
# Run as the operator runs them, not re-implemented: the only check that the
# beats' instructions work is running them. Each exits non-zero if its refusal
# does NOT fire (Beat R: the direct call to payments-mcp must be dropped;
# Beat K: RBAC must refuse, then Kyverno must refuse with the over-grant), and
# `set -e` makes that fail the gate. beat-keystone.sh removes its over-grant
# on every exit path. tests/e2e/test_keystone.py runs in Step 2 with the rest
# of tests/.
"$ROOT/scripts/beat-reach.sh"
"$ROOT/scripts/beat-keystone.sh"

echo ""
echo -e "${BLUE}--- Step 2/2: End-to-end trace assertions, browser hop included ---${NC}"
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
# A skip is invisible in a green gate. The stage-4 refusal above covers the
# OpenSearch Service this gate refuses to run without, but the pytest
# invocation itself carries nothing that fails on a skip, while several
# modules running inside it hold reachable `pytest.skip` calls on conditions
# that refusal does not cover (test_egress_policy.py,
# test_gateway_is_the_only_route.py, test_gateway_config_drift.py,
# test_supervised.py, test_settlement_llm_trace.py and the cluster-reachability
# skips in the OpenSearch/Prometheus modules). Each is defensible on its own;
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
RUN_BROWSER_TESTS=1 "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -v 2>&1 | tee "$SKIP_LOG"
PYTEST_STATUS=${PIPESTATUS[0]}
set -e
if [[ $PYTEST_STATUS -ne 0 ]]; then
  echo -e "${RED}The gate's test run failed (exit $PYTEST_STATUS).${NC}" >&2
  exit "$PYTEST_STATUS"
fi

# Counted off pytest's own summary line ("N skipped"), not by grepping for
# per-test verdict lines ("^path::test SKIPPED"). The per-test form misses a
# collection-time module skip -- which prints no `path::test` line at all,
# the largest possible instance of what this check exists for -- and any
# parametrised id containing a space, which this tree already has
# (test_no_stray_core_networkpolicy_shadows_the_cilium_one[kind: NetworkPolicy]).
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
  echo "stage-4 refusal above covers the cluster, so a skip means a condition" >&2
  echo "nobody accounted for. The skipped tests and their reasons:" >&2
  grep -E "^[^ ]+::[^ ]+ SKIPPED" "$SKIP_LOG" >&2 || true
  echo "" >&2
  echo "Fix the condition, or -- if the skip is genuinely correct -- say so in" >&2
  echo "docs/telemetry-contract.md's gate row and change this check deliberately." >&2
  exit 1
fi

echo ""
echo -e "${GREEN}Northstar M7 smoke test passed.${NC}"
