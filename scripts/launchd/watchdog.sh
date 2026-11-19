#!/bin/bash
# watchdog.sh — thin wrapper that invokes the SAPPHIRE watchdog module.
# Used for manual invocation from the shell; the LaunchAgent invokes
# the installed launcher, which pins the environment for each tick.
#
# The wheel and small host-monitor dependency group are installed at deployment
# time. Ticks neither resolve dependencies nor import the mutable checkout.
#
# Plan 147 Slice C: --probe-token-path is passed EXPLICITLY (mirroring the
# plist) — defensive, so this wrapper never silently depends on the argparse
# default happening to match the cwd. `cd` below (mirrored by the plist's
# WorkingDirectory) resolves the relative ./secrets/health_probe_token to the
# correct host secret file. See docs/standards/cicd.md § Access-token pepper +
# probe-token rotation.

set -e
cd /Users/sapphire/SAPPHIRE_flow
exec /Users/sapphire/.local/share/sapphire-monitor/run-monitor \
    --probe-token-path ./secrets/health_probe_token \
    --deadman-url-path ./secrets/deadman_url
