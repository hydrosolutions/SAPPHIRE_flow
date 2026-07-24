#!/bin/bash
# watchdog.sh — thin wrapper that invokes the SAPPHIRE watchdog module.
# Used for manual invocation from the shell; the LaunchAgent invokes
# uv run --no-sync python -m sapphire_flow.ops.watchdog directly (no wrapper).
#
# --no-sync: this host cannot clone the private recap-dg-client git-pin, so a
# plain `uv run` (implicit sync) fails/hangs. The watchdog module needs only
# httpx/structlog/sapphire_flow, all already present in the venv, so we run
# against the existing venv without syncing.
#
# Plan 147 Slice C: no --probe-token-path flag is passed here (or in the
# plist) DELIBERATELY. `read_probe_token`'s default
# (`./secrets/health_probe_token`, ops/watchdog.py:DEFAULT_PROBE_TOKEN_PATH)
# is relative, and `cd` below (mirrored by the plist's WorkingDirectory)
# already resolves it to the correct host secret file — no code/plist
# change needed. See docs/standards/cicd.md § Access-token pepper +
# probe-token rotation.

set -e
cd /Users/sapphire/SAPPHIRE_flow
exec uv run --no-sync python -m sapphire_flow.ops.watchdog
