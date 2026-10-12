#!/usr/bin/env bash
set -euo pipefail

# Beat K -- "the keystone". The agent can never edit what constrains it.
#
# RBAC already denies every agent ServiceAccount a write to the autonomy flag,
# so on a correct cluster the first attempt below is an RBAC `Forbidden` -- a
# true and useful first line. The keystone is the SECOND line, the one that
# holds when the first is wrong, so this script makes the first wrong in front
# of the audience, on purpose, and takes it back:
#
#   1. as settlement-agent, patch northstar-autonomy to `autonomous`
#      -> RBAC Forbidden (the first line);
#   2. apply deploy/demo/keystone-overgrant.yaml: "someone made a mistake";
#   3. the SAME patch again -> Kyverno denies it, and the message names the
#      policy (the second line). The policy name is asserted, so an RBAC
#      denial cannot pass for the keystone;
#   4. delete the over-grant;
#   5. print the flag's resolved level for settlement-agent: still `assisted`.
#
# The human path is beat 7 itself (Backstage's own identity, which the policy
# does not match), so this script does not repeat it.
#
# Exits non-zero if either denial does NOT fire. The over-grant is removed on
# every exit path, including Ctrl-C -- it models a mistake and must never be
# left standing (scripts/start-demo.sh's pre-flight refuses a cluster that has
# it). If the second patch were ever to SUCCEED, the flag is put back to
# `assisted` before the script reports the failure.

CONTEXT="kind-northstar"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OVERGRANT="$ROOT/deploy/demo/keystone-overgrant.yaml"
AGENT="system:serviceaccount:northstar-agents:settlement-agent"
POLICY="agents-cannot-edit-their-own-authority"
NS="northstar-platform"
FLAG="featureflag/northstar-autonomy"
# The one path Backstage's own write patches (k8sClient.ts TARGETING_THEN_PATH).
PATCH='[{"op":"replace","path":"/spec/flagSpec/flags/autonomy-level/targeting/if/1","value":"autonomous"}]'
REVERT='[{"op":"replace","path":"/spec/flagSpec/flags/autonomy-level/targeting/if/1","value":"assisted"}]'

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

if command -v timeout >/dev/null 2>&1; then
  TIMEOUT=(timeout)
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT=(gtimeout)
else
  echo -e "${RED}Error: needs \`timeout\` (brew install coreutils).${NC}" >&2
  exit 1
fi

kc() { "${TIMEOUT[@]}" 60 kubectl --context "$CONTEXT" "$@"; }
step() { echo; echo -e "${BLUE}$*${NC}"; }

cleanup() {
  # Idempotent, and never fatal: an over-grant that is already gone is fine.
  kc delete -f "$OVERGRANT" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Pre-flight: the keystone must be armed, and the over-grant must not already
# be standing (a stale one would make step 1 show Kyverno, not RBAC, and
# quietly rewrite the story).
READY="$(kc get validatingpolicy "$POLICY" -o jsonpath='{.status.conditionStatus.ready}' 2>/dev/null || true)"
if [[ "$READY" != "true" ]]; then
  echo -e "${RED}FAIL: validatingpolicy/${POLICY} is not Ready (got '${READY}'). Stage 7 not up? ./scripts/setup-kind.sh --stage 7${NC}" >&2
  trap - EXIT
  exit 1
fi
if [[ -n "$(kc -n "$NS" get role,rolebinding keystone-demo-overgrant --ignore-not-found -o name)" ]]; then
  echo -e "${RED}FAIL: the over-grant is already applied. Remove it first: kubectl delete -f deploy/demo/keystone-overgrant.yaml${NC}" >&2
  trap - EXIT
  exit 1
fi

echo -e "${BLUE}=== Beat K: the keystone ===${NC}"
echo "Acting as ${AGENT} (kubectl --as)."

step "1. The agent tries to flip its own autonomy flag to 'autonomous'"
echo "\$ kubectl --as=${AGENT} -n ${NS} patch ${FLAG} --type=json -p '<replace targeting.if[1] with autonomous>'"
set +e
OUT1="$(kc --as="$AGENT" -n "$NS" patch "$FLAG" --type=json -p "$PATCH" 2>&1)"
RC1=$?
set -e
echo "$OUT1" | sed 's/^/  /'
if [[ $RC1 -eq 0 ]]; then
  kc -n "$NS" patch "$FLAG" --type=json -p "$REVERT" >/dev/null
  echo -e "${RED}FAIL: the agent patched the flag with no over-grant. RBAC is wider than it should be. Flag restored to assisted.${NC}" >&2
  exit 1
fi
if [[ "$OUT1" != *Forbidden* ]]; then
  echo -e "${RED}FAIL: expected an RBAC Forbidden, got something else.${NC}" >&2
  exit 1
fi
echo -e "${GREEN}  RBAC says no. That is the first line.${NC}"

step "2. Someone makes a mistake: the agent is over-granted 'patch' on that one flag"
echo "\$ kubectl apply -f deploy/demo/keystone-overgrant.yaml"
kc apply -f "$OVERGRANT" | sed 's/^/  /'
# RBAC changes are not instantaneous for an impersonated request; wait until
# the authorizer agrees, so step 3 tests Kyverno rather than a race.
for _ in $(seq 1 20); do
  if [[ "$(kc --as="$AGENT" -n "$NS" auth can-i patch "$FLAG" 2>/dev/null || true)" == "yes" ]]; then
    break
  fi
  sleep 1
done
echo "\$ kubectl --as=${AGENT} auth can-i patch ${FLAG}"
echo "  $(kc --as="$AGENT" -n "$NS" auth can-i patch "$FLAG" 2>/dev/null || true)"

step "3. The same patch again. RBAC now allows it. Kyverno does not."
echo "\$ kubectl --as=${AGENT} -n ${NS} patch ${FLAG} --type=json -p '<replace targeting.if[1] with autonomous>'"
set +e
OUT3="$(kc --as="$AGENT" -n "$NS" patch "$FLAG" --type=json -p "$PATCH" 2>&1)"
RC3=$?
set -e
echo "$OUT3" | sed 's/^/  /'
if [[ $RC3 -eq 0 ]]; then
  kc -n "$NS" patch "$FLAG" --type=json -p "$REVERT" >/dev/null
  echo -e "${RED}FAIL: the agent changed its own authority. The keystone did not fire. Flag restored to assisted.${NC}" >&2
  exit 1
fi
if [[ "$OUT3" != *"$POLICY"* || "$OUT3" != *DENIED* ]]; then
  echo -e "${RED}FAIL: the patch was refused, but not by the keystone (no '${POLICY}' / DENIED in the message). An RBAC denial must not pass for it.${NC}" >&2
  exit 1
fi
echo -e "${GREEN}  Denied by Kyverno, naming the policy. That is the second line, and it did not need RBAC to be right.${NC}"

step "4. Take the mistake back"
echo "\$ kubectl delete -f deploy/demo/keystone-overgrant.yaml"
kc delete -f "$OVERGRANT" | sed 's/^/  /'

step "5. What does the flag resolve to now?"
echo "\$ kubectl -n ${NS} get ${FLAG} -o jsonpath='{... targeting.if[1]}'"
LEVEL="$(kc -n "$NS" get "$FLAG" -o jsonpath='{.spec.flagSpec.flags.autonomy-level.targeting.if[1]}')"
echo "  settlement-agent resolves: ${LEVEL}"
if [[ "$LEVEL" != "assisted" ]]; then
  echo -e "${RED}FAIL: the flag is '${LEVEL}', not 'assisted'.${NC}" >&2
  exit 1
fi

echo
echo -e "${GREEN}Beat K holds: the agent could not edit what constrains it, even with the grant.${NC}"
