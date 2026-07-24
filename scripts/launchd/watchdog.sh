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
# Plan 147 Slice C: --probe-token-path is passed EXPLICITLY (mirroring the
# plist) — defensive, so this wrapper never silently depends on the argparse
# default happening to match the cwd. `cd` below (mirrored by the plist's
# WorkingDirectory) resolves the relative ./secrets/health_probe_token to the
# correct host secret file. See docs/standards/cicd.md § Access-token pepper +
# probe-token rotation.

set -e
cd /Users/sapphire/SAPPHIRE_flow
exec uv run --no-sync python -m sapphire_flow.ops.watchdog \
    --probe-token-path ./secrets/health_probe_token
