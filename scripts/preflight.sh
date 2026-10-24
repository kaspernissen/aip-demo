#!/usr/bin/env bash
set -euo pipefail

# Is the demo ready? Run before a demo session or a test run; it changes
# nothing. scripts/setup-kind.sh runs it at the end, and scripts/smoke-test.sh
# runs it before the gate.
#
#   1. The keystone is armed: the Kyverno policy is Ready, its webhook is
#      registered, something answers behind it, and the demo's over-grant is
#      absent.
#   2. Every localhost URL answers an HTTP request. Each is a kind host port
#      onto a NodePort (deploy/kind/cluster.yaml); the list below must name
#      exactly the host ports that file publishes, which
#      tests/unit/test_cluster_config.py checks.
#
# Exits non-zero with the reason and the remedy on the first failure.

CONTEXT="kind-northstar"
KEYSTONE_POLICY="validatingpolicy/agents-cannot-edit-their-own-authority"
# How long a URL may take to start answering. A fresh rollout can take a few
# seconds to reach its NodePort; a URL that never answers fails after this.
WAIT_S="${PREFLIGHT_WAIT_S:-60}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo -e "${RED}curl is required${NC}" >&2; exit 1; }

kubectl --context "$CONTEXT" cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}Error: cannot reach the '${CONTEXT}' cluster.${NC}" >&2
  echo "Run ./scripts/setup-kind.sh first." >&2
  exit 1
}

# --- 1. The keystone ---------------------------------------------------------
#
# Each check reads kubectl's OUTPUT, not its exit status: `kubectl get` exits 0
# on some "found nothing" paths, and output is the one thing an absent object
# cannot fake.
#
# Kyverno down matters twice: the policy fails closed, so Backstage's flip of
# the autonomy flag would also be refused, with an error that looks like a
# Backstage bug. A graceful stop of the admission controller also removes the
# webhook registration while the policy still looks Ready, and then nothing is
# enforced at all.
READY="$(kubectl --context "$CONTEXT" get "$KEYSTONE_POLICY" -o jsonpath='{.status.conditionStatus.ready}' 2>/dev/null || true)"
if [[ "$READY" != "true" ]]; then
  echo -e "${RED}Error: the keystone policy is missing or not Ready (conditionStatus.ready='${READY}').${NC}" >&2
  echo "Check: kubectl -n kyverno get pods; kubectl get ${KEYSTONE_POLICY} -o yaml (or re-run ./scripts/setup-kind.sh)" >&2
  exit 1
fi
if [[ -z "$(kubectl --context "$CONTEXT" get validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg --ignore-not-found -o name 2>/dev/null)" ]]; then
  echo -e "${RED}Error: Kyverno's resource webhook configuration is not registered, so the keystone is NOT enforcing.${NC}" >&2
  echo "Bring Kyverno back: kubectl -n kyverno scale deploy/kyverno-admission-controller --replicas=1, then wait for the configuration to reappear." >&2
  exit 1
fi
if [[ -z "$(kubectl --context "$CONTEXT" -n kyverno get endpointslices -l kubernetes.io/service-name=kyverno-svc -o jsonpath='{.items[*].endpoints[*].addresses[*]}' 2>/dev/null)" ]]; then
  echo -e "${RED}Error: nothing answers behind svc/kyverno-svc in the kyverno namespace.${NC}" >&2
  echo "The policy fails closed while registered, so every write it matches is refused. Check: kubectl -n kyverno get pods" >&2
  exit 1
fi
# deploy/demo/keystone-overgrant.yaml models a mistake; `scripts/demo keystone`
# applies it and takes it back. One left behind by an interrupted run is a
# standing grant letting settlement-agent patch the autonomy flag.
if [[ -n "$(kubectl --context "$CONTEXT" -n northstar-platform get role,rolebinding keystone-demo-overgrant --ignore-not-found -o name 2>/dev/null)" ]]; then
  echo -e "${RED}Error: the keystone demo over-grant is still applied (role/rolebinding keystone-demo-overgrant in northstar-platform).${NC}" >&2
  echo "Remove it: scripts/demo reset (or kubectl delete -f deploy/demo/keystone-overgrant.yaml)" >&2
  exit 1
fi
echo -e "${GREEN}Keystone: policy Ready, webhook registered, Kyverno answering, over-grant absent.${NC}"

# --- 2. The localhost URLs ---------------------------------------------------
#
# port|name|path. The path is anything that answers without credentials; any
# HTTP status counts (the collector answers 404/405 to a GET), only "no
# response at all" fails. Backstage gets `/`: every /api route needs a token.
SURFACES=(
  "8080|Customer UI|/"
  "8081|Reviewer UI|/healthz"
  "19001|claims-agent (A2A)|/.well-known/agent-card.json"
  "16686|Jaeger|/"
  "4318|Collector OTLP/HTTP|/"
  "9090|Prometheus|/-/healthy"
  "18001|AgentEvals|/api/health"
  "7007|Backstage|/"
)

answers() {
  curl -s -o /dev/null -m 5 "http://localhost:$1$2"
}

for surface in "${SURFACES[@]}"; do
  IFS='|' read -r port name path <<<"$surface"
  deadline=$((SECONDS + WAIT_S))
  until answers "$port" "$path"; do
    if (( SECONDS >= deadline )); then
      echo -e "${RED}Error: ${name} does not answer on http://localhost:${port}${path} after ${WAIT_S}s.${NC}" >&2
      echo "It is a NodePort published by deploy/kind/cluster.yaml. Check the workload is Running: kubectl get pods -A | grep -v Running" >&2
      exit 1
    fi
    sleep 2
  done
done
echo -e "${GREEN}Every localhost URL answers.${NC}"
echo ""
for surface in "${SURFACES[@]}"; do
  IFS='|' read -r port name _ <<<"$surface"
  printf '  %-21s http://localhost:%s\n' "${name}:" "$port"
done
