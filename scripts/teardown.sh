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
# Two patterns, not one: this repo's own commands read "kubectl -n
# northstar-<ns> port-forward ..." ("northstar" before "port-forward"), while
# a hand-typed "kubectl port-forward -n northstar-agents ..." puts the two
# words in the other order. pkill's regex engine has no reliable alternation
# across BSD and GNU, so both orders are matched with two separate calls
# rather than one pattern. Neither pattern matches a forward for another
# cluster on this machine (e.g. "capybara"), which carries no "northstar"
# substring in either position.
pkill -f "northstar.*port-forward" 2>/dev/null || true
pkill -f "port-forward.*northstar" 2>/dev/null || true

echo -e "${BLUE}--- Deleting cluster '${CLUSTER_NAME}' ---${NC}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  kind delete cluster --name "$CLUSTER_NAME"
  echo -e "${GREEN}Cluster '${CLUSTER_NAME}' deleted.${NC}"
  # kind clears kubectl's current-context when it deletes a cluster; noted
  # here so a bare terminal afterward doesn't leave an operator wondering why
  # kubectl now errors or targets nothing.
  echo -e "${YELLOW}Note: kind cleared kubectl's current-context. If you use another cluster (e.g. capybara), run: kubectl config use-context kind-capybara${NC}"
else
  echo -e "${YELLOW}Cluster '${CLUSTER_NAME}' does not exist.${NC}"
fi

# The local registry and any other cluster (e.g. `capybara`) on this machine
# are shared and out of scope here — this script only ever touches the
# "northstar" cluster and this repo's own port-forwards, never the registry
# container or another cluster's forwards.
