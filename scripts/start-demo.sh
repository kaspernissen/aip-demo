#!/usr/bin/env bash
set -euo pipefail

# Owns every `kubectl port-forward` this demo needs, so an operator has
# exactly one command to run when a forward dies -- which is this project's
# single most-repeated operational failure. A dead `kubectl port-forward`
# keeps its listening socket bound, so `lsof` alone reports it healthy even
# when nothing behind it answers.
#
# The shape is a common one: a `PIDS` array, one background
# `kubectl port-forward` per surface, and `cleanup` on
# `trap ... EXIT INT TERM`. Two choices are deliberate:
#
#   1. NO `pkill -f "kubectl port-forward"` fallback. That kills every
#      port-forward on the machine, including an operator's own in another
#      terminal, not just the ones this script started. The tracked PIDS
#      array below is the whole cleanup.
#   2. Liveness is a real HTTP request, never process existence. A live PID
#      proves nothing, since a dead forward's socket stays bound. This reuses
#      the exact two-question shape scripts/smoke-test.sh already implements
#      -- lsof, then curl -- rather than inventing a second one.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

command_exists() {
  command -v "$1" >/dev/null 2>&1
}
command_exists kubectl || { echo -e "${RED}kubectl is required${NC}" >&2; exit 1; }
command_exists curl || { echo -e "${RED}curl is required (the liveness check uses it)${NC}" >&2; exit 1; }
command_exists lsof || { echo -e "${RED}lsof is required (the liveness check uses it)${NC}" >&2; exit 1; }

# Departure 1 from the reference script: PIDS is the ENTIRE cleanup. Nothing
# here ever kills a process it did not start.
PIDS=()
CLEANED_UP=""

cleanup() {
  # Guards against the trap firing twice for one Ctrl-C: INT/TERM runs this,
  # then the shell's own exit runs the EXIT trap a second time. Idempotent
  # either way (kill -0 on an already-dead PID just skips it), but the guard
  # keeps the "Stopping..." banner from printing twice.
  [[ -n "$CLEANED_UP" ]] && return
  CLEANED_UP=1
  echo ""
  echo -e "${BLUE}Stopping the port-forwards this script started...${NC}"
  for pid in "${PIDS[@]:-}"; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

# Departure 2: a live PID proves nothing -- the target pod behind a
# `kubectl port-forward` can be replaced while the forward's own socket stays
# bound and `lsof` keeps calling it healthy. Every surface below is verified
# with the SAME two questions scripts/smoke-test.sh's prerequisite loop asks:
# is a socket bound, and does an HTTP request actually get an answer through
# it. Any HTTP response counts (this is a liveness probe, not a contract
# check) -- payments-mcp's `/mcp` answering 400 to a bare GET is exactly as
# good a proof as a 200 elsewhere.

# For a forward THIS script started: also confirm the PID we launched is the
# one holding the port, not merely that something is -- a stray forward from
# another cluster on this machine can silently shadow a host port.
verify_owned_forward() {
  local port="$1" pid="$2" name="$3" path="$4"
  # `grep -x ... >/dev/null` and NOT `grep -qx` (the identical construct in
  # scripts/setup-kind.sh uses the same fix). Under `set -o pipefail`,
  # `grep -q` exits the moment it matches, which can SIGPIPE the `awk` still
  # writing to it; awk then exits 141 and pipefail reports 141 for the whole
  # pipeline even though grep matched. That turns a healthy forward into
  # "never bound" and exits 1 -- whose EXIT trap then kills the forwards this
  # script had already established. Dropping -q makes grep drain its input,
  # so nothing upstream can be signalled. It is a race rather than a
  # certainty (lsof's output is small enough that the write usually completes
  # first), which is exactly why it is worth removing rather than reasoning
  # about.
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -x "${pid}" >/dev/null; then
    # The port is held by something other than the PID this script just
    # launched -- a foreign forward, possibly left over from an earlier run.
    # Refusing on an UNVERIFIED holder is correct: a dead `kubectl
    # port-forward` keeps its socket bound, so "something is bound" is not
    # evidence -- but a foreign holder that actually answers is not a problem
    # to fix. Ask it the same question verify_existing_surface already asks
    # of reviewer-ui's NodePort, rather than inventing a second liveness
    # check.
    if curl -s -o /dev/null -m 5 "http://localhost:${port}${path}"; then
      echo -e "${YELLOW}  - ${name}: port ${port} is already held by another process and it answers -- accepting it instead of starting a second forward.${NC}"
      # This script's own kubectl process for this surface never bound (the
      # port was already taken); it is doing nothing and is not worth leaving
      # around.
      kill "${pid}" 2>/dev/null || true
      return 0
    fi
    echo -e "${RED}Error: the port-forward for ${name} (expected PID ${pid} listening on ${port}) never bound, and whatever already holds ${port} does not answer.${NC}" >&2
    echo "Another process holds that port without serving anything through it -- check: lsof -nP -iTCP:${port} -sTCP:LISTEN" >&2
    # Do NOT fire the EXIT trap for this refusal: the forwards already
    # verified for the OTHER surfaces are healthy, and the previous behaviour
    # -- exit under `set -e`, EXIT trap fires, cleanup kills every PID this
    # script started -- left an operator with one stray, unresponsive holder
    # ending up with FEWER live forwards than before they ran this script.
    # Refusing to proceed on this one surface must not undo the others.
    trap - EXIT
    exit 1
  fi
  if ! curl -s -o /dev/null -m 5 "http://localhost:${port}${path}"; then
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request.${NC}" >&2
    echo "This is a dead kubectl port-forward: its target pod was replaced, the socket stayed bound, and lsof still calls it healthy." >&2
    # Same reasoning as above: this refusal must not tear down the other
    # surfaces this script already verified live.
    trap - EXIT
    exit 1
  fi
}

# For a surface this script does NOT forward (reviewer-ui: NodePort, mapped
# straight to a kind host port by deploy/kind/stage3.yaml/stage4 reusing it) --
# same two questions, without the PID-ownership check, since there is no PID
# of this script's to check against.
verify_existing_surface() {
  local port="$1" name="$2" path="$3"
  if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo -e "${RED}Error: nothing is listening on port ${port} (${name}).${NC}" >&2
    exit 1
  fi
  if ! curl -s -o /dev/null -m 5 "http://localhost:${port}${path}"; then
    echo -e "${RED}Error: port ${port} (${name}) is LISTENING but carries no request (ruling R20).${NC}" >&2
    exit 1
  fi
}

# This script is the universal remedy smoke-test.sh sends an operator to, so
# it must not forward a Service unconditionally: on a cluster below the stage
# that provides it, an unconditional forward for e.g. svc/opensearch-dashboards
# never binds, verify_owned_forward exits 1, and the EXIT trap then kills every
# good forward it had just created -- the remedy for a dead port-forward would
# leave the operator with no port-forwards at all.
#
# So the stage-gated surfaces -- reviewer-ui, OpenSearch Dashboards, Prometheus
# and the rest below -- are asked for rather than assumed, one `service_exists`
# call each below. The stage-1 surfaces below are NOT optional -- a cluster
# without them is broken, not merely younger -- and nothing here is silently
# skipped: each skip prints the stage that would provide it.
service_exists() {
  kubectl -n "$1" get "svc/$2" -o name >/dev/null 2>&1
}

DASHBOARDS_AVAILABLE=""
if service_exists observability opensearch-dashboards; then
  DASHBOARDS_AVAILABLE=1
fi
REVIEWER_UI_AVAILABLE=""
if service_exists northstar-agents reviewer-ui; then
  REVIEWER_UI_AVAILABLE=1
fi
PROMETHEUS_AVAILABLE=""
if service_exists observability prometheus; then
  PROMETHEUS_AVAILABLE=1
fi
WEAVER_AVAILABLE=""
if service_exists observability weaver-live-check; then
  WEAVER_AVAILABLE=1
fi
AGENTEVALS_AVAILABLE=""
if service_exists observability agentevals; then
  AGENTEVALS_AVAILABLE=1
fi

# flagd has no Service of its own (it is a sidecar injected into
# policy-decision-service's own pod, not a separate Deployment -- measured, no
# Service anywhere in this cluster exposes 8013/8014/8016), so
# `service_exists` cannot be the discriminator. The `FeatureFlag` object is:
# it is the resource the autonomy-contract manifest creates and the one thing
# that is genuinely absent below stage 5.
featureflag_exists() {
  kubectl -n "$1" get "featureflag/$2" -o name >/dev/null 2>&1
}
FLAGD_AVAILABLE=""
if featureflag_exists northstar-platform northstar-autonomy; then
  FLAGD_AVAILABLE=1
fi

# Backstage, the view over the platform API. Stage-6 only (svc/backstage
# does not exist below it), same `service_exists` discipline as the surfaces
# above.
BACKSTAGE_AVAILABLE=""
if service_exists northstar-platform backstage; then
  BACKSTAGE_AVAILABLE=1
fi

# Stage-7 pre-flight: the keystone. Two assertions, both made BEFORE any
# forward is started so a refusal here has nothing to tear down:
#
#   1. Kyverno is Ready. The policy fails closed (`failurePolicy: Fail`), so a
#      Kyverno that is down does not merely stop protecting anything -- it
#      also makes Backstage's flip of the autonomy flag (beat 7) fail, on
#      stage, with an admission error that looks like a Backstage bug.
#   2. The beat's over-grant is ABSENT. deploy/demo/keystone-overgrant.yaml
#      models a mistake; scripts/beat-keystone.sh applies it and takes it back.
#      One left behind by an aborted beat is a standing grant letting
#      settlement-agent patch the flag, which is exactly what this demo exists
#      to say never exists.
#
# Both are asked with `--ignore-not-found -o name` and judged on OUTPUT, not
# exit status: `kubectl get` exits 0 on some "found nothing" paths and the
# unit-test fakes answer every `get` with exit 0, so an exit-status check
# would read "present" everywhere. Output is the one thing an absent object
# cannot fake. Below stage 7 there is no ValidatingPolicy and both checks are
# skipped with a line saying which stage provides them.
KEYSTONE_POLICY="validatingpolicy/agents-cannot-edit-their-own-authority"
if [[ -n "$(kubectl get "$KEYSTONE_POLICY" --ignore-not-found -o name 2>/dev/null)" ]]; then
  KEYSTONE_READY="$(kubectl get "$KEYSTONE_POLICY" -o jsonpath='{.status.conditionStatus.ready}' 2>/dev/null || true)"
  if [[ "$KEYSTONE_READY" != "true" ]]; then
    echo -e "${RED}Error: the keystone policy is not Ready (conditionStatus.ready='${KEYSTONE_READY}').${NC}" >&2
    echo "It fails closed: with Kyverno down, Backstage's flag flip is refused too. Check: kubectl -n kyverno get pods; kubectl get ${KEYSTONE_POLICY} -o yaml" >&2
    exit 1
  fi
  # Ready on the policy is not enough. MEASURED: a graceful stop of the
  # admission controller (scale to 0) makes Kyverno delete its own webhook
  # configurations on the way out, after which an over-granted agent's write
  # SUCCEEDS -- fail-open -- while the policy object still exists. So also ask
  # for the registration and for something answering behind the Service.
  if [[ -z "$(kubectl get validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg --ignore-not-found -o name 2>/dev/null)" ]]; then
    echo -e "${RED}Error: Kyverno's resource webhook configuration is not registered, so the keystone is NOT enforcing (a stopped admission controller removes it, and requests then pass).${NC}" >&2
    echo "Bring Kyverno back: kubectl -n kyverno scale deploy/kyverno-admission-controller --replicas=1, then wait for the configuration to reappear." >&2
    exit 1
  fi
  if [[ -z "$(kubectl -n kyverno get endpointslices -l kubernetes.io/service-name=kyverno-svc -o jsonpath='{.items[*].endpoints[*].addresses[*]}' 2>/dev/null)" ]]; then
    echo -e "${RED}Error: nothing answers behind svc/kyverno-svc in the kyverno namespace.${NC}" >&2
    echo "The policy fails closed while registered: an agent's write is refused, and so is anything else it matches. Check: kubectl -n kyverno get pods" >&2
    exit 1
  fi
  if [[ -n "$(kubectl -n northstar-platform get role,rolebinding keystone-demo-overgrant --ignore-not-found -o name 2>/dev/null)" ]]; then
    echo -e "${RED}Error: the keystone demo over-grant is still applied (role/rolebinding keystone-demo-overgrant in northstar-platform).${NC}" >&2
    echo "A previous beat-keystone.sh run was interrupted. It is a standing grant letting settlement-agent patch the autonomy flag. Remove it: kubectl delete -f deploy/demo/keystone-overgrant.yaml" >&2
    exit 1
  fi
  echo -e "${GREEN}Keystone pre-flight: policy Ready, over-grant absent.${NC}"
else
  echo -e "${YELLOW}  - Keystone pre-flight: not installed, no ValidatingPolicy in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

echo -e "${BLUE}=== Starting Northstar port-forwards ===${NC}"

echo "  - claims-agent       (19001 -> 9001)"
kubectl -n northstar-agents port-forward svc/claims-agent 19001:9001 >/dev/null 2>&1 &
CLAIMS_PID=$!
PIDS+=("$CLAIMS_PID")

echo "  - settlement-agent   (19002 -> 9002)"
kubectl -n northstar-agents port-forward svc/settlement-agent 19002:9002 >/dev/null 2>&1 &
SETTLEMENT_PID=$!
PIDS+=("$SETTLEMENT_PID")

echo "  - payments-mcp       (18000 -> 8000)"
kubectl -n northstar-tools port-forward svc/payments-mcp 18000:8000 >/dev/null 2>&1 &
MCP_PID=$!
PIDS+=("$MCP_PID")

echo "  - payments-api       (18080 -> 8080)"
kubectl -n northstar-systems port-forward svc/payments-api 18080:8080 >/dev/null 2>&1 &
API_PID=$!
PIDS+=("$API_PID")

# Dashboards has no kind host port -- extraPortMappings is create-time only
# and this cluster was not recreated to add a fifth one -- so this is the
# ONLY way to reach it. Stage 4 installs it; below stage 4 it does not exist,
# and forwarding it anyway would leave the forward dangling on a cluster that
# has never bound it.
DASHBOARDS_PID=""
if [[ -n "$DASHBOARDS_AVAILABLE" ]]; then
  echo "  - OpenSearch Dashboards (5601 -> 5601)"
  kubectl -n observability port-forward svc/opensearch-dashboards 5601:5601 >/dev/null 2>&1 &
  DASHBOARDS_PID=$!
  PIDS+=("$DASHBOARDS_PID")
else
  echo -e "${YELLOW}  - OpenSearch Dashboards: SKIPPED, svc/opensearch-dashboards does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

# Prometheus has no kind host port either (same create-time
# `extraPortMappings` constraint as Dashboards), so a forward is the only way
# to reach it -- and it is stage 4 only, so it takes the same `service_exists`
# guard as Dashboards rather than one of the unconditional forwards above.
#
# 9090 -> 9090 and not an offset like the 1xxxx ports above: the Service's own
# port is 9090 (`server.service.servicePort` in
# deploy/observability/prometheus/values.yaml overrides the chart's default of
# 80), so the host side is kept identical to the cluster side, matching every
# other command in this repo that queries http://localhost:9090.
PROMETHEUS_PID=""
if [[ -n "$PROMETHEUS_AVAILABLE" ]]; then
  echo "  - Prometheus         (9090 -> 9090)"
  kubectl -n observability port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
  PROMETHEUS_PID=$!
  PIDS+=("$PROMETHEUS_PID")
else
  echo -e "${YELLOW}  - Prometheus: SKIPPED, svc/prometheus does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

# Weaver live-check's admin port (M9): the operator's door to the registry
# conformance report (`curl localhost:18400/report` after POST /stop). The gate
# does NOT use this forward -- a restart of the pod kills it, so
# tests/harness/livecheck.py opens its own. Stage 4 and up, on the same
# `service_exists` guard as the other stage-gated surfaces. 18400 -> 4320
# follows the 1xxxx offset convention of the tool ports.
WEAVER_PID=""
if [[ -n "$WEAVER_AVAILABLE" ]]; then
  # Live-check keeps every sample it receives in memory (measured 2026-10-17:
  # about 220 MiB/hour on an idle demo cluster, docs/telemetry-contract.md) and
  # is never reset between gates unless the gate runs. Starting a demo session
  # with an empty one bounds the growth to the session; the pod's 4Gi limit
  # covers about 17 hours between two start-demo runs. A failure here is only a
  # warning: weaver is the gate's, and the gate restarts it itself.
  echo "  - weaver live-check: restarting the pod so the session starts with an empty report"
  if ! kubectl -n observability rollout restart deployment/weaver-live-check >/dev/null 2>&1 \
     || ! kubectl -n observability rollout status deployment/weaver-live-check --timeout=120s >/dev/null 2>&1; then
    echo -e "${YELLOW}  - weaver live-check: the restart did not complete in 120s; continuing (the gate restarts it itself).${NC}"
  fi
  echo "  - weaver live-check  (18400 -> 4320)"
  kubectl -n observability port-forward svc/weaver-live-check 18400:4320 >/dev/null 2>&1 &
  WEAVER_PID=$!
  PIDS+=("$WEAVER_PID")
else
  echo -e "${YELLOW}  - weaver live-check: SKIPPED, svc/weaver-live-check does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

# AgentEvals (M11): its UI and REST API, where `scripts/demo evals` asks it to
# score the claims it has seen. Stage 4 and up, the same `service_exists` guard.
# 18001 -> 8001 follows the 1xxxx offset convention of the tool ports.
AGENTEVALS_PID=""
if [[ -n "$AGENTEVALS_AVAILABLE" ]]; then
  echo "  - AgentEvals         (18001 -> 8001)"
  kubectl -n observability port-forward svc/agentevals 18001:8001 >/dev/null 2>&1 &
  AGENTEVALS_PID=$!
  PIDS+=("$AGENTEVALS_PID")
else
  echo -e "${YELLOW}  - AgentEvals: SKIPPED, svc/agentevals does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

# flagd's own metrics, on its "management" port (8014) alongside `/healthz`
# -- both flagd's own naming, not this project's
# (deploy/k8s/policy-decision-service.yaml's openfeature.dev/* annotations
# wire the Open Feature Operator's mutating webhook to inject the sidecar
# with `--management-port 8014`). Genuinely worth forwarding, not merely
# possible to: measured live, `/metrics` carries
# `feature_flag_flagd_impression_total`, broken down by
# `feature_flag_result_variant` -- the only place in this system that shows
# how many evaluations resolved "assisted" versus "autonomous" without
# querying Prometheus or OpenSearch by hand.
#
# `deployment/policy-decision-service`, not a pod name: flagd is a sidecar
# inside that Deployment's own pod, not a separate Deployment or Service (no
# Service anywhere in this cluster exposes 8013/8014/8016 -- measured), and
# `kubectl port-forward deployment/NAME` resolves to whichever pod currently
# backs it, so this forward survives a rollout the same way `svc/NAME`
# already does for every other forward above.
FLAGD_METRICS_PID=""
if [[ -n "$FLAGD_AVAILABLE" ]]; then
  echo "  - flagd metrics      (18014 -> 8014)"
  kubectl -n northstar-platform port-forward deployment/policy-decision-service 18014:8014 >/dev/null 2>&1 &
  FLAGD_METRICS_PID=$!
  PIDS+=("$FLAGD_METRICS_PID")
else
  echo -e "${YELLOW}  - flagd metrics: SKIPPED, no northstar-autonomy FeatureFlag in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

# Backstage has no kind host port (its Service is ClusterIP-only,
# deploy/k8s/backstage.yaml) and no NodePort the way reviewer-ui has, so a
# forward is the only way to reach it -- same constraint as Dashboards and
# Prometheus above, stage-6-gated the same way.
#
# Started and tracked in PIDS exactly like every other forward this script
# owns (the kill-only-your-own-PIDs rule below is unchanged), but verified
# with `verify_existing_surface` rather than `verify_owned_forward`: the
# latter's PID-ownership check would refuse a cluster where an operator
# already has `kubectl -n northstar-platform port-forward svc/backstage
# 7007:7007` running by hand. `verify_existing_surface` asks the two
# questions that actually matter here --
# is the port bound, does it answer -- without caring which process bound it,
# so it accepts a healthy foreign port-forward on 7007 without this script
# needing to special-case one, and without weakening the rule that cleanup()
# only ever kills a PID this script itself started (a forward it did not
# start is never added to PIDS, so cleanup never touches it either way).
BACKSTAGE_PID=""
if [[ -n "$BACKSTAGE_AVAILABLE" ]]; then
  echo "  - Backstage          (7007 -> 7007)"
  kubectl -n northstar-platform port-forward svc/backstage 7007:7007 >/dev/null 2>&1 &
  BACKSTAGE_PID=$!
  PIDS+=("$BACKSTAGE_PID")
else
  echo -e "${YELLOW}  - Backstage: SKIPPED, svc/backstage does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

# Give the forwards a moment to establish before checking them (same
# allowance setup-kind.sh's own port-forward block uses).
sleep 4

echo ""
echo -e "${BLUE}Verifying every surface is actually live (not just listening)...${NC}"
verify_owned_forward 19001 "${CLAIMS_PID}" claims-agent "/.well-known/agent-card.json"
verify_owned_forward 19002 "${SETTLEMENT_PID}" settlement-agent "/.well-known/agent-card.json"
verify_owned_forward 18000 "${MCP_PID}" payments-mcp "/mcp"
verify_owned_forward 18080 "${API_PID}" payments-api "/healthz"
if [[ -n "$DASHBOARDS_PID" ]]; then
  verify_owned_forward 5601 "${DASHBOARDS_PID}" opensearch-dashboards "/"
fi
if [[ -n "$PROMETHEUS_PID" ]]; then
  # `/-/healthy` rather than `/`: with every scrape job disabled
  # (deploy/observability/prometheus/values.yaml) the root page still renders,
  # but the health endpoint is the one that answers without the UI bundle and
  # is what Prometheus itself documents as its liveness path.
  verify_owned_forward 9090 "${PROMETHEUS_PID}" prometheus "/-/healthy"
fi
if [[ -n "$WEAVER_PID" ]]; then
  # `/health` is Weaver's own admin liveness route (the Deployment's readiness
  # probe uses it too). Weaver is the gate's, not a demo surface (the gate opens
  # its own forward), so a forward that cannot bind or carries nothing is a
  # yellow warning here and never fails start-demo: verify_owned_forward would
  # exit 1 before the flagd and Backstage checks and the URL summary.
  if ! lsof -nP -iTCP:18400 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | grep -x "${WEAVER_PID}" >/dev/null \
     && ! curl -s -o /dev/null -m 5 "http://localhost:18400/health"; then
    echo -e "${YELLOW}  - weaver live-check: the 18400 forward never bound and whatever holds 18400 does not answer. Not a demo surface (the gate opens its own forward), so continuing. Check: lsof -nP -iTCP:18400 -sTCP:LISTEN${NC}"
    kill "${WEAVER_PID}" 2>/dev/null || true
    WEAVER_PID=""
  elif ! curl -s -o /dev/null -m 5 "http://localhost:18400/health"; then
    echo -e "${YELLOW}  - weaver live-check: port 18400 is listening but carries no request (a dead forward, or the pod was replaced). Not a demo surface, so continuing; the gate opens its own forward.${NC}"
    WEAVER_PID=""
  fi
fi
if [[ -n "$AGENTEVALS_PID" ]]; then
  # `/api/health` is the route its own chart probes with.
  verify_owned_forward 18001 "${AGENTEVALS_PID}" agentevals "/api/health"
fi
if [[ -n "$FLAGD_METRICS_PID" ]]; then
  # `/healthz`, not `/metrics`: flagd's management port serves both, and
  # `/healthz` is the cheap, dedicated liveness route every other surface
  # above is checked through, rather than pulling the full (measured: >500KB)
  # metrics payload just to confirm the forward answers.
  verify_owned_forward 18014 "${FLAGD_METRICS_PID}" flagd-metrics "/healthz"
fi
if [[ -n "$BACKSTAGE_PID" ]]; then
  # `/`, not an `/api/...` route: every `/api/*` route in this app requires a
  # Backstage credential (guest or otherwise) and answers 401 unauthenticated
  # by design -- a curl with no token would read a healthy backend as dead.
  # `/` is the served frontend bundle, which answers 200 with no credential,
  # the same reason Dashboards' own check above uses its bare root.
  verify_existing_surface 7007 backstage "/"
fi
# reviewer-ui: covered here for the same reason smoke-test.sh covers it --
# it is normally already live via deploy/kind/stage3.yaml's NodePort mapping,
# not a forward this script starts, but an operator running this script
# wants to know ALL the surfaces smoke-test.sh will check are actually up,
# not just the ones this script itself launched. Stage 3 installs it; on a
# stage-1/2 cluster there is nothing to verify.
if [[ -n "$REVIEWER_UI_AVAILABLE" ]]; then
  verify_existing_surface 8081 reviewer-ui "/healthz"
else
  # Name the CURRENT stage here, not the stage reviewer-ui first appears at
  # (3): stage 3 is a strict subset of the current cluster, and sending an
  # operator to build only as far as reviewer-ui's own first stage lands them
  # on a cluster where every one of
  # tests/e2e/test_opensearch_trace_ingest.py's guards skips, missing
  # everything a later stage added. `tests/unit/test_stage2_linkerd_config.py`'s
  # `test_no_operator_facing_instruction_still_points_at_an_older_stage`
  # enforces exactly this for every operator-facing instruction in this repo:
  # an instruction naming a stage older than `CURRENT_STAGE` is an offender
  # there regardless of whether that older stage happens to be sufficient for
  # the one surface being described, which is why this line must always track
  # `CURRENT_STAGE` and never a fixed literal.
  echo -e "${YELLOW}  - reviewer-ui: SKIPPED, svc/reviewer-ui does not exist in this cluster (it arrives with ./scripts/setup-kind.sh --stage 7)${NC}"
fi

echo ""
if [[ -n "$DASHBOARDS_AVAILABLE" && -n "$REVIEWER_UI_AVAILABLE" && -n "$PROMETHEUS_AVAILABLE" && -n "$WEAVER_AVAILABLE" && -n "$AGENTEVALS_AVAILABLE" && -n "$FLAGD_AVAILABLE" && -n "$BACKSTAGE_AVAILABLE" ]]; then
  echo -e "${GREEN}All surfaces verified up.${NC}"
else
  echo -e "${GREEN}Every surface this cluster has is verified up.${NC}"
  echo -e "${YELLOW}Some stage-gated surfaces are absent -- see the SKIPPED lines above.${NC}"
fi
echo ""
# docs/architecture.md's "See it yourself" table tells the reader to prefer
# this list when the two disagree, so this list names the Customer UI and
# Jaeger too, even though they are kind host ports mapped at cluster creation
# rather than forwards this script owns.
#
# Every line below must end in `#surface` or `#tool-port`, and
# tests/unit/test_stage4_config.py's agreement guard reads the marker rather
# than guessing. It cannot simply check "every printed URL is in the table",
# because this list deliberately also prints agent and tool ports the
# walkthrough never opens. So each line declares which it is, and the guard
# requires the `#surface` set to EQUAL the table's. A new echo here that
# carries neither marker fails the guard, which is the point: a URL an
# operator can reach is either a documented surface or an explicitly
# undocumented one, and nobody gets to skip the choice by adding a line.
echo "  Customer UI:               http://localhost:8080   (kind host port, not forwarded by this script)"  #surface
echo "  Jaeger:                    http://localhost:16686  (kind host port, not forwarded by this script)"  #surface
echo "  claims-agent (A2A):        http://localhost:19001"  #tool-port
echo "  settlement-agent (A2A):    http://localhost:19002"  #tool-port
echo "  payments-mcp:              http://localhost:18000/mcp"  #tool-port
echo "  payments-api:              http://localhost:18080"  #tool-port
if [[ -n "$WEAVER_PID" ]]; then
  echo "  weaver live-check (admin): http://localhost:18400"  #tool-port
fi
if [[ -n "$AGENTEVALS_PID" ]]; then
  echo "  AgentEvals:                http://localhost:18001"  #surface
fi
if [[ -n "$FLAGD_METRICS_PID" ]]; then
  # `#tool-port`, not `#surface`: nothing in docs/architecture.md's "See it yourself" walkthrough opens
  # this URL in a browser -- it is a diagnostic port for an operator who
  # wants to `curl` `feature_flag_flagd_impression_total` directly.
  echo "  flagd metrics:             http://localhost:18014/metrics"  #tool-port
fi
# Written as `if` blocks rather than `[[ ... ]] && echo ...`: under `set -e` a
# bare `test && cmd` list whose test fails is the classic way a script exits on
# a condition simply being false.
if [[ -n "$REVIEWER_UI_AVAILABLE" ]]; then
  echo "  reviewer-ui:               http://localhost:8081"  #surface
fi
if [[ -n "$DASHBOARDS_AVAILABLE" ]]; then
  echo "  OpenSearch Dashboards:     http://localhost:5601"  #surface
fi
if [[ -n "$PROMETHEUS_AVAILABLE" ]]; then
  echo "  Prometheus:                http://localhost:9090"  #surface
fi
if [[ -n "$BACKSTAGE_AVAILABLE" ]]; then
  echo "  Backstage:                 http://localhost:7007"  #surface
fi
echo ""
echo "Press Ctrl+C to stop the port-forwards this script started."

# Block so the trap has something to catch and the background forwards stay
# alive for as long as this script runs -- the same shape the reference
# script uses (there, blocking on `yarn start`; here, there is no foreground
# process of our own to hand off to, so wait on the forwards directly).
wait
