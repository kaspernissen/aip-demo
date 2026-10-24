#!/usr/bin/env bash
set -euo pipefail

# Builds the whole Northstar demo on one kind cluster, in one pass:
# Linkerd, SPIRE, agentgateway and the Policy Decision Service, flagd through
# the Open Feature Operator, Kyverno and the keystone policy, the
# observability stack (collector, Jaeger, Prometheus, Weaver live-check,
# AgentEvals), Backstage, and the agents, tools and UIs. Every page and API
# the laptop needs is a NodePort published on localhost by
# deploy/kind/cluster.yaml; the script ends by checking each one answers
# (scripts/preflight.sh).
#
# Safe to re-run against the cluster it created: every step is an idempotent
# apply, `helm upgrade --install` or rebuild.

CLUSTER_NAME="northstar"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"
# Linkerd. OSS Linkerd ships edge releases only. The chart version is NOT the
# app version (2026.10.1 installs edge-26.10.1), and linkerd-crds and
# linkerd-control-plane are versioned together.
#
# Gateway API CRDs go in first (linkerd-crds has installGatewayAPI: false):
# Linkerd's HTTPRoute-based per-route authorization needs them. Standard
# channel, v1.5.1, inside the 1.2.1-1.5.1 range Linkerd 2.20 supports.
LINKERD_CHART_VERSION="2026.10.1"
LINKERD_APP_VERSION="edge-26.10.1"
LINKERD_HELM_REPO="https://helm.linkerd.io/edge"
GATEWAY_API_VERSION="v1.5.1"
GATEWAY_API_MANIFEST="https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
# SPIRE, from the HARDENED charts repo (the older spiffe/helm-charts location
# does not carry these charts). Two charts, and the ORDER MATTERS: `spire`
# installed on its own fails on the missing spire.spiffe.io CRDs, which its
# bundled spire-controller-manager needs. The repo tags per chart, so the two
# numbers are unrelated. `spire` 0.30.2 installs SPIRE 1.15.3. Both are also
# recorded in deploy/platform/spire/values.yaml's header.
SPIRE_CRDS_CHART_VERSION="0.6.1"
SPIRE_CHART_VERSION="0.30.2"
SPIFFE_HELM_REPO="https://spiffe.github.io/helm-charts-hardened/"
# Prometheus. Chart 29.29.0 installs Prometheus v3.14.0 (chart and app
# versions are independent). Also recorded in
# deploy/observability/prometheus/values.yaml's header.
PROMETHEUS_CHART_VERSION="29.29.0"
PROMETHEUS_HELM_REPO="https://prometheus-community.github.io/helm-charts"
# cert-manager and the Open Feature Operator (OFO), checked against the
# upstream releases, not OFO's install docs (which lag behind on
# cert-manager). OFO's chart version carries the "v" prefix, unlike every
# other chart here.
#
# flagd itself is not pinned here: OFO injects it as a sidecar at the image
# tag its own chart values pin (v0.16.2 for OFO v0.9.3; the newest flagd is
# v0.16.3). This script pins the OFO chart, not the sidecar inside it.
CERT_MANAGER_VERSION="1.21.2"
OPENFEATURE_CHART_VERSION="v0.9.3"
OPENFEATURE_HELM_REPO="https://open-feature.github.io/open-feature-operator/"
# Kyverno. Chart 3.9.1 installs app v1.19.1; the numbers are unrelated, like
# SPIRE's.
KYVERNO_CHART_VERSION="3.9.1"
KYVERNO_APP_VERSION="v1.19.1"
KYVERNO_HELM_REPO="https://kyverno.github.io/kyverno/"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KIND_CONFIG="$ROOT/deploy/kind/cluster.yaml"

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
Usage: setup-kind.sh

Creates the kind cluster `northstar` from deploy/kind/cluster.yaml (if it does
not exist yet) and installs the complete demo on it. Needs docker, kind,
kubectl, helm and curl, and a .env copied from .env.template.

When it finishes, every UI is on localhost (the list is printed at the end)
and ./scripts/smoke-test.sh runs the full test gate.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

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
command_exists helm || { echo -e "${RED}helm is required: brew install helm${NC}" >&2; exit 1; }
command_exists curl || { echo -e "${RED}curl is required${NC}" >&2; exit 1; }
: "${OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE:?OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE not set in .env}"
[[ -f "$KIND_CONFIG" ]] || { echo -e "${RED}Error: ${KIND_CONFIG} not found.${NC}" >&2; exit 1; }

echo -e "${BLUE}Step 1/12: Creating local registry '${REGISTRY_NAME}'...${NC}"
# "Does not exist" and "exists but stopped" are different: `docker run` on a
# stopped container's name fails on the name collision, `docker start` fixes it.
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

echo -e "${BLUE}Step 2/12: Creating kind cluster '${CLUSTER_NAME}'...${NC}"
# The host ports deploy/kind/cluster.yaml publishes, read from the file itself.
HOST_PORTS="$(sed -n 's/^[[:space:]]*hostPort:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$KIND_CONFIG")"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  # Host port mappings are fixed when the cluster is created. A cluster made
  # from an older config would "succeed" here with some UIs unreachable, so
  # refuse and say how to recreate it.
  MAPPED="$(docker port "${CLUSTER_NAME}-control-plane" 2>/dev/null || true)"
  MISSING=""
  for port in $HOST_PORTS; do
    grep -Eq ":${port}\$" <<<"$MAPPED" || MISSING="${MISSING} ${port}"
  done
  if [[ -n "$MISSING" ]]; then
    echo -e "${RED}Error: cluster '${CLUSTER_NAME}' already exists but does not publish host port(s)${MISSING}.${NC}" >&2
    echo "It was created from an older kind config, and host port mappings are fixed at creation. Recreate it:" >&2
    echo "  ./scripts/teardown.sh && ./scripts/setup-kind.sh" >&2
    exit 1
  fi
  echo -e "${YELLOW}Cluster already exists, skipping creation.${NC}"
else
  # A leftover `kubectl port-forward` for this repo would hold one of the host
  # ports and make `kind create` fail. Only forwards naming "northstar" are
  # stopped, never another cluster's; both word orders are matched because
  # pkill has no portable alternation.
  pkill -f "northstar.*port-forward" 2>/dev/null || true
  pkill -f "port-forward.*northstar" 2>/dev/null || true
  kind create cluster --config "$KIND_CONFIG"
fi
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

echo -e "${BLUE}Step 3/12: Installing Linkerd ${LINKERD_APP_VERSION} (chart ${LINKERD_CHART_VERSION})...${NC}"
# Linkerd's injector is a mutating webhook: a pod admitted before it is
# serving comes up WITHOUT its proxy and nothing says so. So the control plane
# is installed, and every Deployment in `linkerd` is Available, before any
# workload is applied.
kubectl --context "kind-${CLUSTER_NAME}" wait --for=condition=Ready nodes --all --timeout=300s
kubectl --context "kind-${CLUSTER_NAME}" -n kube-system rollout status deployment/coredns --timeout=300s

# Gateway API CRDs (standard channel). Server-side apply: these CRDs are larger
# than the client-side last-applied annotation allows.
kubectl --context "kind-${CLUSTER_NAME}" apply --server-side -f "${GATEWAY_API_MANIFEST}"

# The identity certificates (openssl, ECDSA P-256), idempotent, into a
# git-ignored directory.
LINKERD_CERTS="$ROOT/.demo/linkerd-certs"
"$ROOT/scripts/linkerd-certs.sh" "${LINKERD_CERTS}"

helm repo add linkerd-edge "${LINKERD_HELM_REPO}" --force-update >/dev/null
helm repo update linkerd-edge >/dev/null
helm upgrade --install linkerd-crds linkerd-edge/linkerd-crds \
  --version "${LINKERD_CHART_VERSION}" \
  --namespace linkerd --create-namespace \
  --kube-context "kind-${CLUSTER_NAME}" \
  --wait --timeout 5m
helm upgrade --install linkerd-control-plane linkerd-edge/linkerd-control-plane \
  --version "${LINKERD_CHART_VERSION}" \
  --namespace linkerd \
  --kube-context "kind-${CLUSTER_NAME}" \
  --values "$ROOT/deploy/platform/linkerd/values.yaml" \
  --set-file identityTrustAnchorsPEM="${LINKERD_CERTS}/ca.crt" \
  --set-file identity.issuer.tls.crtPEM="${LINKERD_CERTS}/issuer.crt" \
  --set-file identity.issuer.tls.keyPEM="${LINKERD_CERTS}/issuer.key" \
  --wait --timeout 10m

kubectl --context "kind-${CLUSTER_NAME}" -n linkerd wait \
  --for=condition=Available deployment --all --timeout=300s
echo -e "${GREEN}Linkerd ${LINKERD_APP_VERSION} is ready.${NC}"

echo -e "${BLUE}Step 4/12: Wiring the local registry into the cluster...${NC}"
REGISTRY_DIR="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
for node in $(kind get nodes --name="${CLUSTER_NAME}"); do
  docker exec "${node}" mkdir -p "${REGISTRY_DIR}"
  cat <<EOF | docker exec -i "${node}" cp /dev/stdin "${REGISTRY_DIR}/hosts.toml"
[host."http://${REGISTRY_NAME}:5000"]
EOF
done
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}")" = 'null' ]; then
  docker network connect "kind" "${REGISTRY_NAME}"
fi
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

echo -e "${BLUE}Step 5/12: Creating namespaces and the reach policy...${NC}"
kubectl apply -f "$ROOT/deploy/k8s/namespaces.yaml"
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: northstar-platform
EOF

# The reach policy: who may call the money path (Linkerd Servers, routes and
# authorizations, by workload identity) and who may leave the agent pods
# (NetworkPolicy egress allowlists). Applied after Linkerd's CRDs and BEFORE
# any workload, so no pod is ever briefly unprotected.
kubectl --context "kind-${CLUSTER_NAME}" apply -f "$ROOT/deploy/platform/reach/"

# The agents' model credential. Reported SET / EMPTY only, never printed. A
# missing key degrades rather than aborting (the secretKeyRef is optional):
# everything comes up, and only the model calls fail, with a clear error.
echo -e "${BLUE}Step 6/12: Creating the model secret...${NC}"
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  kubectl create secret generic northstar-model \
    --namespace northstar-agents \
    --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo -e "${GREEN}  ANTHROPIC_API_KEY: SET${NC}"
else
  echo -e "${YELLOW}  ANTHROPIC_API_KEY: EMPTY — the agents will fail on their first model call${NC}"
fi

echo -e "${BLUE}Step 7/12: Installing the observability backbone...${NC}"
COLLECTOR_CONFIG="$ROOT/deploy/observability/collector-config.yaml"
DASH0_OVERLAY="$ROOT/deploy/observability/collector-dash0-overlay.yaml"
# The collector's args: the base config, plus the Dash0 overlay as a second
# `--config` (the collector merges the two) only when DASH0_AUTH_TOKEN is set.
COLLECTOR_ARGS='"--config=/conf/collector-config.yaml"'
if [[ -n "${DASH0_AUTH_TOKEN:-}" ]]; then
  echo -e "${GREEN}DASH0_AUTH_TOKEN present: enabling the Dash0 exporter alongside Jaeger.${NC}"
  COLLECTOR_ARGS="${COLLECTOR_ARGS}, \"--config=/conf/collector-dash0-overlay.yaml\""
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
  --from-file=collector-dash0-overlay.yaml="$DASH0_OVERLAY" \
  --dry-run=client -o yaml | kubectl apply -f -

# Weaver live-check and AgentEvals go in BEFORE the collector, which sends
# each of them a copy of its telemetry. Their ConfigMaps come from this
# checkout, and a ConfigMap change does not reach a running process, so each
# is restarted (which also empties its in-memory state).
kubectl create configmap weaver-registry \
  --namespace observability \
  --from-file="$ROOT/telemetry/registry" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap weaver-live-check-config \
  --namespace observability \
  --from-file=live-check.toml="$ROOT/telemetry/live-check.toml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$ROOT/deploy/observability/weaver-live-check.yaml"
kubectl -n observability rollout restart deployment/weaver-live-check

# AgentEvals' LLM judge is Claude through LiteLLM, with a copy of the model
# secret in this namespace. Optional: without it the code evaluators still run.
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  kubectl create secret generic northstar-model \
    --namespace observability \
    --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
fi
kubectl create configmap agentevals-evaluators \
  --namespace observability \
  --from-file="$ROOT/deploy/observability/agentevals/evaluators" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$ROOT/deploy/observability/agentevals.yaml"
kubectl -n observability rollout restart deployment/agentevals

kubectl apply -f "$ROOT/deploy/observability/jaeger.yaml"
# The trace cap lives in the jaeger-config ConfigMap: restart so it applies.
# Jaeger is in-memory, so this also starts with an empty trace store.
kubectl -n observability rollout restart deployment/jaeger
sed "s|args: \[\"--config=/conf/collector-config.yaml\"\]|args: [${COLLECTOR_ARGS}]|" \
  "$ROOT/deploy/observability/collector.yaml" | kubectl apply -f -
# The manifest's `image:` is a documented default; the deployed image comes
# from OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE (.env).
kubectl -n observability set image deployment/otel-collector \
  collector="${OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE}"
kubectl -n observability rollout restart deployment/otel-collector
kubectl -n observability rollout status deployment/jaeger --timeout=180s
kubectl -n observability rollout status deployment/otel-collector --timeout=180s

echo -e "${BLUE}Step 8/12: Installing SPIRE (crds ${SPIRE_CRDS_CHART_VERSION}, then spire ${SPIRE_CHART_VERSION})...${NC}"
# Before the workloads: settlement-agent and reviewer-ui mount a
# `csi.spiffe.io` volume, and a pod whose CSI driver is not registered stays
# in ContainerCreating instead of failing.
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

# The registration entries, and the Services that expose SPIRE's own metrics.
kubectl --context "kind-${CLUSTER_NAME}" apply \
  -f "$ROOT/deploy/platform/spire/registration.yaml" \
  -f "$ROOT/deploy/platform/spire/metrics.yaml"

# `helm --wait` covers the server and the CSI driver, but the agent has to
# attest to its node before it answers a Workload API call.
kubectl --context "kind-${CLUSTER_NAME}" -n northstar-platform rollout status daemonset/spire-agent --timeout=300s
kubectl --context "kind-${CLUSTER_NAME}" -n northstar-platform rollout status daemonset/spire-spiffe-csi-driver --timeout=300s
echo -e "${GREEN}SPIRE ${SPIRE_CHART_VERSION} is ready (trust domain northstar.local).${NC}"

echo -e "${BLUE}Step 9/12: Installing Prometheus ${PROMETHEUS_CHART_VERSION} (OTLP ingest, no scrape jobs)...${NC}"
# The collector's `otlp_http/prometheus` exporter has no sending queue, so it
# is restarted once Prometheus is serving (`--wait`): its first export lands
# instead of being dropped.
helm repo add prometheus-community "${PROMETHEUS_HELM_REPO}" --force-update >/dev/null
helm repo update prometheus-community >/dev/null
helm upgrade --install prometheus prometheus-community/prometheus \
  --version "${PROMETHEUS_CHART_VERSION}" \
  --namespace observability \
  --kube-context "kind-${CLUSTER_NAME}" \
  --values "$ROOT/deploy/observability/prometheus/values.yaml" \
  --wait --timeout 5m
kubectl -n observability rollout restart deployment/otel-collector
kubectl -n observability rollout status deployment/otel-collector --timeout=180s

echo -e "${BLUE}Step 10/12: Installing cert-manager ${CERT_MANAGER_VERSION}, the Open Feature Operator ${OPENFEATURE_CHART_VERSION} and the autonomy flag...${NC}"
# Before the Policy Decision Service: its pod template carries the
# openfeature.dev/* annotations OFO's mutating webhook reads at admission. A
# PDS pod created before the webhook exists silently gets no flagd sidecar.
# cert-manager is only here because OFO's webhooks need it for TLS.
kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/v${CERT_MANAGER_VERSION}/cert-manager.yaml"
kubectl --context "kind-${CLUSTER_NAME}" wait --for=condition=Available=True deploy --all -n cert-manager --timeout=300s

# No `--namespace`: the chart hardcodes `open-feature-operator-system`.
helm repo add openfeature "${OPENFEATURE_HELM_REPO}" --force-update >/dev/null
helm repo update openfeature >/dev/null
helm upgrade --install openfeature openfeature/open-feature-operator \
  --version "${OPENFEATURE_CHART_VERSION}" \
  --kube-context "kind-${CLUSTER_NAME}" \
  --wait --timeout 5m

kubectl --context "kind-${CLUSTER_NAME}" apply -f "$ROOT/deploy/platform/openfeature/"
echo -e "${GREEN}The northstar-autonomy FeatureFlag is applied; flagd is injected into the PDS when it is deployed below.${NC}"

echo -e "${BLUE}Step 11/12: Building and deploying Backstage, then Kyverno and the keystone policy...${NC}"
"$ROOT/scripts/rebuild-and-deploy.sh" backstage

# A Ready pod only proves Backstage's own health endpoint. The autonomy card's
# writer token is filled in asynchronously, and a card that mounted before it
# landed answers 503 on every route. So ask the card itself, as a guest, from
# inside the pod (the image has node but no curl). Unauthenticated, every
# /api route answers 401, so the guest token is needed to tell 200 from 503.
BACKSTAGE_DELIVERY_CHECK=""
for _ in $(seq 1 20); do
  if kubectl --context "kind-${CLUSTER_NAME}" -n northstar-platform \
      exec deploy/backstage -- node -e '
    const http = require("http");
    function req(opts, body) {
      return new Promise((resolve, reject) => {
        const r = http.request(opts, res => {
          let data = "";
          res.on("data", c => { data += c; });
          res.on("end", () => resolve({ status: res.statusCode, body: data }));
        });
        r.on("error", reject);
        if (body) r.write(body);
        r.end();
      });
    }
    (async () => {
      const refreshBody = JSON.stringify({});
      const refresh = await req({
        host: "localhost", port: 7007, path: "/api/auth/guest/refresh",
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(refreshBody) },
      }, refreshBody);
      if (refresh.status !== 200) { console.error("guest/refresh: " + refresh.status); process.exit(1); }
      const token = JSON.parse(refresh.body).backstageIdentity.token;
      const status = await req({
        host: "localhost", port: 7007, path: "/api/autonomy-contract/status",
        method: "GET", headers: { Authorization: "Bearer " + token },
      });
      console.error("autonomy-contract/status: " + status.status);
      process.exit(status.status === 200 ? 0 : 1);
    })().catch(e => { console.error("delivery check error: " + e.message); process.exit(1); });
  ' 2>/dev/null; then
    BACKSTAGE_DELIVERY_CHECK="yes"
    break
  fi
  sleep 3
done
if [[ -z "${BACKSTAGE_DELIVERY_CHECK}" ]]; then
  echo -e "${RED}Error: the autonomy-contract card never answered 200 on /api/autonomy-contract/status (as a guest) within 60s of Backstage becoming Ready.${NC}" >&2
  echo "The pod is Ready but the card cannot write (its writer credential did not arrive in time). Check: kubectl --context kind-${CLUSTER_NAME} -n northstar-platform logs deploy/backstage | grep 'autonomy-contract'" >&2
  exit 1
fi
echo -e "${GREEN}Backstage is up; the autonomy-contract card answered 200 as a guest, so its writer credential is live.${NC}"

# Kyverno: everything changed from the chart defaults is in its values file,
# with the reason. Two matter: OTLP tracing on the admission controller (the
# one that serves the webhook, so the only one that can emit a span for a
# denial), and read access to every kind the keystone matches. Without the
# second the policy is stored but never reports Ready.
helm repo add kyverno "${KYVERNO_HELM_REPO}" --force-update >/dev/null
helm repo update kyverno >/dev/null
helm upgrade --install kyverno kyverno/kyverno \
  --version "${KYVERNO_CHART_VERSION}" \
  --namespace kyverno --create-namespace \
  --kube-context "kind-${CLUSTER_NAME}" \
  -f "$ROOT/deploy/platform/kyverno/values.yaml" \
  --wait --timeout 5m
echo -e "${GREEN}Kyverno ${KYVERNO_CHART_VERSION} (app ${KYVERNO_APP_VERSION}) is ready, tracing to otel-collector.observability:4317.${NC}"

kubectl --context "kind-${CLUSTER_NAME}" apply -f "$ROOT/deploy/platform/kyverno/keystone-policy.yaml"
# `apply` returns once the object is stored; the policy enforces only once
# Kyverno has compiled it and registered its webhook, which is what
# `.status.conditionStatus.ready` reports. The over-grant in deploy/demo/ is a
# demo fixture `scripts/demo keystone` applies and removes, never this script.
kubectl --context "kind-${CLUSTER_NAME}" wait validatingpolicy/agents-cannot-edit-their-own-authority \
  --for=jsonpath='{.status.conditionStatus.ready}'=true --timeout=120s
echo -e "${GREEN}The keystone policy is Ready: an agent identity cannot change the objects that define its own authority.${NC}"

echo -e "${BLUE}Step 12/12: Building and deploying the agents, tools, systems, PDS, gateway and UIs...${NC}"
"$ROOT/scripts/rebuild-and-deploy.sh" \
  payments-api payments-mcp settlement-agent claims-agent customer-ui

# The Policy Decision Service goes in BEFORE the gateway that calls it. The
# gateway's guardrail is fail-closed, so a gateway without a Ready PDS refuses
# every tools/call with an outage message that looks like a policy refusal.
"$ROOT/scripts/rebuild-and-deploy.sh" policy-decision-service
echo -e "${GREEN}policy-decision-service is up on 9100.${NC}"

# agentgateway sits in the MCP path (settlement-agent's PAYMENTS_MCP_URL names
# it) and calls the PDS on every tools/call. Its config is mounted from a
# ConfigMap and read once at startup, so it is restarted after every apply:
# otherwise the ConfigMap and the running gateway can silently disagree.
kubectl create configmap agentgateway-config \
  --namespace northstar-platform \
  --from-file=config.yaml="$ROOT/deploy/platform/agentgateway/config.yaml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$ROOT/deploy/platform/agentgateway/deployment.yaml"
kubectl -n northstar-platform rollout restart deployment/agentgateway
kubectl -n northstar-platform rollout status deployment/agentgateway --timeout=120s
echo -e "${GREEN}agentgateway is up, calling the PDS on every tools/call (fail-closed).${NC}"

# reviewer-ui: the human approval page, and the identity that releases an
# approved payment through the gateway.
"$ROOT/scripts/rebuild-and-deploy.sh" reviewer-ui

echo ""
echo -e "${BLUE}--- Checking every localhost URL answers ---${NC}"
"$ROOT/scripts/preflight.sh"
echo "  Local registry:      localhost:${REGISTRY_PORT}"
echo ""
echo "Next: ./scripts/smoke-test.sh (the test gate), or scripts/demo (the five-minute flow)."
