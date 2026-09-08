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
