#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="northstar"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}--- Stopping this repo's port-forwards ---${NC}"
# S1 (final-review.md, first round): this pattern used to be
# "port-forward.*northstar", which cannot match this repo's own commands
# ("kubectl -n northstar-<ns> port-forward ..." — "northstar" precedes
# "port-forward"). The old pattern made this line announce success and kill
# nothing. Fixed to "northstar.*port-forward" — verified directly against a
# live forward.
#
# Second round (final-review.md, nit 4): that single-order fix is itself
# order-sensitive, so a hand-typed "kubectl port-forward -n northstar-agents
# ..." — the form the plan document itself uses, and so the form a reader is
# likely to type — still escapes cleanup, since there "port-forward"
# precedes "northstar". Two calls instead of one regex, one per order,
# cover both without needing alternation support in pkill's regex engine
# (unavailable/unreliable across BSD and GNU pkill). Re-verified after this
# change, live: both call orders correctly kill this repo's forwards
# regardless of which way they were typed, and neither ever matches a
# `capybara`-cluster forward (which contains no "northstar" substring in
# either position).
pkill -f "northstar.*port-forward" 2>/dev/null || true
pkill -f "port-forward.*northstar" 2>/dev/null || true

echo -e "${BLUE}--- Deleting cluster '${CLUSTER_NAME}' ---${NC}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  kind delete cluster --name "$CLUSTER_NAME"
  echo -e "${GREEN}Cluster '${CLUSTER_NAME}' deleted.${NC}"
  # N7: kind's own behaviour, not something this script does — but worth
  # saying, since a bare terminal afterward gives no hint why kubectl now
  # errors or targets nothing.
  echo -e "${YELLOW}Note: kind cleared kubectl's current-context. If you use another cluster (e.g. capybara), run: kubectl config use-context kind-capybara${NC}"
else
  echo -e "${YELLOW}Cluster '${CLUSTER_NAME}' does not exist.${NC}"
fi

# A1/A9: the local registry and any other cluster (e.g. `capybara`) on this
# machine are shared and out of scope here — this script only ever touches
# the "northstar" cluster and this repo's own port-forwards, never the
# registry container or another cluster's forwards.
