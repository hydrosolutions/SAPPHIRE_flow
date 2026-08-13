#!/bin/bash
# start-sapphire.sh — LaunchAgent wrapper that waits for the Docker engine
# then brings up the SAPPHIRE Flow Mac-mini stack.
#
# KeepAlive in the plist is set to SuccessfulExit=false, so a non-zero
# exit here triggers a throttled restart (ThrottleInterval=60s). On
# cold-boot, Docker Desktop can take 90-120s to expose its socket on
# Apple Silicon (VirtioFS init + Linux VM kernel boot); 240s gives us
# enough headroom before we give up and let launchd retry.
#
# Spec: docs/plans/046-mac-mini-staging-deployment.md §C1;
#       docs/plans/158-session-independent-operational-stack.md D8/T4.

set -e

# Plan 158 D8/T4: single source of truth for the Docker binary + daemon
# endpoint (was a bare `docker`, resolved from launchd's minimal PATH — which
# does not reliably find a Homebrew-installed CLI, e.g. Colima's). DOCKER_CMD
# is preserved as the test-injection seam and wins over both.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=SCRIPTDIR/docker-endpoint.sh
source "${SCRIPT_DIR}/docker-endpoint.sh"
DOCKER="${DOCKER_CMD:-${DOCKER_BIN}}"

WAIT_MAX=240
WAITED=0
until "${DOCKER}" info >/dev/null 2>&1; do
    if [ "$WAITED" -ge "$WAIT_MAX" ]; then
        echo "Docker engine did not start within ${WAIT_MAX}s — aborting" >&2
        exit 1
    fi
    sleep 3
    WAITED=$((WAITED + 3))
done
exec "${DOCKER}" compose \
    -f /Users/sapphire/SAPPHIRE_flow/docker-compose.yml \
    -f /Users/sapphire/SAPPHIRE_flow/docker-compose.macmini.yml \
    up -d
