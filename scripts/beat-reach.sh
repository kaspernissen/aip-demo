#!/usr/bin/env bash
set -euo pipefail

# Beat R -- "the only way in". The Reach ring: a workload that has no business
# at the tool cannot get to it, and a workload that can get to a service cannot
# do what its identity does not allow there. Two enforcement points, one beat:
#
#   * EGRESS is core NetworkPolicy, enforced in the node by kindnet. A pod's
#     own Linkerd proxy accepts every connect locally, so the verdict is read
#     above TCP: the HTTP request fails (DROPPED), it is never a refused connect.
#   * INGRESS is Linkerd, at the destination's proxy, by workload identity. A
#     refusal is an empty HTTP 403 and the destination counts it in
#     inbound_http_authz_deny_total, which this script reads around the call
#     (through the API server's pod proxy, never a port-forward: a port-forward
#     bypasses the proxy) to name the Server and route that refused it.
#
# Three requests:
#
#   (a) the CONTROL, from claims-agent: settlement-agent:9002, the one peer it
#       exists to call. It must answer. Without it, (b) would also "pass" on a
#       cluster whose networking is simply broken.
#   (b) EGRESS, from claims-agent: a direct MCP `initialize` at
#       payments-mcp:8000/mcp, around the gateway. It must be DROPPED: the node
#       drops the packets its proxy sends, the proxy answers 504 with
#       `l5d-proxy-error`. Anything that answers -- a 401, a 403 -- means the
#       packets got through.
#   (c) INGRESS, from reviewer-ui: POST /payments/<id>/execute straight at
#       payments-api, skipping the gateway and the PDS. reviewer-ui may reach
#       payments-api (it lists and rejects there) but its identity is not the
#       one authorized on the execute route: REFUSED 403, empty body, and the
#       payments-api proxy's deny counter rises by one.
#
# Exits non-zero if the control fails, (b) is not dropped, or (c) is not
# refused with a counted 403, so a rehearsal that "worked" cannot hide a ring
# that is open. No new image tooling: the client is python3's standard library,
# already in the pods, which is what the e2e tests' prober uses too.
#
# Refusals are COUNTED, not traced: they are in no span. This beat shows the
# refusal and the counter on the terminal.

CONTEXT="kind-northstar"
NAMESPACE="northstar-agents"
CONTROL_URL="http://settlement-agent.northstar-agents.svc.cluster.local:9002/.well-known/agent-card.json"
DIRECT_URL="http://payments-mcp.northstar-tools.svc.cluster.local:8000/mcp"
INGRESS_URL="http://payments-api.northstar-systems.svc.cluster.local:8080/payments/PAY-000000/execute"
# A dropped request ends as a timeout, a reset, or the proxy's own 504. Long
# enough that a slow-but-allowed request is not mistaken for a drop, short
# enough to stay on stage.
CONNECT_TIMEOUT=8

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Every command carries a hard timeout so a stuck exec cannot hang a demo.
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT=(timeout)
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT=(gtimeout)
else
  echo -e "${RED}Error: needs \`timeout\` (brew install coreutils).${NC}" >&2
  exit 1
fi

kc() { "${TIMEOUT[@]}" 60 kubectl --context "$CONTEXT" "$@"; }

# Runs inside a pod; the source is scripts/reach-probe.py, shared with
# `scripts/demo guard`. It prints exactly one line:
#   REACHED <http status>   -- something answered
#   REFUSED <http status>   -- Linkerd refused it (empty 403, or a proxy 404)
#   DROPPED <how>           -- nothing answered (timeout, reset, proxy-error)
PROBE="$(cat "$(dirname "${BASH_SOURCE[0]}")/reach-probe.py")"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

step() { echo; echo -e "${BLUE}$*${NC}"; }

pod_of() { kc -n "$1" get pod -l "app=$2" -o jsonpath='{.items[0].metadata.name}'; }

POD="$(pod_of "$NAMESPACE" claims-agent)"
[[ -n "$POD" ]] || { echo -e "${RED}Error: no claims-agent pod in ${NAMESPACE}.${NC}" >&2; exit 1; }
RPOD="$(pod_of "$NAMESPACE" reviewer-ui)"
[[ -n "$RPOD" ]] || { echo -e "${RED}Error: no reviewer-ui pod in ${NAMESPACE}.${NC}" >&2; exit 1; }
APIPOD="$(pod_of northstar-systems payments-api)"
[[ -n "$APIPOD" ]] || { echo -e "${RED}Error: no payments-api pod in northstar-systems.${NC}" >&2; exit 1; }

probe() { # <pod> <container> <url> <method>
  kc -n "$NAMESPACE" exec "$1" -c "$2" -- \
    python3 -c "$PROBE" "$3" "$4" "$CONNECT_TIMEOUT" | tail -1
}

reach_cli() { (cd "$ROOT" && python3 -m tests.harness.reach "$@"); }

echo -e "${BLUE}=== Beat R: the only way in ===${NC}"
echo "Egress is NetworkPolicy in the node; ingress is Linkerd, by workload identity."

step "1. Control: claims-agent calls settlement-agent, the one peer it exists to call"
echo "\$ GET ${CONTROL_URL}"
CONTROL="$(probe "$POD" claims-agent "$CONTROL_URL" GET)"
echo "  -> ${CONTROL}"
if [[ "$CONTROL" != REACHED\ * ]]; then
  echo -e "${RED}FAIL: the control did not answer, so the next steps would prove an outage rather than a policy.${NC}" >&2
  exit 1
fi
echo -e "${GREEN}  REACHED. reachable, as it should be.${NC}"

step "2. Egress: claims-agent sends an MCP initialize straight to payments-mcp, around the gateway"
echo "\$ POST ${DIRECT_URL}   (initialize, ${CONNECT_TIMEOUT}s timeout)"
DIRECT="$(probe "$POD" claims-agent "$DIRECT_URL" POST)"
if [[ "$DIRECT" != DROPPED\ * ]]; then
  echo "  -> ${DIRECT}"
  echo -e "${RED}FAIL: claims-agent was not dropped on the way to payments-mcp (${DIRECT}). Either it got through, or something answered. The gateway is not provably the only way in.${NC}" >&2
  exit 1
fi
HOW="${DIRECT#DROPPED }"
case "$HOW" in
  proxy-error) HOW_TEXT="DROPPED (proxy error: the node dropped the packets, this pod's proxy answered 504)" ;;
  reset)       HOW_TEXT="DROPPED (TLS reset)" ;;
  *)           HOW_TEXT="DROPPED (${HOW})" ;;
esac
echo "  -> ${HOW_TEXT}"
echo -e "${GREEN}  DROPPED. Nothing answered: not a 401, not a 403. The packets never arrived.${NC}"

step "3. Ingress: reviewer-ui calls payments-api's execute route directly, skipping the gateway and the PDS"
echo "\$ POST ${INGRESS_URL}"
BEFORE="$(mktemp)"; AFTER="$(mktemp)"
trap 'rm -f "$BEFORE" "$AFTER"' EXIT
reach_cli snapshot "$CONTEXT" northstar-systems "$APIPOD" > "$BEFORE"
REFUSAL="$(probe "$RPOD" reviewer-ui "$INGRESS_URL" POST)"
reach_cli snapshot "$CONTEXT" northstar-systems "$APIPOD" > "$AFTER"
if [[ "$REFUSAL" != "REFUSED 403" ]]; then
  echo "  -> ${REFUSAL}"
  echo -e "${RED}FAIL: reviewer-ui's direct execute was not refused 403 (${REFUSAL}). payments-api's ingress policy is not holding.${NC}" >&2
  exit 1
fi
if ! LINE="$(reach_cli refusal 403 "$BEFORE" "$AFTER" payments-api)"; then
  echo "  -> ${LINE}"
  echo -e "${RED}FAIL: the 403 is not attributable to Linkerd: payments-api's inbound_http_authz_deny_total did not rise.${NC}" >&2
  exit 1
fi
echo "  -> ${LINE}"
echo -e "${GREEN}  REFUSED. By the proxy, by identity, and counted.${NC}"

echo
echo -e "${GREEN}Beat R holds: the gateway is the only route to payments-mcp, and only payments-mcp's identity may execute at payments-api.${NC}"
echo "(Refusals are counted at the destination proxy, not traced.)"
