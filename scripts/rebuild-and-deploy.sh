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
  echo -e "${RED}kind-registry is not running. Run scripts/setup-kind.sh --stage 1 first.${NC}" >&2
  exit 1
fi

# component:build-context:namespace
# NOTE: macOS ships bash 3.2 with no associative arrays (`declare -A`), and
# this repo has no bash 4+ available, so the map is an indexed array of
# "name:context:namespace" triples instead. Append new components here.
COMPONENTS=(
  "payments-api:systems/payments-api:northstar-systems"
  "payments-mcp:tools/payments-mcp:northstar-tools"
  "settlement-agent:agents/settlement-agent:northstar-agents"
  "claims-agent:agents/claims-agent:northstar-agents"
  "customer-ui:apps/customer-ui:northstar-agents"
)

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
explicit=true
if [[ ${#targets[@]} -eq 0 ]]; then
  explicit=false
  targets=()
  while IFS= read -r name; do
    targets+=("$name")
  done < <(component_names)
fi

total=${#targets[@]}
step=0
deployed=()
skipped=()

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

  # A15: until all five build contexts exist (by end of M1.1), a default
  # no-argument run must skip a component whose context directory doesn't
  # exist yet rather than dying on `docker build`. A component named
  # explicitly on the command line is a hard error instead — the user asked
  # for it by name, so silently doing nothing would be worse than failing.
  if [[ ! -d "$ROOT/$context" ]]; then
    if [[ "$explicit" == true ]]; then
      echo -e "${RED}Error: ${component}'s build context ${context} does not exist${NC}" >&2
      exit 1
    fi
    echo -e "${YELLOW}Skipping ${component} — ${context} does not exist yet${NC}"
    skipped+=("$component")
    continue
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

  echo -e "${GREEN}${component} deployed.${NC}"
  deployed+=("$component")
done

echo ""
if [[ ${#deployed[@]} -gt 0 ]]; then
  echo -e "${GREEN}Rebuilt and deployed: ${deployed[*]}${NC}"
else
  echo -e "${YELLOW}Nothing was deployed.${NC}"
fi
if [[ ${#skipped[@]} -gt 0 ]]; then
  echo -e "${YELLOW}Skipped (build context does not exist yet): ${skipped[*]}${NC}"
fi
echo "Check status:  kubectl get pods -A"
echo "Follow logs:   kubectl -n <namespace> logs -f deployment/<component>"
