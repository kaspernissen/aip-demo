#!/usr/bin/env bash
set -euo pipefail

# M4 Task 1 (plan amendment A2). Owns every `kubectl port-forward` this demo
# needs, so an operator has exactly one command to run when a forward dies --
# which, per rulings R10 and R20, is this project's single most-repeated
# operational failure. Every prior milestone re-established these forwards by
# hand; one task even measured against dead ones without noticing (R20: a
# dead `kubectl port-forward` keeps its listening socket bound, so `lsof`
# alone reports it healthy).
#
# Modelled on the human partner's own
# /Users/kaspernissen/dash0/demos/otel-platform-demo/01_start-demo.sh (a
# `PIDS` array, background `kubectl port-forward` per surface, `cleanup` on
# `trap ... EXIT INT TERM`), with two deliberate departures from it, both
# required here:
#
#   1. NO `pkill -f "kubectl port-forward"` fallback. The reference's cleanup
#      has one; it kills every port-forward on the machine, including the
#      human partner's own in another terminal, not just the ones this script
#      started. The tracked PIDS array below is the whole cleanup.
#   2. Liveness is a real HTTP request, never process existence. A live PID
#      proves nothing (R20). This reuses the exact two-question shape
#      scripts/smoke-test.sh already implements -- lsof, then curl -- rather
#      than inventing a second one.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

command_exists() {
  command -v "$1" >/dev/null 2>&1
}
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
command_exists curl || { echo -e "${RED}curl is required (the liveness check uses it)${NC}" >&2; exit 1; }
command_exists lsof || { echo -e "${RED}lsof is required (the liveness check uses it)${NC}" >&2; exit 1; }

# Departure 1 from the reference script: PIDS is the ENTIRE cleanup. Nothing
# here ever kills a process it did not start.
PIDS=()
CLEANED_UP=""

cleanup() {
  # Guards against the trap firing twice for one Ctrl-C: INT/TERM runs this,
  # then the shell's own exit runs the EXIT trap a second time. Idempotent
  # either way (kill -0 on an already-dead PID just skips it), but the guard
  # keeps the "Stopping..." banner from printing twice.
  [[ -n "$CLEANED_UP" ]] && return
  CLEANED_UP=1
  echo ""
  echo -e "${BLUE}Stopping the port-forwards this script started...${NC}"
  for pid in "${PIDS[@]:-}"; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

# Departure 2: a live PID proves nothing (R20) -- the target pod behind a
# `kubectl port-forward` can be replaced while the forward's own socket stays
# bound and `lsof` keeps calling it healthy. Every surface below is verified
# with the SAME two questions scripts/smoke-test.sh's prerequisite loop asks:
# is a socket bound, and does an HTTP request actually get an answer through
# it. Any HTTP response counts (this is a liveness probe, not a contract
# check) -- payments-mcp's `/mcp` answering 400 to a bare GET is exactly as
# good a proof as a 200 elsewhere.

# For a forward THIS script started: also confirm the PID we launched is the
# one holding the port, not merely that something is (A9: a stray forward
# from another cluster on this machine has silently shadowed a host port
# before).
verify_owned_forward() {
  local port="$1" pid="$2" name="$3" path="$4"
  # `grep -x ... >/dev/null` and NOT `grep -qx` (review suspicion S1, and the
  # identical construct in scripts/setup-kind.sh is fixed the same way). Under
  # `set -o pipefail`, `grep -q` exits the moment it matches, which can SIGPIPE
  # the `awk` still writing to it; awk then exits 141 and pipefail reports 141
  # for the whole pipeline even though grep matched. That turns a healthy
  # forward into "never bound" and exits 1 -- whose EXIT trap then kills the
  # forwards this script had already established. Dropping -q makes grep drain
  # its input, so nothing upstream can be signalled. It is a race rather than a
  # certainty (lsof's output is small enough that the write usually completes
  # first), which is exactly why it is worth removing rather than reasoning
  # about.
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -x "${pid}" >/dev/null; then
    echo -e "${RED}Error: the port-forward for ${name} (expected PID ${pid} listening on ${port}) never bound.${NC}" >&2
    echo "Another process may already hold that port -- check: lsof -nP -iTCP:${port} -sTCP:LISTEN" >&2
    exit 1
  fi
  if ! curl -s -o /dev/null -m 5 "http://localhost:${port}${path}"; then
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request.${NC}" >&2
    echo "This is a dead kubectl port-forward: its target pod was replaced, the socket stayed bound, and lsof still calls it healthy (ruling R20)." >&2
    exit 1
  fi
}

# For a surface this script does NOT forward (reviewer-ui: NodePort, mapped
# straight to a kind host port by deploy/kind/stage3.yaml/stage4 reusing it) --
# same two questions, without the PID-ownership check, since there is no PID
# of this script's to check against.
verify_existing_surface() {
  local port="$1" name="$2" path="$3"
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo -e "${RED}Error: nothing is listening on port ${port} (${name}).${NC}" >&2
    exit 1
  fi
  if ! curl -s -o /dev/null -m 5 "http://localhost:${port}${path}"; then
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request (ruling R20).${NC}" >&2
    exit 1
  fi
}

# Review finding F8: this script is now the universal remedy smoke-test.sh
# sends an operator to, and it was unconditional -- it forwarded
# svc/opensearch-dashboards on every cluster. On a stage-1/2/3 cluster that
# Service does not exist, the forward never binds, verify_owned_forward exits
# 1, and the EXIT trap then kills the four good forwards it had just created:
# the remedy for a dead port-forward would leave the operator with no
# port-forwards at all.
#
# So the two stage-gated surfaces are asked for rather than assumed. The four
# stage-1 surfaces below are NOT optional -- a cluster without them is broken,
# not merely younger -- and nothing here is silently skipped: each skip prints
# the stage that would provide it.
service_exists() {
  kubectl -n "$1" get "svc/$2" -o name >/dev/null 2>&1
}

DASHBOARDS_AVAILABLE=""
if service_exists observability opensearch-dashboards; then
  DASHBOARDS_AVAILABLE=1
fi
REVIEWER_UI_AVAILABLE=""
if service_exists northstar-agents reviewer-ui; then
  REVIEWER_UI_AVAILABLE=1
fi

echo -e "${BLUE}=== Starting Northstar port-forwards ===${NC}"

echo "  - claims-agent       (19001 -> 9001)"
kubectl -n northstar-agents port-forward svc/claims-agent 19001:9001 >/dev/null 2>&1 &
CLAIMS_PID=$!
PIDS+=("$CLAIMS_PID")

echo "  - settlement-agent   (19002 -> 9002)"
kubectl -n northstar-agents port-forward svc/settlement-agent 19002:9002 >/dev/null 2>&1 &
SETTLEMENT_PID=$!
PIDS+=("$SETTLEMENT_PID")

echo "  - payments-mcp       (18000 -> 8000)"
kubectl -n northstar-tools port-forward svc/payments-mcp 18000:8000 >/dev/null 2>&1 &
MCP_PID=$!
PIDS+=("$MCP_PID")

echo "  - payments-api       (18080 -> 8080)"
kubectl -n northstar-systems port-forward svc/payments-api 18080:8080 >/dev/null 2>&1 &
API_PID=$!
PIDS+=("$API_PID")

# M4 Task 1, plan amendment A1: Dashboards has no kind host port --
# extraPortMappings is create-time only and the human partner declined the
# cluster recreate that would have added a fifth one -- so this is the ONLY
# way to reach it. Stage 4 installs it; below stage 4 it does not exist, and
# forwarding it anyway is finding F8.
DASHBOARDS_PID=""
if [[ -n "$DASHBOARDS_AVAILABLE" ]]; then
  echo "  - OpenSearch Dashboards (5601 -> 5601)"
  kubectl -n observability port-forward svc/opensearch-dashboards 5601:5601 >/dev/null 2>&1 &
  DASHBOARDS_PID=$!
  PIDS+=("$DASHBOARDS_PID")
else
  echo -e "${YELLOW}  - OpenSearch Dashboards: SKIPPED, svc/opensearch-dashboards does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 4)${NC}"
fi

# --- Task 2's Prometheus goes here -------------------------------------
# Clearly-marked place for the next M4 task: add its `kubectl port-forward`,
# its PID variable, and its `verify_owned_forward` call below, the same shape
# as the five above. Prometheus is stage 4 only, like Dashboards, so copy
# Dashboards' `service_exists` guard rather than the four unconditional
# forwards -- an unconditional forward for a Service a lower stage does not
# have is review finding F8.
# ------------------------------------------------------------------------

# Give the forwards a moment to establish before checking them (same
# allowance setup-kind.sh's own port-forward block uses).
sleep 4

echo ""
echo -e "${BLUE}Verifying every surface is actually live (not just listening)...${NC}"
verify_owned_forward 19001 "${CLAIMS_PID}" claims-agent "/.well-known/agent-card.json"
verify_owned_forward 19002 "${SETTLEMENT_PID}" settlement-agent "/.well-known/agent-card.json"
verify_owned_forward 18000 "${MCP_PID}" payments-mcp "/mcp"
verify_owned_forward 18080 "${API_PID}" payments-api "/healthz"
if [[ -n "$DASHBOARDS_PID" ]]; then
  verify_owned_forward 5601 "${DASHBOARDS_PID}" opensearch-dashboards "/"
fi
# reviewer-ui: covered here for the same reason smoke-test.sh covers it --
# it is normally already live via deploy/kind/stage3.yaml's NodePort mapping,
# not a forward this script starts, but an operator running this script
# wants to know ALL the surfaces smoke-test.sh will check are actually up,
# not just the ones this script itself launched. Stage 3 installs it; on a
# stage-1/2 cluster there is nothing to verify (finding F8 again).
if [[ -n "$REVIEWER_UI_AVAILABLE" ]]; then
  verify_existing_surface 8081 reviewer-ui "/healthz"
else
  # `--stage 4`, not `--stage 3`, even though reviewer-ui first appears at
  # stage 3: stage 4 is the current stage and a strict superset of stage 3
  # (the same cluster config, plus OpenSearch), and sending an operator to
  # stage 3 lands them on a cluster where every one of
  # tests/e2e/test_opensearch_trace_ingest.py's guards skips.
  #
  # Re-review finding N1: this line said `--stage 3`, which was the FOURTH
  # recurrence of the class ruling M4-R7 exists for -- authored inside the fix
  # for the third, in the one file
  # tests/unit/test_stage2_cilium_config.py's guard did not open. Its file
  # list is now a `scripts/*.sh` glob rather than four hand-listed paths, so
  # this file is covered and so is the next script anyone adds.
  echo -e "${YELLOW}  - reviewer-ui: SKIPPED, svc/reviewer-ui does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 4)${NC}"
fi

echo ""
if [[ -n "$DASHBOARDS_AVAILABLE" && -n "$REVIEWER_UI_AVAILABLE" ]]; then
  echo -e "${GREEN}All surfaces verified up.${NC}"
else
  echo -e "${GREEN}Every surface this cluster has is verified up.${NC}"
  echo -e "${YELLOW}Some stage-gated surfaces are absent -- see the SKIPPED lines above.${NC}"
fi
echo ""
echo "  claims-agent (A2A):        http://localhost:19001"
echo "  settlement-agent (A2A):    http://localhost:19002"
echo "  payments-mcp:              http://localhost:18000/mcp"
echo "  payments-api:              http://localhost:18080"
# Written as `if` blocks rather than `[[ ... ]] && echo ...`: under `set -e` a
# bare `test && cmd` list whose test fails is the classic way a script exits on
# a condition simply being false.
if [[ -n "$REVIEWER_UI_AVAILABLE" ]]; then
  echo "  reviewer-ui:               http://localhost:8081"
fi
if [[ -n "$DASHBOARDS_AVAILABLE" ]]; then
  echo "  OpenSearch Dashboards:     http://localhost:5601"
fi
echo ""
echo "Press Ctrl+C to stop the port-forwards this script started."

# Block so the trap has something to catch and the background forwards stay
# alive for as long as this script runs -- the same shape the reference
# script uses (there, blocking on `yarn start`; here, there is no foreground
# process of our own to hand off to, so wait on the forwards directly).
wait
