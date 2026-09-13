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
# M4 Task 1: OpenSearch. Verified 2026-09-13 against TWO independent sources,
# not a local Helm cache -- Artifact Hub's opensearch-project-helm-charts/
# opensearch package (version AND app_version both "3.8.0") and
# https://api.github.com/repos/opensearch-project/helm-charts/releases
# (tag opensearch-3.8.0, published 2026-08-05, prerelease: false, the newest
# entry). Unlike Cilium/SPIRE this chart's tags equal its appVersion. The
# same number and the same evidence are recorded in
# deploy/observability/opensearch/values.yaml and dashboards-values.yaml's
# headers.
OPENSEARCH_CHART_VERSION="3.8.0"
OPENSEARCH_HELM_REPO="https://opensearch-project.github.io/helm-charts"
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
                          [installs Cilium, SPIFFE/SPIRE, agentgateway and
                          the Policy Decision Service. The gateway is in the
                          MCP path as of M2.1 Task 3, verifies a
                          SPIRE-issued JWT-SVID on every MCP call as of
                          Task 4, and as of M2.2 Task 4 asks the PDS for a
                          permit/deny on every tools/call -- FAIL-CLOSED, so
                          a PDS outage refuses payments rather than waving
                          them through. Kyverno is part of this stage in the
                          design but is NOT installed yet. cert-manager is
                          NOT installed and is not needed: the pinned SPIRE
                          chart only uses it for optional features this
                          install does not enable -- see
                          deploy/platform/spire/values.yaml]
  --stage 3   Supervised:           stage 2, plus a human in the loop, on a
                          cluster created with a fourth host port reserved
                          (8081 -> nodePort 30081, for reviewer-ui) --
                          deploy/kind/stage3.yaml.
                          [installs, on top of stage 2: reviewer-ui (the
                          approval page a human reads and clicks) and
                          senior-settlement-agent -- the SAME
                          settlement-agent image in a second Deployment under
                          a second ServiceAccount, because "senior" here is an
                          identity, not a different program. The refusal
                          record and the PDS's senior row are in stage 2's
                          images already, since they are code in payments-api
                          and the Policy Decision Service rather than separate
                          workloads. This paragraph read "no M3 workload
                          exists yet" from M3 Task 1 until Task 5B; that was
                          true when written and false from Task 4 onward]
  --stage 4   Observable:           + OpenSearch, Prometheus
                          [as of M4 Task 1: OpenSearch (single node,
                          security plugin off) and OpenSearch Dashboards are
                          installed, and the trace-ingest path into
                          OpenSearch is live. Prometheus arrives in a later
                          M4 task. Runs on stage 3's cluster config
                          UNCHANGED -- see the KIND_CONFIG note below --
                          Dashboards is reached by port-forward
                          (./scripts/start-demo.sh), never by a kind host
                          port]
  --stage 5   Progressive autonomy: + flagd
                          [not implemented -- exits]
  --stage 6   Golden path:          + Crossplane, Argo CD, Backstage
                          [not implemented -- exits]

Each stage installs only what that stage of the demo needs, so the laptop
runs exactly one stage at a time.

Stages 2, 3 and 4 all need `networking.disableDefaultCNI`, a create-time kind
flag: it cannot be applied to a cluster that already exists, so the cluster
must be created FROM a kind config that sets it -- deploy/kind/stage2.yaml or
deploy/kind/stage3.yaml (whose only difference from stage 2's is the
reviewer-ui port reservation; stage 4 reuses stage 3's, see below) -- and this
script refuses rather than layering Cilium on a running default CNI. Note that
--stage defaults to 1, so a bare `./scripts/setup-kind.sh` builds a stage-1
(kindnet) cluster -- pass --stage 2, 3 or 4 explicitly for the Cilium
dataplane. (This paragraph said "Stage 2 and stage 3 both" and listed only
those two until M4 Task 1's fix round; stage 4 needs the create-time flag for
exactly the same reason, because it runs on stage 3's config.)

Stage 4 is the one exception to "each stage brings its own deploy/kind/
stageN.yaml": there is deliberately no stage4.yaml. OpenSearch Dashboards
would have needed a fifth host port (extraPortMappings is create-time only,
same rule as above), and the human partner declined a cluster recreate for
it on 2026-09-12 (M4 Task 1, plan amendment A1) -- so stage 4 reuses stage
3's cluster config unchanged and reaches Dashboards by port-forward instead.
tests/unit/test_stage4_config.py pins that no stage4.yaml appears.
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

if [[ "${STAGE}" -gt 4 ]]; then
  echo "Stage $STAGE is not implemented yet. Stages 1 (Connected), 2 (Governed: Cilium), 3 (Supervised) and 4 (Observable) are implemented; stages 5-6 arrive in later milestones." >&2
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
# — see deploy/kind/stage2.yaml's header. Stage 3's only difference from
# stage 2's is one extra host port reservation for reviewer-ui — see
# deploy/kind/stage3.yaml's header.
KIND_CONFIG="$ROOT/deploy/kind/stage${STAGE}.yaml"
# M4 Task 1, plan amendment A1: there is no stage4.yaml, deliberately. A
# fifth host port mapping (for OpenSearch Dashboards) can only be added at
# cluster CREATION -- same create-time rule as disableDefaultCNI above -- and
# the human partner declined a cluster recreate on 2026-09-12. So --stage 4
# reuses stage 3's cluster config unchanged; Dashboards is reached by
# port-forward (./scripts/start-demo.sh) instead of a host port.
if [[ "${STAGE}" -eq 4 ]]; then
  KIND_CONFIG="$ROOT/deploy/kind/stage3.yaml"
fi
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
    echo "networking.disableDefaultCNI is a create-time kind flag, so stage ${STAGE} cannot be applied to this cluster." >&2
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

# M4 Task 1, fix round 1 (review finding F10, ruling M4-R8): below stage 4 the
# collector must not name a backend this cluster does not have.
#
# OpenSearch is installed only at `-ge 4` (Step 8c below), but the collector
# config is chosen on DASH0_AUTH_TOKEN and never on stage -- so as shipped,
# `--stage 1|2|3` deployed a collector whose traces pipeline named
# `opensearch/trace`. Jaeger still received everything (the fanout is
# per-exporter), so it degraded to permanent export failures logged into a file
# nobody reads, which is precisely the silently-broken shape this repo exists
# to avoid.
#
# The rule is one line of sed and one marker convention, not a YAML rewriter:
# every OpenSearch-only region of both collector configs is bracketed by
# `# BEGIN stage-4-only` / `# END stage-4-only`, including the single
# `- forward/opensearch` entry in the traces pipeline's exporter list (which is
# why that list is written one-per-line). Deleting those regions yields the
# exact pre-M4 config -- verified by round-tripping both files through PyYAML.
# tests/unit/test_stage4_config.py runs this same sed program (it reads the
# expression out of this file, so the two cannot drift) and asserts the result
# parses, still exports traces to Jaeger, and mentions OpenSearch nowhere.
STAGE4_ONLY_STRIP='/^[[:space:]]*# BEGIN stage-4-only$/,/^[[:space:]]*# END stage-4-only$/d'
COLLECTOR_CONFIG_APPLIED="$COLLECTOR_CONFIG"
if [[ "${STAGE}" -lt 4 ]]; then
  # `.XXXXXX`, not a bare prefix (re-review finding N5). BSD/macOS `mktemp -t`
  # appends its own random suffix to whatever prefix it is given, so a bare
  # `collector-config` works here -- but GNU coreutils `mktemp -t` requires
  # the template to end in at least three X and exits `too few X's in
  # template`, which under `set -e` would kill every `--stage 1|2|3` run at
  # this line on Linux. The explicit template works on both. This repo is
  # macOS-only today (it documents bash 3.2 and no `declare -A`), so this was
  # latent rather than live; it is one character class and not worth leaving.
  COLLECTOR_CONFIG_APPLIED="$(mktemp -t collector-config.XXXXXX)"
  trap 'rm -f "$COLLECTOR_CONFIG_APPLIED"' EXIT
  sed "$STAGE4_ONLY_STRIP" "$COLLECTOR_CONFIG" > "$COLLECTOR_CONFIG_APPLIED"
  echo -e "${YELLOW}Stage ${STAGE}: dropping the stage-4-only OpenSearch branch from the collector config (OpenSearch is installed at stage 4 only).${NC}"
fi

kubectl create configmap otel-collector-config \
  --namespace observability \
  --from-file=collector-config.yaml="$COLLECTOR_CONFIG_APPLIED" \
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

if [[ "${STAGE}" -ge 4 ]]; then
  # M4 Task 1: OpenSearch is the destination
  # deploy/observability/collector-config.yaml's `opensearch/trace` exporter
  # was measured against (Task 1 report has the full command transcript);
  # Dashboards is the UI a human reads it through, reached by port-forward
  # (./scripts/start-demo.sh) rather than a kind host port -- see the
  # KIND_CONFIG note above (plan amendment A1).
  #
  # `--wait` on both installs, and then a SECOND collector restart below, and
  # the ordering between them is a real requirement rather than tidiness.
  # Fix round 1 corrected the claim that used to stand here -- "a collector
  # restart before OpenSearch's Service exists would just mean the first
  # export retries until it does". Nothing measured that, and the collector's
  # own message in this cluster is `Exporting failed. Rejecting data. Try
  # enabling sending_queue to survive temporary failures.`, i.e. this exporter
  # has no sending queue to survive anything with. Step 8 above has already
  # restarted the collector by the time this runs, so at stage 4 the restart
  # here is what guarantees the collector's first export attempt happens after
  # OpenSearch is serving, instead of guessing about retry behaviour nobody
  # has measured.
  echo -e "${BLUE}Step 8c/9 (stage 4): Installing OpenSearch ${OPENSEARCH_CHART_VERSION}...${NC}"
  helm repo add opensearch "${OPENSEARCH_HELM_REPO}" --force-update >/dev/null
  helm repo update opensearch >/dev/null
  helm upgrade --install opensearch opensearch/opensearch \
    --version "${OPENSEARCH_CHART_VERSION}" \
    --namespace observability \
    --kube-context "kind-${CLUSTER_NAME}" \
    --values "$ROOT/deploy/observability/opensearch/values.yaml" \
    --wait --timeout 5m
  helm upgrade --install opensearch-dashboards opensearch/opensearch-dashboards \
    --version "${OPENSEARCH_CHART_VERSION}" \
    --namespace observability \
    --kube-context "kind-${CLUSTER_NAME}" \
    --values "$ROOT/deploy/observability/opensearch/dashboards-values.yaml" \
    --wait --timeout 5m

  # See the ordering note above: the collector has already been restarted once,
  # against a cluster with no OpenSearch in it. This restart is the one whose
  # first export attempt can succeed.
  kubectl -n observability rollout restart deployment/otel-collector
  kubectl -n observability rollout status deployment/otel-collector --timeout=180s
  echo -e "${GREEN}OpenSearch ${OPENSEARCH_CHART_VERSION} is up (single node, security plugin off, heap and resources set in deploy/observability/opensearch/values.yaml); Dashboards is up, reached by port-forward; the collector has been restarted against it.${NC}"
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
  # M2.2 Task 4: the Policy Decision Service, deployed BEFORE the gateway that
  # calls it. The ordering is not cosmetic. The gateway's guardrail is
  # `failureMode: failClosed`, so between the gateway coming up and the PDS
  # becoming Ready every `tools/call` is refused -- and refused with the
  # gateway's own `mcpGuardrails checkRequest failed` outage message, not with
  # a policy reason. Nothing between these two blocks drives a settlement, so
  # neither order would leave a real request refused; this order means that if
  # a future step ever does, the window does not exist. It also fails in the
  # right place: a PDS that cannot start stops this script here, at its own
  # `rollout status`, rather than presenting later as a gateway that refuses
  # everything.
  #
  # Built and deployed through rebuild-and-deploy.sh rather than a bare
  # `kubectl apply`, so this component's image is rebuilt from source like
  # every other one (it is registered in that script's COMPONENTS list).
  echo -e "${BLUE}Step 9b/9 (stage 2+): Building and deploying the Policy Decision Service...${NC}"
  "$ROOT/scripts/rebuild-and-deploy.sh" policy-decision-service
  echo -e "${GREEN}policy-decision-service is up on 9100.${NC}"

  echo -e "${BLUE}Step 9c/9 (stage 2+): Installing agentgateway...${NC}"
  kubectl create configmap agentgateway-config \
    --namespace northstar-platform \
    --from-file=config.yaml="$ROOT/deploy/platform/agentgateway/config.yaml" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f "$ROOT/deploy/platform/agentgateway/deployment.yaml"
  # A ConfigMap change is invisible to a running pod here: this config is
  # mounted as a volume, and agentgateway reads it once at startup with `-f`.
  # Without a restart, `kubectl apply` reports the ConfigMap updated and the
  # gateway goes on running M2.1's policy-free config -- which, since the
  # milestone's whole claim is a refusal, would present as the €6,200 being
  # paid out with the committed config and the live ConfigMap in perfect
  # agreement. Restart, then wait.
  kubectl -n northstar-platform rollout restart deployment/agentgateway
  kubectl -n northstar-platform rollout status deployment/agentgateway --timeout=120s
  echo -e "${GREEN}agentgateway is up, calling the PDS on every tools/call (fail-closed).${NC}"
fi

if [[ "${STAGE}" -ge 3 ]]; then
  # M3 Task 5B. Until this block existed, `--stage 3` installed exactly what
  # `--stage 2` installed plus one host-port reservation -- which was true and
  # documented while M3 had no workload, and false from Task 4 onward. The
  # consequence was not cosmetic: README tells a reader to run this script and
  # then run the gate, and the gate checks port 8081 (reviewer-ui) as one of
  # its five prerequisites, so a cold reader following the documented path got
  # a gate that failed on a workload the setup script had never deployed.
  #
  # Two components, and they are deployed differently for a reason that is
  # already recorded in scripts/rebuild-and-deploy.sh's COMPONENTS list:
  #
  #   * reviewer-ui is a genuinely new image with its own build context
  #     (apps/reviewer-ui/Dockerfile), so it goes through rebuild-and-deploy.sh
  #     like every other component -- which also gives it the
  #     apply/restart/rollout-status sequence the blocks above rely on.
  #   * senior-settlement-agent has NO image of its own. It is the
  #     settlement-agent image, unmodified, in a second Deployment with a
  #     second ServiceAccount -- the whole point being that "senior" is an
  #     identity, not a different program (deploy/k8s/senior-settlement-agent.yaml's
  #     own header). So it is a bare `kubectl apply`, and the `rollout status`
  #     is spelled out here rather than inherited.
  #
  # Order: the identity first, the UI second. reviewer-ui's
  # SENIOR_SETTLEMENT_AGENT_URL -- this line named `SENIOR_AGENT_URL` until
  # the M3 final-review fix round, and no such variable exists; the real name
  # is in deploy/k8s/reviewer-ui.yaml and in apps/reviewer-ui/main.py's own
  # `os.environ.get` default --
  # points at senior-settlement-agent's Service, and although nothing between
  # here and the end of this script drives an approval (so neither order would
  # leave a real request failing), this order means the window does not exist
  # if a future step ever does.
  #
  # Nothing else is needed for either: deploy/platform/cilium-policies.yaml,
  # applied at Step 6 above, already carries both workloads' policies (M3 Task
  # 3 added them), and neither needs a ClusterSPIFFEID of its own -- both take
  # their ID from the SPIRE chart's catch-all `default` CR (fallback: true),
  # which derives it from namespace + ServiceAccount. That is why
  # deploy/platform/spire/registration.yaml is unchanged by this stage.
  echo -e "${BLUE}Step 9d/9 (stage 3): Deploying the M3 supervised workloads...${NC}"
  kubectl --context "kind-${CLUSTER_NAME}" apply \
    -f "$ROOT/deploy/k8s/senior-settlement-agent.yaml"
  # `rollout restart` for the same reason rebuild-and-deploy.sh does it, not by
  # imitation: both Deployments name the MUTABLE tag
  # `localhost:5001/northstar/settlement-agent:dev` (read from both manifests,
  # not assumed). On a re-run Step 9 has just pushed a new image under that
  # same tag, and `kubectl apply` over an unchanged manifest is a no-op -- so
  # without this, senior-settlement-agent alone would keep serving the previous
  # build while settlement-agent, which rebuild-and-deploy.sh does restart,
  # served the new one. Two pods of "the same image" silently disagreeing is
  # the worst version of this to debug.
  kubectl --context "kind-${CLUSTER_NAME}" -n northstar-agents \
    rollout restart deployment/senior-settlement-agent
  kubectl --context "kind-${CLUSTER_NAME}" -n northstar-agents \
    rollout status deployment/senior-settlement-agent --timeout=180s
  "$ROOT/scripts/rebuild-and-deploy.sh" reviewer-ui
  echo -e "${GREEN}reviewer-ui is up on 8081 (NodePort, mapped by deploy/kind/stage3.yaml); senior-settlement-agent is up on 9002 in its own Deployment.${NC}"
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
  # `grep -x ... >/dev/null`, not `grep -qx` -- see the identical note in
  # scripts/start-demo.sh::verify_owned_forward. Under `pipefail`, `grep -q`
  # can SIGPIPE the upstream `awk`, making the pipeline exit 141 on a
  # successful match and reporting a healthy forward as "never bound". This
  # instance predates M4; it is fixed here in the same change that fixed the
  # copy in start-demo.sh, because leaving one of two identical constructs
  # broken is how it comes back.
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -x "${pid}" >/dev/null; then
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
if [[ "${STAGE}" -eq 4 ]]; then
  echo -e "${GREEN}Stage 4 (Observable) is up: stage 3's workloads (reviewer-ui, senior-settlement-agent) on the same Cilium ${CILIUM_CHART_VERSION} / SPIRE ${SPIRE_CHART_VERSION} cluster -- amendment A1, no cluster recreate -- plus OpenSearch ${OPENSEARCH_CHART_VERSION} holding this system's traces and OpenSearch Dashboards for reading them. Run ./scripts/start-demo.sh to reach Dashboards; it is not a kind host port.${NC}"
elif [[ "${STAGE}" -eq 3 ]]; then
  echo -e "${GREEN}Stage 3 (Supervised) is up: stage 1's workloads on a Cilium ${CILIUM_CHART_VERSION} dataplane, with SPIRE ${SPIRE_CHART_VERSION} issuing workload identity, plus reviewer-ui and senior-settlement-agent -- so a refused payment now has a human who can approve it.${NC}"
elif [[ "${STAGE}" -ge 2 ]]; then
  echo -e "${GREEN}Stage 2 (Governed) is up: stage 1's workloads on a Cilium ${CILIUM_CHART_VERSION} dataplane, with SPIRE ${SPIRE_CHART_VERSION} issuing workload identity.${NC}"
else
  echo -e "${GREEN}Stage 1 (Connected) is up.${NC}"
fi
echo "  Customer UI:         http://localhost:8080"
if [[ "${STAGE}" -ge 3 ]]; then
  # Stage 3 and above: the host port is reserved by deploy/kind/stage3.yaml,
  # which stage 4 also runs on (amendment A1), so both stages answer here. On
  # a stage-1 or stage-2 cluster this URL answers nothing and printing it
  # would be the same class of false instruction this block's sibling in
  # scripts/smoke-test.sh checks for. (This comment read "Only at stage 3"
  # until M4 Task 1's fix round -- true while 3 was the maximum, and the
  # branch condition was `-ge 3` even then.)
  echo "  Reviewer UI:         http://localhost:8081"
fi
echo "  Jaeger UI:           http://localhost:16686"
echo "  Collector OTLP/HTTP: http://localhost:4318"
echo "  claims-agent (A2A):  http://localhost:19001"
echo "  Local registry:      localhost:${REGISTRY_PORT}"
echo ""
echo "Next: ./scripts/smoke-test.sh"
