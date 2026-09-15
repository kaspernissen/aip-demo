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
  echo -e "${RED}kind-registry is not running. Run scripts/setup-kind.sh --stage 5 first.${NC}" >&2
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
  # M6 Task 1: Backstage. Registered here for the same reason eval-scorer's
  # own addition comment argues -- a component outside this list keeps running
  # whatever image happened to be pushed last, silently, and this one's source
  # is the platform-API view the whole milestone is about.
  #
  # It is a STAGE 6 component (the catalog it serves, added in Task 2, reads
  # `contracts/agent-cards/` and the eight component directories baked into
  # its own image, none of which exist below stage 6 in any meaningful sense),
  # which is why scripts/setup-kind.sh names it inside a `STAGE -ge 6` block
  # rather than adding it to the stage-1 list. A bare
  # `./scripts/rebuild-and-deploy.sh` with no arguments does still build it
  # (this script has no notion of stages and never has), and against a
  # stage-1..5 cluster this component's apply would fail on the missing
  # `northstar-platform` namespace exactly as policy-decision-service's would
  # -- except `northstar-platform` exists from stage 2 on, so in practice it
  # would simply come up two stages before the catalog it is meant to serve
  # has anything real in it. That is a cosmetic gap, not a functional one:
  # nothing here reads catalog-info.yaml files that Task 2 has not written yet.
  "backstage:platform/backstage:northstar-platform"
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

  # M6 Task 2: backstage's build context (platform/backstage/) cannot reach
  # the catalog-info.yaml files its catalog serves — they live outside that
  # build context, and design §3 rules out a git-based catalog (this repo
  # has no remote). Stage a mirror of their real repo-relative paths into
  # .catalog-stage/ before the build, on the same "copy in, always clean up"
  # terms as agent-card.json above, so platform/backstage/Dockerfile can
  # COPY it into the image at ./catalog.
  #
  # contracts/agent-cards/*.json is deliberately NOT staged here. It was,
  # until this task's own verification found that Backstage's default
  # UrlReaders register no reader for a local `file:` scheme — see
  # agents/claims-agent/catalog-info.yaml's header comment for the measured
  # error and the trace. Nothing baked into the image would ever read a
  # staged copy, so staging one was dead weight kept only for a mechanism
  # that does not work; the agent cards stay linked by repo path in prose,
  # read from the committed tree the normal way, same as any other doc
  # reference in this repo.
  catalog_stage=""
  if [[ "$component" == "backstage" ]]; then
    catalog_stage="$ROOT/$context/.catalog-stage"
    rm -rf "$catalog_stage"
    mkdir -p "$catalog_stage"
    cp "$ROOT/catalog-info.yaml" "$catalog_stage/system-catalog-info.yaml"
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

  # M6-R9: the Task 3 review found the running Backstage image had been
  # built an hour before the commit under review -- nothing checked that
  # the image contained the committed code, so an RBAC matrix and a healthy
  # pod both "verified" a security boundary that was not running. Stamping
  # the current commit into the image (platform/backstage/Dockerfile's own
  # `GIT_SHA` build-arg / `org.opencontainers.image.revision` label) is what
  # tests/e2e/test_backstage_image_matches_head.py compares against
  # `git rev-parse HEAD`. Backstage only, for now: it is the one image this
  # milestone's security review turned out to need this for, and adding the
  # build-arg to a Dockerfile that does not declare it would just be a
  # docker warning about an unused arg, not a false sense of coverage.
  build_cmd=(docker build -t "$image" "$ROOT/$context")
  if [[ "$component" == "backstage" ]]; then
    # `git -C ... || echo unknown`, not a bare `git rev-parse`: this script
    # also runs inside test_rebuild_and_deploy_guard.py's sandboxes, which
    # copy the script into a tmp dir that is NOT a git repository, and
    # `set -e` would otherwise take the whole run down on a command
    # substitution failure inside this array assignment (verified: it does).
    # A real checkout always resolves a real SHA; "unknown" only ever shows
    # up in a context that was never a git repo to begin with, and the image
    # revision guard (tests/e2e/test_backstage_image_matches_head.py) treats
    # that as a hard failure rather than a pass, so this fallback cannot mask
    # a stale build from a real checkout.
    git_sha="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    # Whole-branch review M3(b): the label used to bind the image to HEAD,
    # not to the tree -- a commit's SHA does not move for uncommitted edits,
    # so a mutated working tree stamped and passed the label check anyway.
    # Demonstrated live in the review: `router.ts` neutered in the working
    # tree, rebuilt, and both image-identity tests in
    # tests/e2e/test_backstage_image_matches_head.py PASSED while the
    # anti-vacuity tests were RED against that same image. `git status
    # --porcelain` catches both halves of "uncommitted" -- modified/staged
    # tracked files and untracked new ones -- so a dirty tree now stamps
    # `<sha>-dirty`, which that guard's `label == head` comparison fails on
    # rather than silently matching.
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

  # Every other Deployment running the image this step just pushed. No
  # `kubectl apply` for these: their manifests are not this script's to apply
  # (senior-settlement-agent's is applied by setup-kind.sh's Step 9d), and an
  # apply would be a no-op anyway. What they need is the restart, and they
  # need it for exactly the reason the component itself does.
  while IFS= read -r companion; do
    [[ -n "$companion" ]] || continue
    companion_ns="${companion##*:}"
    companion_name="${companion%%:*}"
    # M5-R27. A companion that does not exist YET is not an error, and until
    # 2026-09-14 it killed every cold bring-up. setup-kind.sh's Step 9/9 runs
    # this script for the five stage-1 components; the manifest that CREATES
    # senior-settlement-agent is not applied until Step 9d, later in that same
    # script. Warm, the Deployment was always already there, so the ordering
    # was never exercised -- stages 4 and 5 were only ever applied over an
    # existing cluster (amendment A1). Cold, `rollout restart` hit a
    # non-existent Deployment and `set -e` took the run down at the first of
    # five components. That mattered more than it looked: this repo has no git
    # remote, so rebuilding from scratch is the only recovery path there is,
    # and it did not work.
    #
    # Skipping is correct here and costs nothing: a Deployment created AFTER
    # this push pulls the image this step just pushed, by definition, so no
    # restart is owed. Step 9d creates it moments later.
    #
    # What this must NOT become is a silent skip -- "skips instead of failing
    # on the object it guards" is this repo's most-repeated defect (M2.2, four
    # times). So only a genuine NotFound is tolerated: an unreachable API
    # server, an RBAC denial, or any other kubectl failure still aborts loudly
    # rather than being mistaken for "not created yet", which would quietly
    # leave a companion serving a stale image -- the exact hazard
    # COMPANION_DEPLOYMENTS exists to prevent.
    if ! companion_probe_err="$(kubectl -n "$companion_ns" get "deployment/${companion_name}" 2>&1 >/dev/null)"; then
      if [[ "$companion_probe_err" == *NotFound* || "$companion_probe_err" == *"not found"* ]]; then
        echo -e "${YELLOW}Step ${step}/${total}: companion deployment/${companion_name} does not exist yet -- skipping its restart; whatever creates it will already be running this image.${NC}"
        continue
      fi
      echo -e "${RED}Error: could not determine whether deployment/${companion_name} exists in namespace ${companion_ns}.${NC}" >&2
      echo "${companion_probe_err}" >&2
      echo "Refusing to continue: treating this as 'not created yet' could leave the companion on a stale image." >&2
      exit 1
    fi
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
