#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="northstar"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"
STAGE=1
# Nothing floats. Resolved from `helm search repo cilium/cilium --versions`:
# newest stable 1.x, appVersion 1.20.1 (previous minor 1.19.7). The same
# version is recorded in deploy/platform/cilium-values.yaml's header and
# pinned by tests/unit/test_stage2_cilium_config.py so the two cannot drift.
CILIUM_CHART_VERSION="1.20.1"
CILIUM_HELM_REPO="https://helm.cilium.io/"
# M2.1 Task 4: SPIRE. Two charts, and the ORDER MATTERS -- `spiffe/spire`
# installed on its own fails on the missing spire.spiffe.io CRDs, which its own
# bundled spire-controller-manager needs before it can reconcile a single
# ClusterSPIFFEID. The hardened charts repo ships them separately for exactly
# that reason (CRDs outlive releases; Helm will not upgrade them in place).
#
# This is the HARDENED charts repo, https://spiffe.github.io/helm-charts-hardened/
# -- not the older spiffe/helm-charts location, which does not carry these
# charts at all. Resolved from `helm search repo spiffe/spire --versions` and
# cross-checked against the upstream releases API: the repo tags PER CHART
# (`spire-0.30.1` and `spire-crds-0.6.1` are independent tag series), so these
# two numbers are unrelated to each other. `spire` 0.30.1's appVersion is SPIRE
# 1.15.3. Both versions are also recorded in
# deploy/platform/spire/values.yaml's header and pinned by
# tests/unit/test_stage2_spire_config.py so the two cannot drift.
SPIRE_CRDS_CHART_VERSION="0.6.1"
SPIRE_CHART_VERSION="0.30.1"
SPIFFE_HELM_REPO="https://spiffe.github.io/helm-charts-hardened/"
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

  --stage 1   Connected:  agents, tools, systems, customer UI, collector, Jaeger
                          [installs]
  --stage 2   Governed:   stage 1, on a Cilium dataplane -- the cluster is
                          created with kind's default CNI disabled
                          [installs Cilium, SPIFFE/SPIRE and agentgateway.
                          The gateway is in the MCP path as of M2.1 Task 3
                          and verifies a SPIRE-issued JWT-SVID on every MCP
                          call as of Task 4; it still carries no
                          authorization policy. Kyverno and the Policy
                          Decision Service are part of this stage in the
                          design but are NOT installed yet. cert-manager is
                          NOT installed and is not needed: the pinned SPIRE
                          chart only uses it for optional features this
                          install does not enable -- see
                          deploy/platform/spire/values.yaml]
  --stage 3   Supervised:           + Dapr, Keycloak, reviewer UI, Postgres
                          [not implemented -- exits]
  --stage 4   Observable:           + OpenSearch, Prometheus
                          [not implemented -- exits]
  --stage 5   Progressive autonomy: + flagd
                          [not implemented -- exits]
  --stage 6   Golden path:          + Crossplane, Argo CD, Backstage
                          [not implemented -- exits]

Each stage installs only what that stage of the demo needs, so the laptop
runs exactly one stage at a time.

Stage 2 is the current cluster. It must be created FROM deploy/kind/stage2.yaml:
`networking.disableDefaultCNI` is a create-time kind flag, so it cannot be
applied to a cluster that already exists, and this script refuses rather than
layering Cilium on a running default CNI. Note that --stage defaults to 1, so a
bare `./scripts/setup-kind.sh` builds a stage-1 (kindnet) cluster -- pass
--stage 2 explicitly for the M2.1 dataplane.
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

if [[ "${STAGE}" -gt 2 ]]; then
  echo "Stage $STAGE is not implemented yet. Stages 1 (Connected) and 2 (Governed: Cilium) are implemented; stages 3-6 arrive in later milestones." >&2
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
if [[ "${STAGE}" -ge 2 ]]; then
  command_exists helm || { echo -e "${RED}helm is required for stage 2 (Cilium): brew install helm${NC}" >&2; exit 1; }
fi
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
# Each stage brings its own kind config. Stage 2's only difference from stage
# 1's is `networking.disableDefaultCNI: true`, which is a **create-time** flag
# — see deploy/kind/stage2.yaml's header.
KIND_CONFIG="$ROOT/deploy/kind/stage${STAGE}.yaml"
[[ -f "$KIND_CONFIG" ]] || { echo -e "${RED}Error: ${KIND_CONFIG} not found.${NC}" >&2; exit 1; }

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  # `disableDefaultCNI` cannot be applied to a cluster that already exists, so
  # re-running `--stage 2` over a stage-1 cluster would install Cilium on top
  # of a live kindnet — two CNIs, and a script that reports success. That is
  # the worst outcome available here precisely because it looks like it
  # worked, so detect it and refuse. The check reads the cluster rather than
  # trusting a marker file: kindnet's DaemonSet is present if and only if kind
  # installed the default CNI.
  if [[ "${STAGE}" -ge 2 ]] \
    && kubectl --context "kind-${CLUSTER_NAME}" -n kube-system get daemonset kindnet >/dev/null 2>&1; then
    echo -e "${RED}Error: cluster '${CLUSTER_NAME}' already exists and is still running the default CNI (the kube-system/kindnet DaemonSet is present).${NC}" >&2
    echo "networking.disableDefaultCNI is a create-time kind flag, so stage 2 cannot be applied to this cluster." >&2
    echo "Recreate it deliberately:" >&2
    echo "  kind delete cluster --name ${CLUSTER_NAME} && ./scripts/setup-kind.sh --stage ${STAGE}" >&2
    exit 1
  fi
  echo -e "${YELLOW}Cluster already exists, skipping creation.${NC}"
else
  kind create cluster --config "$KIND_CONFIG"
fi
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

if [[ "${STAGE}" -ge 2 ]]; then
  echo -e "${BLUE}Step 2b/9 (stage 2+): Installing Cilium ${CILIUM_CHART_VERSION}...${NC}"
  # With disableDefaultCNI the node stays NotReady and coredns stays Pending
  # until a CNI arrives, so this must run before anything is scheduled. The
  # API server itself is host-networked and already reachable above.
  helm repo add cilium "${CILIUM_HELM_REPO}" --force-update >/dev/null
  helm repo update cilium >/dev/null
  helm upgrade --install cilium cilium/cilium \
    --version "${CILIUM_CHART_VERSION}" \
    --namespace kube-system \
    --kube-context "kind-${CLUSTER_NAME}" \
    --values "$ROOT/deploy/platform/cilium-values.yaml" \
    --wait --timeout 10m

  # Cilium must be *ready*, not merely installed, before any workload is
  # applied: a pod scheduled while cilium-agent is still starting comes up
  # without networking, which presents as a broken demo rather than a failed
  # install. Ordering asserted by
  # tests/unit/test_stage2_cilium_config.py::test_cilium_is_ready_before_any_workload_is_deployed.
  kubectl --context "kind-${CLUSTER_NAME}" -n kube-system rollout status daemonset/cilium --timeout=300s
  kubectl --context "kind-${CLUSTER_NAME}" -n kube-system rollout status deployment/cilium-operator --timeout=300s
  kubectl --context "kind-${CLUSTER_NAME}" wait --for=condition=Ready nodes --all --timeout=300s
  # coredns was created before the CNI existed and sat Pending; it only
  # schedules once Cilium is up. Wait for it too — every service in this repo
  # resolves its peers by DNS name, so a workload deployed while coredns is
  # still coming up fails on name resolution, not on networking.
  kubectl --context "kind-${CLUSTER_NAME}" -n kube-system rollout status deployment/coredns --timeout=300s
  echo -e "${GREEN}Cilium ${CILIUM_CHART_VERSION} is ready.${NC}"
fi

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

if [[ "${STAGE}" -ge 2 ]]; then
  # Checked deploy/ first, as the plan asks: deploy/k8s/namespaces.yaml creates
  # four namespaces — observability, northstar-agents, northstar-tools,
  # northstar-systems — and no `northstar-platform`. It is NOT added to that
  # shared file, because that file is stage 1's and stage 1 has nothing to put
  # in this namespace; creating it here keeps stage 1 unchanged. M2.1 Tasks 2
  # and 4 both need it.
  kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: northstar-platform
EOF

  # M2.2: the network policy that makes the choke point real rather than
  # conventional -- only agentgateway may reach payments-mcp on 8000. See
  # deploy/platform/cilium-policies.yaml for the measurement that motivated it
  # (claims-agent reached the tool directly and got HTTP 200).
  #
  # Applied HERE, before Step 9 deploys any workload, and not next to the
  # gateway install at Step 9b where it would read more naturally. A
  # CiliumNetworkPolicy selects endpoints by label and does not require its
  # targets to exist yet, so applying it early costs nothing and closes the
  # window in which payments-mcp is running but unprotected. The CRD it needs
  # was installed with Cilium at Step 2b, which is why this cannot move any
  # earlier than this block.
  kubectl --context "kind-${CLUSTER_NAME}" apply \
    -f "$ROOT/deploy/platform/cilium-policies.yaml"
fi

# M1.2: settlement-agent's decision is a real model call, so it needs a model
# credential in-cluster. Same idempotent shape as the dash0-secrets block
# below (`--dry-run=client -o yaml | kubectl apply -f -`), so re-running this
# script updates the secret in place rather than failing on "already exists".
#
# The value is handed to kubectl and never printed — this block reports SET /
# EMPTY only, and nothing anywhere near it enables `set -x`. A missing key
# degrades rather than aborting: deploy/k8s/settlement-agent.yaml marks the
# secretKeyRef `optional: true` (matching the convention used for degradable
# secrets elsewhere), so stage 1 still comes up fully traced and every hop
# below the model still works; only the settlement decision itself fails, at
# its first model call, with a clear credential error.
echo -e "${BLUE}Step 6/9: Creating the model secret...${NC}"
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  kubectl create secret generic northstar-model \
    --namespace northstar-agents \
    --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo -e "${GREEN}  ANTHROPIC_API_KEY: SET${NC}"
else
  echo -e "${YELLOW}  ANTHROPIC_API_KEY: EMPTY — settlement-agent will fail on its first model call${NC}"
fi

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

if [[ "${STAGE}" -ge 2 ]]; then
  echo -e "${BLUE}Step 8b/9 (stage 2+): Installing SPIRE (crds ${SPIRE_CRDS_CHART_VERSION}, then spire ${SPIRE_CHART_VERSION})...${NC}"
  # This runs BEFORE Step 9, not after it, and that is load-bearing:
  # deploy/k8s/settlement-agent.yaml mounts a `csi.spiffe.io` volume, and a pod
  # requesting a CSI volume whose driver is not registered stays in
  # ContainerCreating rather than failing fast. Installing SPIRE after the
  # workloads would leave settlement-agent wedged and the script blocked on
  # `rollout status`.
  helm repo add spiffe "${SPIFFE_HELM_REPO}" --force-update >/dev/null
  helm repo update spiffe >/dev/null
  helm upgrade --install spire-crds spiffe/spire-crds \
    --version "${SPIRE_CRDS_CHART_VERSION}" \
    --namespace northstar-platform \
    --kube-context "kind-${CLUSTER_NAME}" \
    --wait --timeout 5m
  helm upgrade --install spire spiffe/spire \
    --version "${SPIRE_CHART_VERSION}" \
    --namespace northstar-platform \
    --kube-context "kind-${CLUSTER_NAME}" \
    --values "$ROOT/deploy/platform/spire/values.yaml" \
    --wait --timeout 10m

  # The registration entries, and the Services that make SPIRE's own
  # Prometheus telemetry scrapable. Both are declarative and idempotent, so
  # re-running this script re-applies them without error.
  kubectl --context "kind-${CLUSTER_NAME}" apply \
    -f "$ROOT/deploy/platform/spire/registration.yaml" \
    -f "$ROOT/deploy/platform/spire/metrics.yaml"

  # SPIRE must be *ready*, not merely installed, before the workloads that
  # mount its socket are applied -- same rule Cilium follows above, for the
  # same reason. `helm --wait` already waited on the server and the CSI
  # driver, but the spire-agent DaemonSet has to have attested to its node
  # before it can answer a single Workload API call, and that happens after
  # the pod is Running.
  kubectl --context "kind-${CLUSTER_NAME}" -n northstar-platform rollout status daemonset/spire-agent --timeout=300s
  kubectl --context "kind-${CLUSTER_NAME}" -n northstar-platform rollout status daemonset/spire-spiffe-csi-driver --timeout=300s
  echo -e "${GREEN}SPIRE ${SPIRE_CHART_VERSION} is ready (trust domain northstar.local).${NC}"
fi

echo -e "${BLUE}Step 9/9: Building and deploying stage 1 workloads, then starting port-forwards...${NC}"
"$ROOT/scripts/rebuild-and-deploy.sh" \
  payments-api payments-mcp settlement-agent claims-agent customer-ui

if [[ "${STAGE}" -ge 2 ]]; then
  # M2.1 Task 2 deployed agentgateway permitting everything but out of the
  # path; Task 3 put it IN the path -- settlement-agent's PAYMENTS_MCP_URL
  # now names it (deploy/k8s/settlement-agent.yaml). It still carries no
  # policy: M2.2 adds the decision.
  #
  # Still applied after the workloads it fronts, so `payments-mcp` already
  # exists when the gateway starts proxying to it. Task 3 checked whether this
  # now needs to move *above* Step 9 (settlement-agent is deployed pointing at
  # a gateway that does not exist yet) and deliberately left it: the pod's
  # readinessProbe is its agent card, not an MCP call, and nothing between here
  # and the `rollout status` wait below drives a settlement -- so the gateway is
  # up before any request can reach it. If a future step ever drives traffic
  # between those two points, this block has to move.
  echo -e "${BLUE}Step 9b/9 (stage 2+): Installing agentgateway...${NC}"
  kubectl create configmap agentgateway-config \
    --namespace northstar-platform \
    --from-file=config.yaml="$ROOT/deploy/platform/agentgateway/config.yaml" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f "$ROOT/deploy/platform/agentgateway/deployment.yaml"
  kubectl -n northstar-platform rollout status deployment/agentgateway --timeout=120s
  echo -e "${GREEN}agentgateway is up (in the MCP path, permitting everything -- no policy yet).${NC}"
fi

# A9: a stray port-forward from another cluster on this machine (e.g.
# `capybara`) has silently shadowed a host port before, twice. This only ever
# kills forwards matching this repo's own pattern, never blanket-kills
# another cluster's forwards — see scripts/teardown.sh for the same rule.
#
# S1 (final-review.md, first round): the pattern used to be
# "port-forward.*northstar", which cannot match anything this script itself
# launches — every command below reads "kubectl -n northstar-<ns>
# port-forward ...", i.e. "northstar" precedes "port-forward", not the
# other way round. The old pattern's only practical effect was a silent
# no-op here. Fixed to "northstar.*port-forward" — verified directly
# against a live forward.
#
# Second round (final-review.md, nit 4): that single-order fix is itself
# order-sensitive, so a hand-typed "kubectl port-forward -n
# northstar-agents ..." — the form the plan document itself uses, and so
# the form a reader is likely to type — still escapes cleanup, since there
# "port-forward" precedes "northstar". Two calls instead of one regex, one
# per order, cover both without needing alternation support in pkill's
# regex engine (unavailable/unreliable across BSD and GNU pkill).
# Re-verified after this change, live: both call orders correctly kill this
# repo's forwards regardless of which way they were typed, and neither
# ever matches a `capybara`-cluster forward (which contains no "northstar"
# substring in either position).
pkill -f "northstar.*port-forward" 2>/dev/null || true
pkill -f "port-forward.*northstar" 2>/dev/null || true
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
if [[ "${STAGE}" -ge 2 ]]; then
  echo -e "${GREEN}Stage 2 (Governed) is up: stage 1's workloads on a Cilium ${CILIUM_CHART_VERSION} dataplane, with SPIRE ${SPIRE_CHART_VERSION} issuing workload identity.${NC}"
else
  echo -e "${GREEN}Stage 1 (Connected) is up.${NC}"
fi
echo "  Customer UI:         http://localhost:8080"
echo "  Jaeger UI:           http://localhost:16686"
echo "  Collector OTLP/HTTP: http://localhost:4318"
echo "  claims-agent (A2A):  http://localhost:19001"
echo "  Local registry:      localhost:${REGISTRY_PORT}"
echo ""
echo "Next: ./scripts/smoke-test.sh"
