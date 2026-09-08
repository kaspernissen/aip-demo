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
# S1 (final-review.md): this pattern used to be "port-forward.*northstar",
# which cannot match this repo's own commands ("kubectl -n northstar-<ns>
# port-forward ..." — "northstar" precedes "port-forward"). The old pattern
# made this line announce success and kill nothing. Verified directly:
# `pgrep -f 'port-forward.*northstar'` finds nothing against live forwards
# from this repo; `pgrep -f 'northstar.*port-forward'` finds them. See
# scripts/setup-kind.sh for the matching fix.
pkill -f "northstar.*port-forward" 2>/dev/null || true

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
