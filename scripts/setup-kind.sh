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

echo -e "${BLUE}Step 1/8: Creating local registry '${REGISTRY_NAME}'...${NC}"
if [ "$(docker inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null || true)" != 'true' ]; then
  docker run \
    -d --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" --network bridge --name "${REGISTRY_NAME}" \
    registry:2
  echo -e "${GREEN}Local registry started on port ${REGISTRY_PORT}${NC}"
else
  echo -e "${GREEN}Local registry already running${NC}"
fi

echo -e "${BLUE}Step 2/8: Creating kind cluster '${CLUSTER_NAME}'...${NC}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo -e "${YELLOW}Cluster already exists, skipping creation.${NC}"
else
  kind create cluster --config "$ROOT/deploy/kind/stage1.yaml"
fi
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

echo -e "${BLUE}Step 3/8: Configuring registry access on cluster nodes...${NC}"
REGISTRY_DIR="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
for node in $(kind get nodes --name="${CLUSTER_NAME}"); do
  docker exec "${node}" mkdir -p "${REGISTRY_DIR}"
  cat <<EOF | docker exec -i "${node}" cp /dev/stdin "${REGISTRY_DIR}/hosts.toml"
[host."http://${REGISTRY_NAME}:5000"]
EOF
done

echo -e "${BLUE}Step 4/8: Connecting registry to cluster network...${NC}"
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}")" = 'null' ]; then
  docker network connect "kind" "${REGISTRY_NAME}"
fi

echo -e "${BLUE}Step 5/8: Documenting local registry...${NC}"
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

echo -e "${BLUE}Step 6/8: Creating namespaces...${NC}"
kubectl apply -f "$ROOT/deploy/k8s/namespaces.yaml"

echo -e "${BLUE}Step 7/8: Installing observability backbone...${NC}"
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

echo -e "${BLUE}Step 8/8: Rolling out observability workloads...${NC}"
kubectl -n observability rollout restart deployment/otel-collector
kubectl -n observability rollout status deployment/jaeger --timeout=180s
kubectl -n observability rollout status deployment/otel-collector --timeout=180s

echo ""
echo -e "${GREEN}Stage 1 observability is up.${NC}"
echo "  Jaeger UI:           http://localhost:16686"
echo "  Collector OTLP/HTTP: http://localhost:4318"
echo "  Local registry:      localhost:${REGISTRY_PORT}"
echo ""
echo "Next: python -m pytest tests/e2e/test_harness.py -v"
