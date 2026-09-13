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
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -qx "${pid}"; then
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

echo "  - OpenSearch Dashboards (5601 -> 5601)"
# M4 Task 1, plan amendment A1: Dashboards has no kind host port --
# extraPortMappings is create-time only and the human partner declined the
# cluster recreate that would have added a fifth one -- so this is the ONLY
# way to reach it.
kubectl -n observability port-forward svc/opensearch-dashboards 5601:5601 >/dev/null 2>&1 &
DASHBOARDS_PID=$!
PIDS+=("$DASHBOARDS_PID")

# --- Task 2's Prometheus goes here -------------------------------------
# Clearly-marked place for the next M4 task: add its `kubectl port-forward`,
# its PID variable, and its `verify_owned_forward` call below, the same shape
# as the five above.
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
verify_owned_forward 5601 "${DASHBOARDS_PID}" opensearch-dashboards "/"
# reviewer-ui: covered here for the same reason smoke-test.sh covers it --
# it is normally already live via deploy/kind/stage3.yaml's NodePort mapping,
# not a forward this script starts, but an operator running this script
# wants to know ALL five surfaces smoke-test.sh will check are actually up,
# not just the four this script itself launched.
verify_existing_surface 8081 reviewer-ui "/healthz"

echo ""
echo -e "${GREEN}All surfaces verified up.${NC}"
echo ""
echo "  claims-agent (A2A):        http://localhost:19001"
echo "  settlement-agent (A2A):    http://localhost:19002"
echo "  payments-mcp:              http://localhost:18000/mcp"
echo "  payments-api:              http://localhost:18080"
echo "  reviewer-ui:               http://localhost:8081"
echo "  OpenSearch Dashboards:     http://localhost:5601"
echo ""
echo "Press Ctrl+C to stop the port-forwards this script started."

# Block so the trap has something to catch and the background forwards stay
# alive for as long as this script runs -- the same shape the reference
# script uses (there, blocking on `yarn start`; here, there is no foreground
# process of our own to hand off to, so wait on the forwards directly).
wait
