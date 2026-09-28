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
# SPIRE. Two charts, and the ORDER MATTERS -- `spiffe/spire`
# installed on its own fails on the missing spire.spiffe.io CRDs, which its own
# bundled spire-controller-manager needs before it can reconcile a single
# ClusterSPIFFEID. The hardened charts repo ships them separately for exactly
# that reason (CRDs outlive releases; Helm will not upgrade them in place).
#
# This is the HARDENED charts repo, https://spiffe.github.io/helm-charts-hardened/
# -- not the older spiffe/helm-charts location, which does not carry these
# charts at all. Resolved from `helm search repo spiffe/spire --versions` and
# cross-checked against the upstream releases API: the repo tags PER CHART
# (`spire-0.30.2` and `spire-crds-0.6.1` are independent tag series), so these
# two numbers are unrelated to each other. `spire` 0.30.2's appVersion is SPIRE
# 1.15.3. Both versions are also recorded in
# deploy/platform/spire/values.yaml's header and pinned by
# tests/unit/test_stage2_spire_config.py so the two cannot drift.
SPIRE_CRDS_CHART_VERSION="0.6.1"
SPIRE_CHART_VERSION="0.30.2"
SPIFFE_HELM_REPO="https://spiffe.github.io/helm-charts-hardened/"
# OpenSearch. Verified against TWO independent sources, not a local Helm
# cache -- Artifact Hub's opensearch-project-helm-charts/opensearch package
# (version AND app_version both "3.8.0") and
# https://api.github.com/repos/opensearch-project/helm-charts/releases (tag
# opensearch-3.8.0, prerelease: false, the newest entry). Unlike Cilium/SPIRE
# this chart's tags equal its appVersion. The same number and the same
# evidence are recorded in deploy/observability/opensearch/values.yaml and
# dashboards-values.yaml's headers.
OPENSEARCH_CHART_VERSION="3.8.0"
OPENSEARCH_HELM_REPO="https://opensearch-project.github.io/helm-charts"
# Prometheus. Verified against TWO independent sources, not a local Helm
# cache -- Artifact Hub's prometheus-community/prometheus package (version
# "29.29.0", app_version "v3.14.0", prerelease false) and
# https://api.github.com/repos/prometheus-community/helm-charts/releases
# (tag prometheus-29.29.0, prerelease false, the newest `prometheus-*` entry;
# that repo tags per chart, so the many `prometheus-<exporter>-*` tags
# alongside it are unrelated series). Unlike the OpenSearch chart, chart
# version and appVersion are independent here: 29.29.0 installs Prometheus
# v3.14.0. The same numbers and the same evidence are recorded in
# deploy/observability/prometheus/values.yaml's header, and pinned by
# tests/unit/test_metrics_wiring.py so the two cannot drift.
PROMETHEUS_CHART_VERSION="29.29.0"
PROMETHEUS_HELM_REPO="https://prometheus-community.github.io/helm-charts"
# cert-manager and the Open Feature Operator (OFO). Both verified against the
# upstream releases API directly -- `curl -s
# https://api.github.com/repos/<org>/<repo>/releases/latest | grep tag_name`
# -- not against OFO's own installation docs, which can lag several minor
# releases behind on cert-manager (see docs/installation.md in the
# open-feature-operator repo). cert-manager's latest release is `v1.21.2`;
# OFO's is `v0.9.3`, chart version literally tagged with the "v" prefix
# (confirmed with `helm search repo openfeature/open-feature-operator
# --versions`, unlike every other chart pinned in this file).
#
# flagd itself is not installed by a line in this script -- OFO's chart
# injects it as a sidecar, at whatever image tag its own values pin. The
# newest flagd release is `v0.16.3` (`flagd/v0.16.3` in that repo's release
# tags, which mix flagd, flagd-proxy and core under one series -- filter on
# the `flagd/` prefix specifically). OFO v0.9.3's chart defaults
# `sidecarConfiguration.image.tag` to `v0.16.2`, one patch behind (`helm show
# values openfeature/open-feature-operator --version v0.9.3`) -- recorded
# here rather than silently overridden, since this script pins the OFO chart
# version, not the sidecar image inside it.
CERT_MANAGER_VERSION="1.21.2"
OPENFEATURE_CHART_VERSION="v0.9.3"
OPENFEATURE_HELM_REPO="https://open-feature.github.io/open-feature-operator/"
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
                          the Policy Decision Service. The gateway sits in
                          the MCP path, verifies a SPIRE-issued JWT-SVID on
                          every MCP call, and asks the PDS for a permit/deny
                          on every tools/call -- FAIL-CLOSED, so a PDS outage
                          refuses payments rather than waving them through.
                          cert-manager is NOT installed and is not needed:
                          the pinned SPIRE chart only uses it for optional
                          features this install does not enable -- see
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
                          workloads]
  --stage 4   Observable:           + OpenSearch, Prometheus, eval-scorer
                          [OpenSearch (single node, security plugin off) and
                          OpenSearch Dashboards are installed, and the
                          trace-ingest path into OpenSearch is live.
                          Prometheus is installed too, OTLP-ingest only:
                          every default scrape job is off, so the only way a
                          series reaches it is the collector's
                          otlp_http/prometheus exporter. The evidence view is
                          imported into Dashboards from
                          deploy/observability/dashboards/evidence.ndjson, so
                          it survives a cluster recreate instead of living
                          only in a pod's volume. eval-scorer is deployed
                          here too -- the service that scores a settlement's
                          trajectory against the five policy-adherence
                          properties. Runs on stage 3's cluster config
                          UNCHANGED -- see the KIND_CONFIG note below --
                          Dashboards is reached by port-forward
                          (./scripts/start-demo.sh), never by a kind host
                          port]
  --stage 5   Progressive autonomy: + flagd
                          [cert-manager (a prerequisite the Open Feature
                          Operator's webhooks need for TLS, not used by
                          anything else in this cluster) and the Open
                          Feature Operator are installed, then the
                          FeatureFlag/FeatureFlagSource pair in
                          deploy/platform/openfeature/ is applied. The
                          operator's mutating webhook injects a flagd sidecar
                          into the Policy Decision Service pod (its pod
                          template in deploy/k8s/policy-decision-service.yaml
                          carries the openfeature.dev/* annotations that
                          trigger it), answering on localhost:8013 inside
                          that pod. Runs on stage 3's cluster config
                          UNCHANGED -- see the KIND_CONFIG note below -- no
                          host port is needed, the sidecar is reached only
                          from inside its own pod]
  --stage 6   Golden path:          + Backstage
                          [Backstage is deployed in `northstar-platform`
                          (deploy/k8s/backstage.yaml), built and pushed the
                          same way every other component's image is, on
                          SQLite rather than PostgreSQL (this cluster is torn
                          down and rebuilt, not operated, and the catalog is
                          re-read from committed catalog-info.yaml files on
                          every start regardless). Its ServiceAccount is
                          READ-ONLY (`get`/`list`/`watch` on Pods,
                          Deployments, ReplicaSets, Services, ConfigMaps and
                          the other object types the Kubernetes plugin reads
                          by default; no Secrets, no write verbs anywhere) --
                          a SEPARATE ServiceAccount carries the one grant
                          that can PATCH the `northstar-autonomy`
                          FeatureFlag, deliberately, so the two grants stay
                          independently reviewable. Reached by port-forward
                          (./scripts/start-demo.sh), never a kind host port,
                          the same convention every platform component here
                          follows. Runs on stage 3's cluster config UNCHANGED
                          -- see the KIND_CONFIG note below -- nothing this
                          stage installs needs a host port]

Each stage installs only what that stage of the demo needs, so the laptop
runs exactly one stage at a time.

Stages 2, 3, 4 and 5 all need `networking.disableDefaultCNI`, a create-time
kind flag: it cannot be applied to a cluster that already exists, so the
cluster must be created FROM a kind config that sets it -- deploy/kind/
stage2.yaml or deploy/kind/stage3.yaml (whose only difference from stage 2's
is the reviewer-ui port reservation; stages 4 and 5 both reuse stage 3's, see
below) -- and this script refuses rather than layering Cilium on a running
default CNI. Note that --stage defaults to 1, so a bare
`./scripts/setup-kind.sh` builds a stage-1 (kindnet) cluster -- pass --stage
2, 3, 4 or 5 explicitly for the Cilium dataplane.

Stage 4 is the first exception to "each stage brings its own deploy/kind/
stageN.yaml": there is deliberately no stage4.yaml. OpenSearch Dashboards
would need a fifth host port (extraPortMappings is create-time only, same
rule as above), and this cluster is not recreated just to add it -- so
stage 4 reuses stage 3's cluster config unchanged and reaches Dashboards by
port-forward instead. tests/unit/test_stage4_config.py pins that no
stage4.yaml appears.

Stage 5 is the second: there is deliberately no stage5.yaml either. Nothing
it installs (cert-manager, the Open Feature Operator, the flagd sidecar)
needs a host port -- the sidecar answers only inside its own pod, at
localhost:8013 -- so stage 5 reuses stage 3's cluster config unchanged too,
the same reasoning stage 4 already established. A cluster already brought up
as `--stage 4` and then re-run as `--stage 5` takes the "cluster already
exists, skipping creation" path below and never re-evaluates KIND_CONFIG for
cluster creation at all -- the assignment below only has to be right for a
cold `--stage 5` bring-up.
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

if [[ "${STAGE}" -gt 6 ]]; then
  echo "Stage $STAGE is not implemented yet. Stages 1 (Connected), 2 (Governed: Cilium), 3 (Supervised), 4 (Observable), 5 (Progressive autonomy) and 6 (Golden path) are implemented." >&2
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
# A container can exist but be stopped (e.g. after a `docker system prune`
# or a host restart with restart policies not yet applied) without `docker
# inspect -f '{{.State.Running}}'` ever being 'true'. Running `docker run
# --name kind-registry` whenever it isn't running would fail on the name
# collision with an existing-but-stopped container and kill this script
# under `set -e` — a false "the registry is broken" failure when `docker
# start` would fix it in one step. So "does not exist" is distinguished from
# "exists but stopped", and `docker run` is only ever used in the former
# case.
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
# There is no stage4.yaml, deliberately. A fifth host port mapping (for
# OpenSearch Dashboards) can only be added at cluster CREATION -- same
# create-time rule as disableDefaultCNI above -- and this cluster is not
# recreated just to add it. So --stage 4 reuses stage 3's cluster config
# unchanged; Dashboards is reached by port-forward (./scripts/start-demo.sh)
# instead of a host port.
#
# There is no stage5.yaml either, for the same reasoning -- nothing this
# stage installs (cert-manager, the Open Feature Operator, the flagd
# sidecar) needs a host port, so it reuses stage 3's config too.
#
# There is no stage6.yaml either, measured rather than assumed. Backstage is
# reached by port-forward (./scripts/start-demo.sh), the same convention
# every platform component in this cluster already follows, so it needs no
# `extraPortMappings` entry of its own -- and that entry is the only thing a
# create-time kind config buys here (same as stages 4 and 5). Written as
# `-eq 4 || -eq 5 || -eq 6` rather than `-ge 4`, still: whether a stage past
# 6 needs a new host port has not been measured, so this does not silently
# decide it either.
if [[ "${STAGE}" -eq 4 || "${STAGE}" -eq 5 || "${STAGE}" -eq 6 ]]; then
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
  # deploy/k8s/namespaces.yaml creates four namespaces — observability,
  # northstar-agents, northstar-tools, northstar-systems — and no
  # `northstar-platform`. It is NOT added to that shared file, because that
  # file is stage 1's and stage 1 has nothing to put in this namespace;
  # creating it here keeps stage 1 unchanged.
  kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: northstar-platform
EOF

  # The network policy that makes the choke point real rather than
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

# settlement-agent's decision is a real model call, so it needs a model
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

# Below stage 4 the collector must not name a backend this cluster does not
# have.
#
# OpenSearch is installed only at `-ge 4` (Step 8c below), but the collector
# config is chosen on DASH0_AUTH_TOKEN and never on stage -- so without a
# strip, `--stage 1|2|3` would deploy a collector whose traces pipeline names
# `opensearch/trace`. Jaeger would still receive everything (the fanout is
# per-exporter), so this would degrade to permanent export failures logged
# into a file nobody reads, which is precisely the silently-broken shape this
# repo exists to avoid.
#
# The rule is one line of sed and one marker convention, not a YAML rewriter:
# every region of both collector configs that is stage-4-only is bracketed by
# `# BEGIN stage-4-only` / `# END stage-4-only`. Two backends share the
# convention, and it is named after the stage rather than after either one
# for exactly that reason:
#   * traces -- the `opensearch/trace` exporter, the
#     `attributes/opensearch-compat` processor, the `forward/opensearch`
#     connector, the `traces/opensearch` branch pipeline, and the single
#     `- forward/opensearch` entry in the traces pipeline's exporter list;
#   * metrics -- the `otlp_http/prometheus` exporter and its entry in the
#     metrics pipeline's exporter list.
# Both entries are why those exporter lists are written one-per-line. Deleting
# the bracketed regions yields the exact below-stage-4 config -- verified by
# round-tripping both files through PyYAML.
#
# tests/unit/test_stage4_config.py runs this same sed program (it reads the
# expression out of this file, so the two cannot drift) and asserts, per
# signal, that the result parses, still exports traces to Jaeger and metrics to
# debug, keeps the `prometheus/spire` receiver (which is NOT stage-4-only), and
# names neither `opensearch` nor `otlp_http/prometheus` anywhere. It also
# asserts that the `echo` below names every backend the strip actually removes,
# derived by diffing the two configs rather than restated -- which is what
# turns the next third region into a red test instead of a stale sentence.
STAGE4_ONLY_STRIP='/^[[:space:]]*# BEGIN stage-4-only$/,/^[[:space:]]*# END stage-4-only$/d'
COLLECTOR_CONFIG_APPLIED="$COLLECTOR_CONFIG"
if [[ "${STAGE}" -lt 4 ]]; then
  # `.XXXXXX`, not a bare prefix. BSD/macOS `mktemp -t` appends its own
  # random suffix to whatever prefix it is given, so a bare `collector-config`
  # works here -- but GNU coreutils `mktemp -t` requires the template to end
  # in at least three X and exits `too few X's in template`, which under
  # `set -e` would kill every `--stage 1|2|3` run at this line on Linux. The
  # explicit template works on both, at no cost even though this repo is
  # macOS-only today (it documents bash 3.2 and no `declare -A`).
  COLLECTOR_CONFIG_APPLIED="$(mktemp -t collector-config.XXXXXX)"
  trap 'rm -f "$COLLECTOR_CONFIG_APPLIED"' EXIT
  sed "$STAGE4_ONLY_STRIP" "$COLLECTOR_CONFIG" > "$COLLECTOR_CONFIG_APPLIED"
  echo -e "${YELLOW}Stage ${STAGE}: dropping every stage-4-only region from the collector config -- the OpenSearch trace branch and the Prometheus metrics exporter (both backends are installed at stage 4 only).${NC}"
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
  # OpenSearch is the destination
  # deploy/observability/collector-config.yaml's `opensearch/trace` exporter
  # writes to; Dashboards is the UI a human reads it through, reached by
  # port-forward (./scripts/start-demo.sh) rather than a kind host port --
  # see the KIND_CONFIG note above.
  #
  # `--wait` on both installs, and then a SECOND collector restart below, and
  # the ordering between them is a real requirement rather than tidiness. A
  # collector restart before OpenSearch's Service exists would NOT just mean
  # the first export retries until it does: the collector's own message in
  # this cluster is `Exporting failed. Rejecting data. Try enabling
  # sending_queue to survive temporary failures.`, i.e. this exporter has no
  # sending queue to survive anything with. Step 8 above has already
  # restarted the collector by the time this runs, so at stage 4 the restart
  # here is what guarantees the collector's first export attempt happens after
  # OpenSearch is serving, instead of relying on retry behaviour this exporter
  # does not have.
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

  # The evidence view, imported rather than hand-made.
  #
  # The audit trail already exists in OpenSearch; this is what makes it
  # queryable through a saved view -- so the view that reads it has to be
  # reproducible too. A dashboard that lives only in the Dashboards pod's
  # volume is gone at the next `kind delete cluster`, and "we had one, you had
  # to be there" is not evidence of anything.
  #
  # Done by `kubectl exec` into the Dashboards pod rather than through a
  # port-forward: the image ships curl, the API is on localhost:5601 inside
  # the pod, and this repo's standing rule is that `scripts/start-demo.sh`
  # owns the demo's forwards. A forward opened and closed inside this block
  # would be a fifth thing that can leave a socket behind for no gain.
  #
  # **`--wait` is NOT enough on its own.** The chart's readiness probe is not
  # Dashboards' own status endpoint: measured on the deployed object
  # (`kubectl get deploy opensearch-dashboards -o jsonpath=
  # '{.spec.template.spec.containers[0].readinessProbe}'`), it is
  # `{"tcpSocket":{"port":5601},"initialDelaySeconds":10,"periodSeconds":20,
  #   "timeoutSeconds":5,"failureThreshold":10}` -- a TCP accept, and
  # dashboards-values.yaml overrides neither probe. A socket accepting is not
  # the saved-objects API serving: OSD answers
  # `503 {"statusCode":503,"error":"Service Unavailable",
  # "message":"OpenSearch Dashboards server is not ready yet"}` for a window
  # after the bind.
  #
  # So the readiness this block needs is polled for directly, against the API
  # that actually receives the import. `/api/status` is what the OSD UI itself
  # polls and it returns 200 only once the server is past "not ready yet".
  # Without this the import would run once and `exit 1` on a 503, aborting
  # `--stage 4` at step 8c2 -- before Prometheus is installed and before the
  # collector restart below -- leaving a half-built cluster.
  echo -e "${BLUE}Step 8c2/9 (stage 4): Importing the evidence view into OpenSearch Dashboards...${NC}"
  DASHBOARDS_READY=""
  for _ in $(seq 1 60); do
    if kubectl --context "kind-${CLUSTER_NAME}" -n observability \
        exec deploy/opensearch-dashboards -- \
        curl -sf -o /dev/null "http://localhost:5601/api/status" 2>/dev/null; then
      DASHBOARDS_READY="yes"
      break
    fi
    sleep 3
  done
  if [[ -z "${DASHBOARDS_READY}" ]]; then
    echo -e "${RED}OpenSearch Dashboards' /api/status never answered 200 within 180s, so the evidence view cannot be imported.${NC}" >&2
    echo "  kubectl -n observability logs deploy/opensearch-dashboards" >&2
    exit 1
  fi

  # curl exits 0 on an HTTP 400, so the response is checked rather than the
  # exit code -- an import that silently failed would leave the operator with
  # a cluster that looks complete and a Dashboards with nothing in it, which is
  # this milestone's own favourite failure mode.
  EVIDENCE_IMPORT=$(kubectl --context "kind-${CLUSTER_NAME}" -n observability \
    exec -i deploy/opensearch-dashboards -- \
    curl -sS -X POST "http://localhost:5601/api/saved_objects/_import?overwrite=true" \
    -H 'osd-xsrf: true' -F 'file=@-;filename=evidence.ndjson' \
    < "$ROOT/deploy/observability/dashboards/evidence.ndjson")
  if [[ "${EVIDENCE_IMPORT}" != *'"success":true'* ]]; then
    echo -e "${RED}Importing deploy/observability/dashboards/evidence.ndjson failed.${NC}" >&2
    echo "${EVIDENCE_IMPORT}" >&2
    exit 1
  fi
  echo -e "${GREEN}Evidence view imported. Open it with ./scripts/start-demo.sh and browse to http://localhost:5601/app/dashboards#/view/northstar-evidence${NC}"

  # Prometheus, installed in the SAME stage-4 block and BEFORE the
  # collector restart below, for exactly the reason the OpenSearch install is.
  # `otlp_http/prometheus` is a plain otlphttp exporter with no sending queue
  # configured, so its first export attempt after a restart either lands or is
  # dropped -- restarting the collector while this Service does not yet exist
  # would throw the first minute of every service's metrics away and report it
  # only in a log nobody reads. `--wait` is what makes "exists" mean "serving".
  echo -e "${BLUE}Step 8d/9 (stage 4): Installing Prometheus ${PROMETHEUS_CHART_VERSION} (OTLP ingest, no scrape jobs)...${NC}"
  helm repo add prometheus-community "${PROMETHEUS_HELM_REPO}" --force-update >/dev/null
  helm repo update prometheus-community >/dev/null
  helm upgrade --install prometheus prometheus-community/prometheus \
    --version "${PROMETHEUS_CHART_VERSION}" \
    --namespace observability \
    --kube-context "kind-${CLUSTER_NAME}" \
    --values "$ROOT/deploy/observability/prometheus/values.yaml" \
    --wait --timeout 5m

  # See the ordering note above: the collector has already been restarted once,
  # against a cluster with neither OpenSearch nor Prometheus in it. This
  # restart is the one whose first export attempt can succeed.
  kubectl -n observability rollout restart deployment/otel-collector
  kubectl -n observability rollout status deployment/otel-collector --timeout=180s
  echo -e "${GREEN}OpenSearch ${OPENSEARCH_CHART_VERSION} is up (single node, security plugin off, heap and resources set in deploy/observability/opensearch/values.yaml); Dashboards is up, reached by port-forward, with the evidence view imported into it from deploy/observability/dashboards/evidence.ndjson; Prometheus ${PROMETHEUS_CHART_VERSION} is up on OTLP ingest alone; the collector has been restarted against both.${NC}"

  # eval-scorer, the component that scores a settlement's
  # trajectory against the five policy-adherence properties.
  #
  # Deployed HERE, at the end of the stage-4 block, rather than after Step 9
  # with the other workloads. Both of the things it needs were made ready in
  # the lines directly above -- the OpenSearch it reads trajectories out of,
  # and the collector it emits score spans into -- and nothing between here
  # and the end of this script asks it for a score, so neither order would
  # leave a real request failing. This order means the window does not exist
  # if a future step ever does. What it needs from the earlier steps (the kind
  # registry, Step 1; the `northstar-platform` namespace, Step 6) has existed
  # since long before this block.
  #
  # Its SPIFFE ID comes from an explicit ClusterSPIFFEID in
  # deploy/platform/spire/registration.yaml, applied by the stage-2 block
  # above and so already in place by the time this runs.
  #
  # **Not** from the SPIRE chart's catch-all `default` CR. That CR's
  # namespaceSelector excludes `northstar-platform` by name, which makes
  # reviewer-ui and senior-settlement-agent (both in `northstar-agents`) the
  # wrong precedent for anything deployed here: a platform workload with no CR
  # of its own gets no identity at all and nothing says so -- verified against
  # the live cluster with `spire-server entry show` rather than assumed.
  #
  # deploy/platform/cilium-policies.yaml, on the other hand, names no
  # rule selecting it, so -- in Cilium, where policy is per-endpoint and an
  # endpoint no rule selects is unrestricted -- it reaches OpenSearch and the
  # collector without one. That is a known posture gap, not an oversight:
  # writing an egress allow-list makes the endpoint default-deny, which is the
  # hazard that file's own header names twice, and it is the kind of change
  # that cannot be safely verified without recreating the cluster.
  echo -e "${BLUE}Step 8e/9 (stage 4): Building and deploying eval-scorer...${NC}"
  "$ROOT/scripts/rebuild-and-deploy.sh" eval-scorer
  echo -e "${GREEN}eval-scorer is up on 8300, reading ss4o_traces-default-namespace.${NC}"
fi

if [[ "${STAGE}" -ge 5 ]]; then
  # The machinery that turns the autonomy level from a constant
  # compiled into the Policy Decision Service into a contract living in a
  # Kubernetes resource. This block installs the machinery only -- cert-manager,
  # the Open Feature Operator (OFO), and the two CRs; making the PDS read the
  # flag is application code, not installation.
  #
  # Ordered, and deliberately run here rather than folded into the `-ge 2`
  # block above: it must finish before Step 9b below builds and deploys the
  # PDS, because that pod's template (deploy/k8s/policy-decision-service.yaml)
  # now carries the openfeature.dev/* annotations OFO's mutating webhook reads
  # at admission time. A PDS pod created before that webhook exists gets no
  # sidecar injected and no error either -- an annotation a webhook does not
  # yet exist to act on is just metadata -- so the ordering is load-bearing in
  # the same "wrong order fails silently" way Cilium-before-workloads and
  # SPIRE-before-workloads already are above.
  echo -e "${BLUE}Step 8f/9 (stage 5): Installing cert-manager ${CERT_MANAGER_VERSION}...${NC}"
  # cert-manager itself needs no `--kube-context`: this releases's own
  # manifest is namespace-scoped (it creates `cert-manager` and everything in
  # it), and `kubectl apply -f <url>` runs against the current context, which
  # is `kind-${CLUSTER_NAME}` immediately after `kind create cluster` /
  # `kubectl cluster-info` above set it. Every other command in this block
  # names the context explicitly, the same as the rest of this script, because
  # `kubectl apply -f <url>` is the one command shape here that has no
  # `--context` flag to give it.
  kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/v${CERT_MANAGER_VERSION}/cert-manager.yaml"
  kubectl --context "kind-${CLUSTER_NAME}" wait --for=condition=Available=True deploy --all -n cert-manager --timeout=300s
  echo -e "${GREEN}cert-manager ${CERT_MANAGER_VERSION} is ready.${NC}"

  echo -e "${BLUE}Step 8g/9 (stage 5): Installing the Open Feature Operator ${OPENFEATURE_CHART_VERSION}...${NC}"
  # No `--namespace` flag: this chart hardcodes its own resources into
  # `open-feature-operator-system` regardless of what namespace the release
  # metadata is recorded under (verified with `helm template
  # openfeature/open-feature-operator --version v0.9.3 --namespace
  # <anything>` -- the rendered Namespace object and every Deployment's
  # `namespace:` stay `open-feature-operator-system` either way), so passing
  # one would only be decorative.
  helm repo add openfeature "${OPENFEATURE_HELM_REPO}" --force-update >/dev/null
  helm repo update openfeature >/dev/null
  helm upgrade --install openfeature openfeature/open-feature-operator \
    --version "${OPENFEATURE_CHART_VERSION}" \
    --kube-context "kind-${CLUSTER_NAME}" \
    --wait --timeout 5m
  echo -e "${GREEN}Open Feature Operator ${OPENFEATURE_CHART_VERSION} is ready.${NC}"

  echo -e "${BLUE}Step 8h/9 (stage 5): Applying the autonomy contract (FeatureFlag + FeatureFlagSource)...${NC}"
  kubectl --context "kind-${CLUSTER_NAME}" apply -f "$ROOT/deploy/platform/openfeature/"
  echo -e "${GREEN}northstar-autonomy / northstar-autonomy-source applied -- flagd will be injected into any pod carrying the openfeature.dev/* annotations, PDS included, when it is (re)created below.${NC}"
fi

if [[ "${STAGE}" -ge 6 ]]; then
  # Backstage, the view over the platform API. Built and deployed through
  # rebuild-and-deploy.sh rather than a bare `kubectl apply`, so this
  # component's image is rebuilt
  # from source like every other one (it is registered in that script's
  # COMPONENTS list) -- deploy/k8s/backstage.yaml is a single manifest
  # carrying its ServiceAccount, its read-only ClusterRole/ClusterRoleBinding,
  # its Deployment and its Service, applied together the same way
  # eval-scorer's own manifest is.
  #
  # Deployed here, in its own stage-6 block, rather than folded into Step 9
  # below: Step 9 is stage-1's own block (it names its five components by
  # hand, unconditionally), and every later stage's addition -- the PDS at
  # `-ge 2`, the stage-3 workloads at `-ge 3`, eval-scorer inside the `-ge 4`
  # block above -- gets its own guarded block for the same reason. Nothing
  # here needs anything Step 9 provides that is not already true by
  # `-ge 6` (`northstar-platform` has existed since `-ge 2`), so there is no
  # ordering constraint forcing this before or after it; it is placed here,
  # with the rest of this stage's machinery, for that reason alone.
  echo -e "${BLUE}Step 8i/9 (stage 6): Building and deploying Backstage...${NC}"
  "$ROOT/scripts/rebuild-and-deploy.sh" backstage

  # `rollout status` above only proves the pod is READY -- the readiness
  # probe hits Backstage's own health endpoint, not this plugin. The
  # writer-token Secret's `token` key is populated ASYNCHRONOUSLY by the
  # token controller, and its retry (index.ts) has a bounded backoff, so a
  # cold bring-up that mounts before the key lands still degrades the
  # autonomy-contract plugin to a permanent 503 on every route -- silently:
  # `rollout status` succeeds, `set -e` is satisfied, and this script would
  # otherwise print a GREEN banner claiming a card that can never actually
  # write. A backend health signal is not a delivery check, so this checks
  # delivery directly rather than trusting pod readiness for it.
  #
  # Checked with `kubectl exec` + node's own `http` module, not curl: the
  # final image stage installs no curl (unlike the OpenSearch Dashboards
  # image Step 8c2 above execs into), and this is a one-shot in-pod check,
  # not a persistent forward -- `scripts/start-demo.sh` still owns the
  # demo's own port-forwards. A guest token is required first
  # (`/api/autonomy-contract/status` answers 401 unauthenticated either way,
  # by design, so an unauthenticated probe could not tell a healthy router
  # from a degraded one); only the authenticated response can be 200 or 503.
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
    echo "This is the degraded-router / writer-credential-race failure mode: the pod is Ready (its own health endpoint, not this plugin) but the card cannot write. Check: kubectl --context kind-${CLUSTER_NAME} -n northstar-platform logs deploy/backstage | grep 'autonomy-contract'" >&2
    exit 1
  fi
  echo -e "${GREEN}Backstage is up on 7007 with its read-only ServiceAccount and its separate write ServiceAccount (backstage-autonomy-writer); the autonomy-contract card answered 200 as a guest, so the writer credential is live, not merely the pod.${NC}"
fi

echo -e "${BLUE}Step 9/9: Building and deploying stage 1 workloads, then starting port-forwards...${NC}"
"$ROOT/scripts/rebuild-and-deploy.sh" \
  payments-api payments-mcp settlement-agent claims-agent customer-ui

if [[ "${STAGE}" -ge 2 ]]; then
  # agentgateway sits IN the MCP path -- settlement-agent's PAYMENTS_MCP_URL
  # names it (deploy/k8s/settlement-agent.yaml) -- and enforces the policy
  # decision below; on its own it carries no policy.
  #
  # Still applied after the workloads it fronts, so `payments-mcp` already
  # exists when the gateway starts proxying to it. This does not need to move
  # *above* Step 9 (settlement-agent is deployed pointing at a gateway that
  # does not exist yet), deliberately: the pod's readinessProbe is its agent
  # card, not an MCP call, and nothing between here and the `rollout status`
  # wait below drives a settlement -- so the gateway is up before any request
  # can reach it. If a future step ever drives traffic between those two
  # points, this block has to move.
  # The Policy Decision Service, deployed BEFORE the gateway that
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
  # gateway goes on running its previous config -- which would present as a
  # payment being paid out with the committed config and the live ConfigMap
  # in perfect agreement, while the running pod enforces neither. Restart,
  # then wait.
  kubectl -n northstar-platform rollout restart deployment/agentgateway
  kubectl -n northstar-platform rollout status deployment/agentgateway --timeout=120s
  echo -e "${GREEN}agentgateway is up, calling the PDS on every tools/call (fail-closed).${NC}"
fi

if [[ "${STAGE}" -ge 3 ]]; then
  # Without this block, `--stage 3` would install exactly what `--stage 2`
  # installs plus one host-port reservation, with no actual workload behind
  # it -- and the consequence is not cosmetic: the gate checks port 8081
  # (reviewer-ui) as one of its five prerequisites, so a reader following
  # README's documented path would get a gate that fails on a workload the
  # setup script never deployed.
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
  # SENIOR_SETTLEMENT_AGENT_URL -- the real name is in
  # deploy/k8s/reviewer-ui.yaml and in apps/reviewer-ui/main.py's own
  # `os.environ.get` default -- points at senior-settlement-agent's Service,
  # and although nothing between here and the end of this script drives an
  # approval (so neither order would leave a real request failing), this
  # order means the window does not exist if a future step ever does.
  #
  # Nothing else is needed for either: deploy/platform/cilium-policies.yaml,
  # applied at Step 6 above, already carries both workloads' policies, and
  # neither needs a ClusterSPIFFEID of its own -- both take their ID from the
  # SPIRE chart's catch-all `default` CR (fallback: true), which derives it
  # from namespace + ServiceAccount. That is why
  # deploy/platform/spire/registration.yaml is unchanged by this stage.
  echo -e "${BLUE}Step 9d/9 (stage 3): Deploying the supervised workloads...${NC}"
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

# A stray port-forward from another cluster on this machine (e.g.
# `capybara`) can silently shadow a host port. This only ever kills forwards
# matching this repo's own pattern, never blanket-kills another cluster's
# forwards — see scripts/teardown.sh for the same rule.
#
# Two patterns, not one: this repo's own commands read "kubectl -n
# northstar-<ns> port-forward ..." ("northstar" before "port-forward"), while
# a hand-typed "kubectl port-forward -n northstar-agents ..." puts the two
# words in the other order. Two calls, one per order, cover both without
# needing alternation support in pkill's regex engine (unavailable/unreliable
# across BSD and GNU pkill). Verified directly against a live forward: both
# call orders correctly kill this repo's forwards regardless of which way
# they were typed, and neither ever matches a `capybara`-cluster forward
# (which contains no "northstar" substring in either position).
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

# Don't just launch the forwards and declare success — a stray forward from
# another cluster can silently shadow one of these host ports (see
# apps/customer-ui/NOTES.md §8). Verify each port is actually bound by the
# PID we just started, not merely that something is listening there.
verify_port_forward() {
  local port="$1" pid="$2" name="$3"
  # `grep -x ... >/dev/null`, not `grep -qx` -- see the identical note in
  # scripts/start-demo.sh::verify_owned_forward. Under `pipefail`, `grep -q`
  # can SIGPIPE the upstream `awk`, making the pipeline exit 141 on a
  # successful match and reporting a healthy forward as "never bound". Fixed
  # here in the same shape as the copy in start-demo.sh, since leaving one of
  # two identical constructs broken is how it comes back.
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -x "${pid}" >/dev/null; then
    echo -e "${RED}Error: the port-forward for ${name} (expected PID ${pid} listening on ${port}) never bound.${NC}" >&2
    echo "Another process may already hold that port — check: lsof -nP -iTCP:${port} -sTCP:LISTEN" >&2
    exit 1
  fi
}
verify_port_forward 19001 "${CLAIMS_PF_PID}" claims-agent
verify_port_forward 19002 "${SETTLEMENT_PF_PID}" settlement-agent
verify_port_forward 18000 "${MCP_PF_PID}" payments-mcp
verify_port_forward 18080 "${API_PF_PID}" payments-api
echo -e "${GREEN}Port-forwards verified: claims-agent, settlement-agent, payments-mcp, payments-api.${NC}"

echo ""
if [[ "${STAGE}" -eq 6 ]]; then
  echo -e "${GREEN}Stage 6 (Golden path) is up: stage 5's workloads and backends, unchanged, plus Backstage in northstar-platform on SQLite. Its read-only ServiceAccount is wired to the Kubernetes plugin (get/list/watch on Pods, Deployments, ReplicaSets, Services, ConfigMaps and the other default object types; no Secrets, no write verbs) and its own, separate ServiceAccount carries the one narrow write grant (get/list/patch, resourceNames: [northstar-autonomy], no delete -- deploy/k8s/backstage.yaml). The catalog covers this repo's own eight components (agent cards linked, not restated) plus Backstage itself, each showing its live workload; the autonomy card reads northstar-autonomy live, per identity, and can change it from a UI instead of kubectl. Run ./scripts/start-demo.sh to reach it, unauthenticated at http://localhost:7007 once forwarded.${NC}"
elif [[ "${STAGE}" -eq 5 ]]; then
  echo -e "${GREEN}Stage 5 (Progressive autonomy) is up: stage 4's workloads and backends, unchanged, plus cert-manager ${CERT_MANAGER_VERSION}, the Open Feature Operator ${OPENFEATURE_CHART_VERSION}, and the northstar-autonomy FeatureFlag/FeatureFlagSource pair. The PDS pod carries a flagd sidecar answering on localhost:8013 inside it, serving flag 'autonomy-level' (variants assisted/autonomous, targeted on evaluation-context key spiffeId) -- still resolved nowhere yet, since this stage stands up the machinery only.${NC}"
elif [[ "${STAGE}" -eq 4 ]]; then
  echo -e "${GREEN}Stage 4 (Observable) is up: stage 3's workloads (reviewer-ui, senior-settlement-agent) on the same Cilium ${CILIUM_CHART_VERSION} / SPIRE ${SPIRE_CHART_VERSION} cluster -- unchanged -- plus OpenSearch ${OPENSEARCH_CHART_VERSION} holding this system's traces, opensearch-dashboards for reading them with the evidence view already imported, prometheus ${PROMETHEUS_CHART_VERSION} holding its metrics on OTLP ingest alone, and eval-scorer scoring a settlement's trajectory against the five policy-adherence properties and emitting each score back into the trace it judges. Run ./scripts/start-demo.sh to reach Dashboards and Prometheus; neither is a kind host port, and the evidence view is at http://localhost:5601/app/dashboards#/view/northstar-evidence.${NC}"
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
  # which every later stage also runs on, so all of them answer here. On a
  # stage-1 or stage-2 cluster this URL answers nothing and printing it would
  # be the same class of false instruction this block's sibling in
  # scripts/smoke-test.sh checks for.
  echo "  Reviewer UI:         http://localhost:8081"
fi
echo "  Jaeger UI:           http://localhost:16686"
echo "  Collector OTLP/HTTP: http://localhost:4318"
echo "  claims-agent (A2A):  http://localhost:19001"
echo "  Local registry:      localhost:${REGISTRY_PORT}"
if [[ "${STAGE}" -ge 6 ]]; then
  # Not a kind host port, same reasoning as Dashboards/Prometheus at stage 4:
  # reached by port-forward rather than printed as a bare URL.
  # ./scripts/start-demo.sh owns this forward the same way it owns Dashboards'
  # and Prometheus', so this points there instead of to a hand-assembled
  # `kubectl port-forward` command an operator would otherwise have to
  # remember.
  echo "  Backstage:           run ./scripts/start-demo.sh, then http://localhost:7007"
fi
echo ""
echo "Next: ./scripts/smoke-test.sh"
