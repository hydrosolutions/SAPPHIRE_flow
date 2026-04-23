#!/bin/bash
# start-sapphire.sh — LaunchAgent wrapper that waits for Docker Desktop
# then brings up the SAPPHIRE Flow Mac-mini stack.
#
# KeepAlive in the plist is set to SuccessfulExit=false, so a non-zero
# exit here triggers a throttled restart (ThrottleInterval=60s). On
# cold-boot, Docker Desktop can take 90-120s to expose its socket on
# Apple Silicon (VirtioFS init + Linux VM kernel boot); 240s gives us
# enough headroom before we give up and let launchd retry.
#
# Spec: docs/plans/046-mac-mini-staging-deployment.md §C1.

set -e
WAIT_MAX=240
WAITED=0
until docker info >/dev/null 2>&1; do
    if [ "$WAITED" -ge "$WAIT_MAX" ]; then
        echo "Docker Desktop did not start within ${WAIT_MAX}s — aborting" >&2
        exit 1
    fi
    sleep 3
    WAITED=$((WAITED + 3))
done
exec docker compose \
    -f /Users/sapphire/SAPPHIRE_flow/docker-compose.yml \
    -f /Users/sapphire/SAPPHIRE_flow/docker-compose.macmini.yml \
    up -d
