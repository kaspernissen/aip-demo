#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="northstar"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"
STAGE=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<'EOF'
Usage: setup-kind.sh [--stage N]

  --stage 1   Connected:            agents, tools, systems, customer UI, collector, Jaeger
  --stage 2   Governed:             + agentgateway, SPIRE, Kyverno, Cilium, cert-manager, PDS
  --stage 3   Supervised:           + Dapr, Keycloak, reviewer UI, Postgres
  --stage 4   Observable:           + OpenSearch, Prometheus
  --stage 5   Progressive autonomy: + flagd
  --stage 6   Golden path:          + Crossplane, Argo CD, Backstage

Each stage installs only what that stage of the demo needs, so the laptop
runs exactly one stage at a time.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if ! [[ "$STAGE" =~ ^[0-9]+$ ]]; then
  echo "Error: --stage must be a positive integer, got '${STAGE}'" >&2
  usage
  exit 1
fi

if [[ "$STAGE" -gt 1 ]]; then
  echo "Stage $STAGE is not implemented yet. M1.1 implements stage 1 only." >&2
  exit 1
fi

echo -e "${BLUE}--- Loading environment ---${NC}"
if [ -f "${ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
else
  echo -e "${RED}Error: .env not found. Copy .env.template to .env and fill it in.${NC}" >&2
  exit 1
fi

echo -e "${BLUE}--- Checking prerequisites ---${NC}"
command_exists docker || { echo -e "${RED}docker is required${NC}" >&2; exit 1; }
command_exists kind || { echo -e "${RED}kind is required: brew install kind${NC}" >&2; exit 1; }
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
: "${OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE:?OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE not set in .env}"

echo -e "${BLUE}Step 1/9: Creating local registry '${REGISTRY_NAME}'...${NC}"
# N12 (final-review.md): a container can exist but be stopped (e.g. after a
# `docker system prune` or a host restart with restart policies not yet
# applied) without `docker inspect -f '{{.State.Running}}'` ever being
# 'true'. The old two-branch check ran `docker run --name kind-registry`
# whenever it wasn't running, which fails on the name collision with an
# existing-but-stopped container and kills this script under `set -e` — a
# false "the registry is broken" failure when `docker start` would have
# fixed it in one step. Distinguish "does not exist" from "exists but
# stopped" and only ever `docker run` in the former case.
if ! docker inspect "${REGISTRY_NAME}" >/dev/null 2>&1; then
  docker run \
    -d --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" --network bridge --name "${REGISTRY_NAME}" \
    registry:2
  echo -e "${GREEN}Local registry started on port ${REGISTRY_PORT}${NC}"
elif [ "$(docker inspect -f '{{.State.Running}}' "${REGISTRY_NAME}")" != 'true' ]; then
  docker start "${REGISTRY_NAME}"
  echo -e "${GREEN}Local registry existed but was stopped; started it.${NC}"
else
  echo -e "${GREEN}Local registry already running${NC}"
fi

echo -e "${BLUE}Step 2/9: Creating kind cluster '${CLUSTER_NAME}'...${NC}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo -e "${YELLOW}Cluster already exists, skipping creation.${NC}"
else
  kind create cluster --config "$ROOT/deploy/kind/stage1.yaml"
fi
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

echo -e "${BLUE}Step 3/9: Configuring registry access on cluster nodes...${NC}"
REGISTRY_DIR="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
for node in $(kind get nodes --name="${CLUSTER_NAME}"); do
  docker exec "${node}" mkdir -p "${REGISTRY_DIR}"
  cat <<EOF | docker exec -i "${node}" cp /dev/stdin "${REGISTRY_DIR}/hosts.toml"
[host."http://${REGISTRY_NAME}:5000"]
EOF
done

echo -e "${BLUE}Step 4/9: Connecting registry to cluster network...${NC}"
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}")" = 'null' ]; then
  docker network connect "kind" "${REGISTRY_NAME}"
fi

echo -e "${BLUE}Step 5/9: Documenting local registry...${NC}"
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REGISTRY_PORT}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
EOF

echo -e "${BLUE}Step 6/9: Creating namespaces...${NC}"
kubectl apply -f "$ROOT/deploy/k8s/namespaces.yaml"

echo -e "${BLUE}Step 7/9: Installing observability backbone...${NC}"
COLLECTOR_CONFIG="$ROOT/deploy/observability/collector-config.yaml"
if [[ -n "${DASH0_AUTH_TOKEN:-}" ]]; then
  echo -e "${GREEN}DASH0_AUTH_TOKEN present: enabling the Dash0 exporter alongside Jaeger.${NC}"
  COLLECTOR_CONFIG="$ROOT/deploy/observability/collector-config-dash0.yaml"
  kubectl create secret generic dash0-secrets \
    --namespace observability \
    --from-literal=dash0-authorization-token="$DASH0_AUTH_TOKEN" \
    --from-literal=dash0-dataset="${DASH0_DATASET:-default}" \
    --from-literal=dash0-grpc-hostname="${DASH0_ENDPOINT_OTLP_GRPC_HOSTNAME}" \
    --from-literal=dash0-grpc-port="${DASH0_ENDPOINT_OTLP_GRPC_PORT}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo -e "${YELLOW}DASH0_AUTH_TOKEN not set: exporting to Jaeger only (vendor-neutral default).${NC}"
fi

kubectl create configmap otel-collector-config \
  --namespace observability \
  --from-file=collector-config.yaml="$COLLECTOR_CONFIG" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$ROOT/deploy/observability/jaeger.yaml"
kubectl apply -f "$ROOT/deploy/observability/collector.yaml"
# The manifest's `image:` literal is a documented default; the deployed image genuinely
# derives from OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE (.env), not the literal.
kubectl -n observability set image deployment/otel-collector \
  collector="${OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE}"

echo -e "${BLUE}Step 8/9: Rolling out observability workloads...${NC}"
kubectl -n observability rollout restart deployment/otel-collector
kubectl -n observability rollout status deployment/jaeger --timeout=180s
kubectl -n observability rollout status deployment/otel-collector --timeout=180s

echo -e "${BLUE}Step 9/9: Building and deploying stage 1 workloads, then starting port-forwards...${NC}"
"$ROOT/scripts/rebuild-and-deploy.sh" \
  payments-api payments-mcp settlement-agent claims-agent customer-ui

# A9: a stray port-forward from another cluster on this machine (e.g.
# `capybara`) has silently shadowed a host port before, twice. This only ever
# kills forwards matching this repo's own pattern, never blanket-kills
# another cluster's forwards — see scripts/teardown.sh for the same rule.
#
# S1 (final-review.md): the pattern used to be "port-forward.*northstar",
# which cannot match anything this script itself launches — every command
# below reads "kubectl -n northstar-<ns> port-forward ...", i.e. "northstar"
# precedes "port-forward", not the other way round. Verified directly:
# `pgrep -f 'port-forward.*northstar'` finds nothing even with these
# port-forwards running; `pgrep -f 'northstar.*port-forward'` finds them.
# The old pattern's only practical effect was a silent no-op here, and (in
# teardown.sh) an announced cleanup that stopped nothing.
pkill -f "northstar.*port-forward" 2>/dev/null || true
kubectl -n northstar-agents port-forward svc/claims-agent 19001:9001 >/dev/null 2>&1 &
CLAIMS_PF_PID=$!
kubectl -n northstar-agents port-forward svc/settlement-agent 19002:9002 >/dev/null 2>&1 &
SETTLEMENT_PF_PID=$!
kubectl -n northstar-tools port-forward svc/payments-mcp 18000:8000 >/dev/null 2>&1 &
MCP_PF_PID=$!
kubectl -n northstar-systems port-forward svc/payments-api 18080:8080 >/dev/null 2>&1 &
API_PF_PID=$!
sleep 4

# A9 again: don't just launch the forwards and declare success — a stray
# forward from another cluster silently shadowing one of these host ports is
# exactly the failure mode A9 exists to catch, and this project has hit it
# twice already (see apps/customer-ui/NOTES.md §8). Verify each port is
# actually bound by the PID we just started, not merely that something is
# listening there.
verify_port_forward() {
  local port="$1" pid="$2" name="$3"
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -qx "${pid}"; then
    echo -e "${RED}Error: the port-forward for ${name} (expected PID ${pid} listening on ${port}) never bound.${NC}" >&2
    echo "Another process may already hold that port — check: lsof -nP -iTCP:${port} -sTCP:LISTEN (amendment A9)." >&2
    exit 1
  fi
}
verify_port_forward 19001 "${CLAIMS_PF_PID}" claims-agent
verify_port_forward 19002 "${SETTLEMENT_PF_PID}" settlement-agent
verify_port_forward 18000 "${MCP_PF_PID}" payments-mcp
verify_port_forward 18080 "${API_PF_PID}" payments-api
echo -e "${GREEN}Port-forwards verified: claims-agent, settlement-agent, payments-mcp, payments-api.${NC}"

echo ""
echo -e "${GREEN}Stage 1 (Connected) is up.${NC}"
echo "  Customer UI:         http://localhost:8080"
echo "  Jaeger UI:           http://localhost:16686"
echo "  Collector OTLP/HTTP: http://localhost:4318"
echo "  claims-agent (A2A):  http://localhost:19001"
echo "  Local registry:      localhost:${REGISTRY_PORT}"
echo ""
echo "Next: ./scripts/smoke-test.sh"
