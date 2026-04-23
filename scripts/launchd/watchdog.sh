#!/bin/bash
# watchdog.sh — thin wrapper that invokes the SAPPHIRE watchdog module.
# Used for manual invocation from the shell; the LaunchAgent invokes
# uv run python -m sapphire_flow.ops.watchdog directly (no wrapper).

set -e
cd /Users/sapphire/SAPPHIRE_flow
exec uv run python -m sapphire_flow.ops.watchdog
