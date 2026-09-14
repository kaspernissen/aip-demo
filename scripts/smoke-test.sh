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

# M4 Task 5: renamed from "M3", which is the THIRD time this pair has gone
# stale -- 8c0e980 took it from M2.2 to M3, M3 Task 5B repeated the rename, and
# it then sat at M3 through the whole of M4 while this gate grew four new e2e
# files of M4 assertions (OpenSearch trace ingest, Prometheus over OTLP, the
# evidence view, the policy-adherence scores). A banner and a sign-off naming
# an older milestone misreport what a passing run proved, which is the reason
# 8c0e980 gives for the first rename.
#
# The M3 comment here ended "No test pins either string". That is now false and
# deliberately so: tests/unit/test_stage4_config.py::
# test_the_gates_banner_names_the_milestone_it_actually_proves DERIVES the
# expected milestone from the newest design spec in docs/superpowers/specs/ and
# checks both echoes against it. The next milestone does not have to remember
# to come here -- the guard reddens when the spec lands. The sign-off also
# gained the word "Northstar" so the two strings match one pattern instead of
# two.
echo -e "${BLUE}=== Northstar M4 smoke test ===${NC}"

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
# aborts the settlement-agent suite at collection (0 of 114 tests run -- 79
# when this comment was written) and
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
# prerequisite. Measured, not assumed: the helper's venv assertion is
# `_pytest_collected_count` in tests/unit/test_telemetry_contract_counts.py and
# the reviewer-ui row is its `_PER_SERVICE_SUITES` entry. (Those were cited by
# line number until M4 Task 4 and both numbers had already drifted — the row had
# moved from :296 to :312 — so they are cited by name now, which is the one form
# that survives an edit to the file above it.) Its own suite is run below
# alongside the rest.
#
# No counts in this comment. It said "its own 50-test suite ... alongside the
# other six", then M4 Task 4 updated the 50 to 56 and left "the other six"
# in the same sentence while adding the seventh other suite 236 lines below.
# docs/telemetry-contract.md's per-service row is the one place the figures
# live and tests/unit/test_telemetry_contract_counts.py re-measures it.
#
# `platform/eval-scorer` joins the loop in M4 Task 4, the task that creates it,
# rather than a milestone later. The two services before it were both added
# late, both for the same stated reason, and both times the gap meant a suite
# that nothing ran: the same `_pytest_collected_count` helper re-measures its
# published per-service row, so without this line the gate would fail inside a
# unit test about a contract count rather than here, about a missing
# prerequisite.
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui platform/eval-scorer; do
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
  #
  # M4 Task 1 fix round 1: it recurred, and the guard caught it. This line
  # said `--stage 3` after stage 4 became real; raising the guard's
  # CURRENT_STAGE to 4 (ruling M4-R7) is what turned it red. Stage 4 is a
  # superset of stage 3 -- same cluster config, plus OpenSearch -- so an
  # operator following this still gets the reviewer-ui the port loop below
  # requires, and also gets the OpenSearch the M4 assertions need.
  echo "Run ./scripts/setup-kind.sh --stage 4 first." >&2
  exit 1
}
# RULING M4-R9 (re-review finding N3): the gate must PROVE it is running on a
# stage-4 cluster rather than be told so by a README.
#
# tests/e2e/test_opensearch_trace_ingest.py is M4 Task 1's whole deliverable --
# it is what catches a span silently failing to reach OpenSearch, which is the
# defect that shipped at 955dd18 and that 119 green unit tests did not see. It
# SKIPS on a coherently stage-3 cluster, deliberately: OpenSearch is a stage-4
# component, and a test that failed for its absence would cry wolf on every
# lower stage.
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
  echo "This cluster is below stage 4. The gate runs tests/e2e/test_opensearch_trace_ingest.py -- M4's trace-ingest guard -- and on a cluster without OpenSearch every one of its assertions SKIPs, so the gate would pass while proving nothing about them (ruling M4-R9)." >&2
  echo "Bring the cluster to stage 4. No recreate is needed: stage 4 runs on stage 3's cluster config unchanged." >&2
  echo "Run ./scripts/setup-kind.sh --stage 4 first." >&2
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
    # M4 Task 1 (plan amendment A2): this used to hand the operator a
    # `kubectl port-forward` line to assemble by hand, per-port -- exactly
    # the ad-hoc pattern rulings R10 and R20 came from. The script the echo
    # below now names owns every port-forward this demo needs (and
    # re-verifies each one with this same lsof-then-curl shape), so the
    # remedy is one command, not a line an operator has to get right five
    # times. This file still creates nothing itself -- that division
    # predates this milestone and is unchanged; only the remedy text below
    # points somewhere new.
    #
    # This comment used to claim that leaving the script's name out of it was
    # what kept the guard honest -- "otherwise reverting only the echo below
    # would still leave that guard green". Review finding F7: that was false
    # as written. The remedy at the end of the `curl` branch below names the
    # script too, so reverting *this* echo alone always left the old
    # substring guard green regardless of what this comment said. The
    # precaution is still worth keeping (a comment must not be able to satisfy
    # a guard), but the guard is what was actually fixed:
    # tests/unit/test_stage4_config.py now strips whole-line comments and
    # requires BOTH remedies to name the script, so reverting either one goes
    # red on its own.
    echo "Port-forwards die after a redeploy and need re-establishing: ./scripts/start-demo.sh (or re-run ./scripts/setup-kind.sh --stage 4 first if the cluster itself is gone)." >&2
    exit 1
  }
  # -o /dev/null: the body is irrelevant, only whether one arrived.
  # -m 5: a live forward answers these paths in milliseconds; five seconds is
  # far past that and still a second per port in the worst case.
  curl -s -o /dev/null -m 5 "http://localhost:${port}${path}" || {
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request.${NC}" >&2
    echo "This is a dead kubectl port-forward: its target pod was replaced, the socket stayed bound, and lsof still calls it healthy (ruling R20). Nothing here is broken in the cluster." >&2
    echo "Re-establish every forward, verified live rather than merely bound: ./scripts/start-demo.sh" >&2
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

# M3 Task 5B: reviewer-ui, which was the last per-service suite still outside
# the gate when it was added. Same shape as the PDS row above -- the venv is
# required in the prerequisite loop, so reaching here means it exists, and the
# suite is hermetic and sub-second, so this costs the gate nothing.
# docs/telemetry-contract.md's per-service row publishes every figure and
# tests/unit/test_telemetry_contract_counts.py re-measures each of them, which
# means this suite's count was gating before its assertions were.
#
# (Counts removed in M4 Task 4's fix round: "50 tests, 0.36s" was stale at 56
# from M3, and "all seven figures" was falsified by the eighth row added eight
# lines below this comment, in the same commit that left this one alone.)
echo -e "${YELLOW}reviewer-ui${NC}"
(cd "$ROOT/apps/reviewer-ui" && .venv/bin/python -m pytest tests -q)

# M4 Task 4: the eighth per-service suite, added in the same task as the
# service. Sub-second, and every test is either a pure function over a span
# list or a request to a handler on an ephemeral port -- no cluster, no
# collector, no OpenSearch, no model. (No count in this comment. The figure
# lives in docs/telemetry-contract.md's per-service row, which
# tests/unit/test_telemetry_contract_counts.py re-measures against the suite;
# a second copy in a shell comment is only a way to be wrong somewhere nothing
# checks, and this one said 106 the day the suite reached 128.) The
# anti-vacuity half of M4 spec §7.4 lives here (tests/test_anti_vacuity.py):
# the same trajectory with one call moved scores 1.0 and 0.0, so a scorer that
# regressed to always-compliant reddens on every commit rather than at the next
# live demo.
echo -e "${YELLOW}eval-scorer${NC}"
(cd "$ROOT/platform/eval-scorer" && .venv/bin/python -m pytest tests -q)

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
#
# M4 FINAL REVIEW, finding L2: a skip is invisible in a green gate, and until
# now this gate acted on that for exactly one object -- the OpenSearch Service
# it refuses to run without (ruling M4-R9). The pytest invocation itself
# carried nothing that fails on a skip, while at least six modules running
# inside it hold reachable `pytest.skip` calls on conditions the stage-4 check
# does not cover (test_egress_policy.py, test_gateway_is_the_only_route.py,
# test_gateway_config_drift.py, test_supervised.py, test_settlement_llm_trace.py
# and the four M4 modules' cluster-reachability skips). Each is defensible on
# its own; collectively, nothing stopped a future green gate that had silently
# skipped a milestone's assertions. "Zero skips" was a property of one 2026-09-14
# run, verified by grep after the fact. It is a property of the gate now.
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

# Counted off the summary line rather than by grepping for "SKIPPED", which
# also matches the word inside a test name or a failure message.
SKIPPED=$(grep -cE "^[^ ]+::[^ ]+ SKIPPED" "$SKIP_LOG" || true)
if [[ "$SKIPPED" -ne 0 ]]; then
  echo "" >&2
  echo -e "${RED}The gate passed with ${SKIPPED} skipped test(s), which is a failure.${NC}" >&2
  echo "A skip inside this gate is invisible in a green run: the milestone's" >&2
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
echo -e "${GREEN}Northstar M4 smoke test passed.${NC}"
