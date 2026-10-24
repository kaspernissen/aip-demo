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
  echo -e "${RED}kind-registry is not running. Run scripts/setup-kind.sh first.${NC}" >&2
  exit 1
fi

# component:build-context:namespace
# NOTE: macOS ships bash 3.2 with no associative arrays (`declare -A`), and
# this repo has no bash 4+ available, so the map is an indexed array of
# "name:context:namespace" triples instead. Append new components here.
#
# Every component is rebuilt from source through this list: a component outside
# it would silently keep running whichever image was pushed last.
COMPONENTS=(
  "payments-api:systems/payments-api:northstar-systems"
  "payments-mcp:tools/payments-mcp:northstar-tools"
  "settlement-agent:agents/settlement-agent:northstar-agents"
  "claims-agent:agents/claims-agent:northstar-agents"
  "customer-ui:apps/customer-ui:northstar-agents"
  "policy-decision-service:platform/policy-decision-service:northstar-platform"
  "reviewer-ui:apps/reviewer-ui:northstar-agents"
  "backstage:platform/backstage:northstar-platform"
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

  # A missing build context is a hard error in every case, default run or
  # explicitly named component alike -- there is no skip branch for it,
  # because a skip here would silently swallow a missing build context the
  # moment a new component is added to COMPONENTS without one. Unlike a
  # resolvable "unknown component" typo (checked above), this error can only
  # mean a real, broken build context, so it always exits.
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

  # backstage's build context (platform/backstage/) cannot reach the
  # catalog-info.yaml files its catalog serves — they live outside that build
  # context, and this repo's catalog is static rather than git-discovered (it
  # has no git remote). Stage a mirror of their real repo-relative paths into
  # .catalog-stage/ before the build, on the same "copy in, always clean up"
  # terms as agent-card.json above, so platform/backstage/Dockerfile can
  # COPY it into the image at ./catalog.
  #
  # contracts/agent-cards/*.json is deliberately NOT staged here: Backstage's
  # default UrlReaders register no reader for a local `file:` scheme — see
  # agents/claims-agent/catalog-info.yaml's header comment for the measured
  # error and the trace. Nothing baked into the image would ever read a
  # staged copy, so staging one would be dead weight for a mechanism that
  # does not work; the agent cards stay linked by repo path in prose, read
  # from the committed tree the normal way, same as any other doc reference
  # in this repo.
  catalog_stage=""
  if [[ "$component" == "backstage" ]]; then
    catalog_stage="$ROOT/$context/.catalog-stage"
    rm -rf "$catalog_stage"
    mkdir -p "$catalog_stage"
    cp "$ROOT/catalog-info.yaml" "$catalog_stage/system-catalog-info.yaml"
    # The System's own TechDocs -- the solution documentation, as opposed to
    # the nine per-component sites staged in the loop below. The System entity
    # carries `backstage.io/techdocs-ref: dir:.`, which resolves to the
    # directory holding its catalog-info.yaml: this staging root. So the repo
    # root's mkdocs.yml and docs/ have to land here, beside
    # system-catalog-info.yaml, exactly as each component's do beside theirs.
    #
    # docs/ is copied whole, superpowers/ included, and mkdocs.yml excludes
    # that subtree from the built site. One source of truth, one place the
    # exclusion is stated -- rather than a copy rule here and a nav rule there
    # that can disagree about which documents are part of the solution.
    cp "$ROOT/mkdocs.yml" "$catalog_stage/mkdocs.yml"
    cp -R "$ROOT/docs" "$catalog_stage/docs"
    for entry in "${COMPONENTS[@]}"; do
      cname="${entry%%:*}"
      [[ "$cname" == "backstage" ]] && continue
      crest="${entry#*:}"
      ccontext="${crest%%:*}"
      mkdir -p "$catalog_stage/$ccontext"
      cp "$ROOT/$ccontext/catalog-info.yaml" "$catalog_stage/$ccontext/catalog-info.yaml"
      # TechDocs travels with the entity that owns it. Each component's
      # `backstage.io/techdocs-ref: dir:.` resolves relative to the directory
      # holding its catalog-info.yaml, which in the image is this staged
      # directory -- so mkdocs.yml and docs/ have to land beside it or the
      # docs tab renders "no techdocs" against a component that has some.
      #
      # Copied rather than required: a component with no docs yet simply has
      # no mkdocs.yml, and stages fine. tests/unit/
      # test_every_component_has_techdocs.py is what makes that a deliberate
      # gap rather than a silent one.
      if [[ -f "$ROOT/$ccontext/mkdocs.yml" ]]; then
        cp "$ROOT/$ccontext/mkdocs.yml" "$catalog_stage/$ccontext/mkdocs.yml"
      fi
      if [[ -d "$ROOT/$ccontext/docs" ]]; then
        cp -R "$ROOT/$ccontext/docs" "$catalog_stage/$ccontext/docs"
      fi
    done
  fi

  # A running image that predates the commit under review means an RBAC
  # matrix and a healthy pod can both "verify" a security boundary that
  # isn't actually running. Stamping the current commit into the image
  # (platform/backstage/Dockerfile's own `GIT_SHA` build-arg /
  # `org.opencontainers.image.revision` label) is what
  # tests/e2e/test_backstage_image_matches_head.py compares against
  # `git rev-parse HEAD`. Backstage only, for now: it is the one image this
  # repo's security posture depends on being provably current, and adding
  # the build-arg to a Dockerfile that does not declare it would just be a
  # docker warning about an unused arg, not a false sense of coverage.
  build_cmd=(docker build -t "$image" "$ROOT/$context")
  if [[ "$component" == "backstage" ]]; then
    # `git -C ... || echo unknown`, not a bare `git rev-parse`: under `set -e`
    # a failing command substitution here (no git repo) would end the run.
    # A real checkout always resolves a real SHA; "unknown" only ever shows
    # up in a context that was never a git repo to begin with, and the image
    # revision guard (tests/e2e/test_backstage_image_matches_head.py) treats
    # that as a hard failure rather than a pass, so this fallback cannot mask
    # a stale build from a real checkout.
    git_sha="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    # The label must bind the image to the TREE, not just to HEAD: a commit's
    # SHA does not move for uncommitted edits, so a mutated working tree could
    # stamp and pass the label check while running code the commit does not
    # contain -- silently defeating tests/e2e/test_backstage_image_matches_head.py's
    # anti-vacuity guarantee. `git status --porcelain` catches both halves of
    # "uncommitted" -- modified/staged tracked files and untracked new ones --
    # so a dirty tree stamps `<sha>-dirty`, which that guard's
    # `label == head` comparison fails on rather than silently matching.
    if [[ "$git_sha" != "unknown" ]] && [[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]]; then
      git_sha="${git_sha}-dirty"
    fi
    build_cmd=(docker build --build-arg "GIT_SHA=${git_sha}" -t "$image" "$ROOT/$context")
  fi

  if ! "${build_cmd[@]}"; then
    rm -f "$ROOT/$context/agent-card.json"
    [[ -n "$catalog_stage" ]] && rm -rf "$catalog_stage"
    exit 1
  fi
  rm -f "$ROOT/$context/agent-card.json"
  [[ -n "$catalog_stage" ]] && rm -rf "$catalog_stage"

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
echo "Check status:  kubectl get pods -A"
echo "Follow logs:   kubectl -n <namespace> logs -f deployment/<component>"
