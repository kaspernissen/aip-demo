#!/usr/bin/env bash
set -euo pipefail

# Beat R -- "the only way in". The Reach ring: a workload that holds no SVID and
# has no business at the tool cannot even open a connection to it.
#
# From inside claims-agent -- the same prober, for the same reason, as
# tests/e2e/test_gateway_is_the_only_route.py (a real neighbouring workload in
# the agents' own namespace, no SVID, no MCP client; if the policy were scoped
# by namespace instead of by workload this probe would wrongly succeed) --
# this script makes two requests:
#
#   (a) the CONTROL: settlement-agent:9002, the one peer claims-agent exists to
#       call. It must answer. Without it, step (b) would also "pass" on a
#       cluster whose networking is simply broken.
#   (b) a direct MCP `initialize` at payments-mcp:8000/mcp, around the gateway.
#       It must be DROPPED -- a connect timeout, not a refusal and not an HTTP
#       status: Cilium drops the packet, it does not reject it, so there is no
#       RST to turn into an error. A 401/403 here would mean something at the
#       application layer answered, i.e. the dataplane let it through.
#
# Exits non-zero if the control fails or the direct request is NOT dropped, so
# a rehearsal that "worked" cannot hide a ring that is open. No new image
# tooling: the client is python3's standard library, already in the pod, which
# is what the e2e test's prober uses too.
#
# Cilium drops are NOT in the trace (Hubble is not installed); this beat shows
# the refusal on the terminal, and says so on the slide.

CONTEXT="kind-northstar"
NAMESPACE="northstar-agents"
CONTROL_URL="http://settlement-agent.northstar-agents.svc.cluster.local:9002/.well-known/agent-card.json"
DIRECT_URL="http://payments-mcp.northstar-tools.svc.cluster.local:8000/mcp"
# A Cilium drop presents as a connect timeout. Long enough that a slow-but-
# allowed connection is not mistaken for a drop, short enough to stay on stage.
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

# Runs inside the claims-agent pod. Prints exactly one line:
#   REACHED <http status>        -- something answered
#   DROPPED timeout              -- nothing answered: the packet was dropped
#   REFUSED <exception name>     -- something actively refused (an RST, a reset)
PROBE='
import json, sys, urllib.request, urllib.error, socket
url, method, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
body = None
headers = {}
if method == "POST":
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "beat-reach", "version": "1"}}}).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
req = urllib.request.Request(url, data=body, headers=headers, method=method)
try:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        print("REACHED", r.status)
except urllib.error.HTTPError as e:
    print("REACHED", e.code)
except urllib.error.URLError as e:
    # urllib wraps a connect timeout in URLError; the wrapped reason is what
    # tells a Cilium drop (timeout) from a refusal (ConnectionRefusedError).
    reason = e.reason
    if isinstance(reason, (socket.timeout, TimeoutError)):
        print("DROPPED timeout")
    else:
        print("REFUSED", type(reason).__name__)
except (socket.timeout, TimeoutError):
    print("DROPPED timeout")
except Exception as e:
    print("REFUSED", type(e).__name__)
'

step() { echo; echo -e "${BLUE}$*${NC}"; }

POD="$(kc -n "$NAMESPACE" get pod -l app=claims-agent -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$POD" ]] || { echo -e "${RED}Error: no claims-agent pod in ${NAMESPACE}.${NC}" >&2; exit 1; }

probe() {
  kc -n "$NAMESPACE" exec "$POD" -c claims-agent -- \
    python3 -c "$PROBE" "$1" "$2" "$CONNECT_TIMEOUT" | tail -1
}

echo -e "${BLUE}=== Beat R: the only way in ===${NC}"
echo "Probing from inside ${POD} (claims-agent: no SVID, no MCP client, no business at the tool)."

step "1. Control: claims-agent calls settlement-agent, the one peer it exists to call"
echo "\$ GET ${CONTROL_URL}"
CONTROL="$(probe "$CONTROL_URL" GET)"
echo "  -> ${CONTROL}"
if [[ "$CONTROL" != REACHED\ * ]]; then
  echo -e "${RED}FAIL: the control did not answer, so the next step would prove an outage rather than a policy.${NC}" >&2
  exit 1
fi
echo -e "${GREEN}  reachable, as it should be.${NC}"

step "2. Around the gateway: claims-agent sends an MCP initialize straight to payments-mcp"
echo "\$ POST ${DIRECT_URL}   (initialize, ${CONNECT_TIMEOUT}s connect timeout)"
DIRECT="$(probe "$DIRECT_URL" POST)"
echo "  -> ${DIRECT}"
if [[ "$DIRECT" != DROPPED\ * ]]; then
  echo -e "${RED}FAIL: claims-agent was not dropped on the way to payments-mcp (${DIRECT}). Either it got through, or something refused it instead of the dataplane dropping it. The gateway is not provably the only way in.${NC}" >&2
  exit 1
fi
echo -e "${GREEN}  DROPPED. Nothing answered: not a 401, not a refusal. The packet never arrived.${NC}"

echo
echo -e "${GREEN}Beat R holds: the gateway is the only route to payments-mcp.${NC}"
echo "(Cilium drops are not in the trace; Hubble is not installed.)"
