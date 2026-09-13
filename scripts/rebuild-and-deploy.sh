#!/usr/bin/env bash
set -euo pipefail

REGISTRY_PORT="5001"
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
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }

if ! docker ps --format '{{.Names}}' | grep -qx kind-registry; then
  # M4 Task 1 fix round 1 (ruling M4-R7): stage 4 is the current stage, so
  # this points there. It said `--stage 3` until stage 4 became real.
  echo -e "${RED}kind-registry is not running. Run scripts/setup-kind.sh --stage 4 first.${NC}" >&2
  exit 1
fi

# component:build-context:namespace
# NOTE: macOS ships bash 3.2 with no associative arrays (`declare -A`), and
# this repo has no bash 4+ available, so the map is an indexed array of
# "name:context:namespace" triples instead. Append new components here.
#
# M2.2 Task 4 appended policy-decision-service. It is registered here rather
# than applied by hand so it is rebuilt exactly like the others: a component
# outside this list is a component that silently keeps running last week's
# image after someone edits its source, and this one's source is the policy.
#
# It is a STAGE 2 component (northstar-platform does not exist at stage 1),
# which is why scripts/setup-kind.sh names it inside a `STAGE -ge 2` block
# rather than adding it to the stage-1 list at Step 9. A bare
# `./scripts/rebuild-and-deploy.sh` with no arguments does still build it: this
# script has no notion of stages and never has, and against a stage-1 cluster
# this component's apply would fail on the missing namespace exactly as
# agentgateway's ConfigMap would.
COMPONENTS=(
  "payments-api:systems/payments-api:northstar-systems"
  "payments-mcp:tools/payments-mcp:northstar-tools"
  "settlement-agent:agents/settlement-agent:northstar-agents"
  "claims-agent:agents/claims-agent:northstar-agents"
  "customer-ui:apps/customer-ui:northstar-agents"
  "policy-decision-service:platform/policy-decision-service:northstar-platform"
  # M3 Task 4: reviewer-ui. Note what is deliberately NOT here:
  # senior-settlement-agent -- it is the settlement-agent image, unmodified,
  # in a second Deployment (deploy/k8s/senior-settlement-agent.yaml's own
  # header), so it has no build context of its own and is applied by hand
  # (`kubectl apply -f deploy/k8s/senior-settlement-agent.yaml`) rather than
  # rebuilt here. reviewer-ui is a genuinely new image, built from its own
  # apps/reviewer-ui context, so it belongs in this list the same way
  # policy-decision-service's own addition comment argues: a component
  # outside this list keeps running whatever image happened to be pushed
  # last, silently.
  #
  # M3 final-review fix round: "applied by hand" was only half the story, and
  # the missing half was a real hazard -- see COMPANION_DEPLOYMENTS below.
  "reviewer-ui:apps/reviewer-ui:northstar-agents"
  # M4 Task 4: eval-scorer. Registered here for the same reason
  # policy-decision-service's own addition comment argues -- a component
  # outside this list keeps running whatever image happened to be pushed last,
  # silently, and this one's source is the audit.
  #
  # It is a STAGE 4 component (it reads `ss4o_traces-default-namespace`, which
  # does not exist below stage 4), which is why scripts/setup-kind.sh names it
  # inside the `STAGE -ge 4` block rather than adding it to the stage-1 list
  # at Step 9. A bare `./scripts/rebuild-and-deploy.sh` with no arguments does
  # still build it, exactly as it still builds policy-decision-service: this
  # script has no notion of stages and never has, and against a stage-1
  # cluster this component's apply would fail on the missing namespace.
  "eval-scorer:platform/eval-scorer:northstar-platform"
)

# component:companion-deployment:namespace
#
# A *companion* is a Deployment that runs one of COMPONENTS' images but is not
# a component itself: no build context, no manifest applied by this script. It
# still has to be RESTARTED whenever that image is rebuilt, because both
# Deployments name the same MUTABLE tag and `kubectl apply` over an unchanged
# manifest is a no-op.
#
# M3 final-review fix round. `deploy/k8s/settlement-agent.yaml` and
# `deploy/k8s/senior-settlement-agent.yaml` both name
# `localhost:5001/northstar/settlement-agent:dev` -- read out of both
# manifests, not assumed. Until now, running this script for
# `settlement-agent` (or a bare full run) pushed a new image under that tag
# and restarted `deployment/settlement-agent` alone, leaving
# `deployment/senior-settlement-agent` serving the previous build.
# scripts/setup-kind.sh's Step 9d already states this hazard in full -- "Two
# pods of 'the same image' silently disagreeing is the worst version of this
# to debug" -- and fixed it only on the full-cluster path, which is not the
# path an operator takes between demos. The two digests happened to agree when
# the whole-branch review found this; the first edit to `settlement.py`
# followed by a redeploy is what would have broken the byte-identical-image
# premise this entire milestone's argument rests on.
COMPANION_DEPLOYMENTS=(
  "settlement-agent:senior-settlement-agent:northstar-agents"
)

component_companions() {
  local name="$1" entry
  for entry in "${COMPANION_DEPLOYMENTS[@]}"; do
    if [[ "${entry%%:*}" == "$name" ]]; then
      echo "${entry#*:}"
    fi
  done
}

component_names() {
  local entry
  for entry in "${COMPONENTS[@]}"; do
    echo "${entry%%:*}"
  done
}

component_spec() {
  local name="$1" entry
  for entry in "${COMPONENTS[@]}"; do
    if [[ "${entry%%:*}" == "$name" ]]; then
      echo "${entry#*:}"
      return 0
    fi
  done
  return 1
}

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=()
  while IFS= read -r name; do
    targets+=("$name")
  done < <(component_names)
fi

total=${#targets[@]}
step=0
deployed=()

for component in "${targets[@]}"; do
  step=$((step + 1))
  if ! spec="$(component_spec "$component")"; then
    echo -e "${RED}Error: unknown component '${component}'${NC}" >&2
    echo "known components: $(component_names | tr '\n' ' ')" >&2
    exit 1
  fi
  context="${spec%%:*}"
  namespace="${spec##*:}"
  image="localhost:${REGISTRY_PORT}/northstar/${component}:dev"

  # A15a: A15's original skip branch is retired. All five build contexts have
  # existed since Task 6 landed, so the skip branch was unreachable in a
  # healthy tree and would have silently swallowed a missing build context
  # the moment a later milestone adds a new component to COMPONENTS — the
  # opposite of what A15 set out to achieve. A missing build context is now a
  # hard error in every case, default run or explicitly named component
  # alike; unlike a resolvable "unknown component" typo (checked above), this
  # error can only mean a real, broken build context, so it always exits.
  if [[ ! -d "$ROOT/$context" ]]; then
    echo -e "${RED}Error: ${component}'s build context ${context} does not exist${NC}" >&2
    exit 1
  fi

  echo -e "${BLUE}Step ${step}/${total}: Building ${image} from ${context}${NC}"
  # Agent Dockerfiles COPY agent-card.json from their own build context, but
  # the canonical copy lives in contracts/agent-cards/ (one source of truth,
  # shared with the A2A trace tests). Stage it in before the build and always
  # remove it after — including on a failed build, so `set -e` exiting this
  # script never leaves a stray copy behind for git to pick up (see
  # .gitignore: agents/*/agent-card.json, belt-and-braces on top of this).
  card="$ROOT/contracts/agent-cards/${component}.json"
  if [[ -f "$card" ]]; then
    cp "$card" "$ROOT/$context/agent-card.json"
  fi
  if ! docker build -t "$image" "$ROOT/$context"; then
    rm -f "$ROOT/$context/agent-card.json"
    exit 1
  fi
  rm -f "$ROOT/$context/agent-card.json"

  echo -e "${BLUE}Step ${step}/${total}: Pushing ${image} to the local registry${NC}"
  docker push "$image"

  echo -e "${BLUE}Step ${step}/${total}: Applying manifest and restarting deployment/${component}${NC}"
  kubectl apply -f "$ROOT/deploy/k8s/${component}.yaml"
  kubectl -n "$namespace" rollout restart "deployment/${component}"
  kubectl -n "$namespace" rollout status "deployment/${component}" --timeout=180s

  # Every other Deployment running the image this step just pushed. No
  # `kubectl apply` for these: their manifests are not this script's to apply
  # (senior-settlement-agent's is applied by setup-kind.sh's Step 9d), and an
  # apply would be a no-op anyway. What they need is the restart, and they
  # need it for exactly the reason the component itself does.
  while IFS= read -r companion; do
    [[ -n "$companion" ]] || continue
    companion_ns="${companion##*:}"
    companion_name="${companion%%:*}"
    echo -e "${BLUE}Step ${step}/${total}: Restarting companion deployment/${companion_name} (same image, second identity)${NC}"
    kubectl -n "$companion_ns" rollout restart "deployment/${companion_name}"
    kubectl -n "$companion_ns" rollout status "deployment/${companion_name}" --timeout=180s
  done < <(component_companions "$component")

  echo -e "${GREEN}${component} deployed.${NC}"
  deployed+=("$component")
done

echo ""
if [[ ${#deployed[@]} -gt 0 ]]; then
  echo -e "${GREEN}Rebuilt and deployed: ${deployed[*]}${NC}"
else
  echo -e "${YELLOW}Nothing was deployed.${NC}"
fi
echo "Check status:  kubectl get pods -A"
echo "Follow logs:   kubectl -n <namespace> logs -f deployment/<component>"
