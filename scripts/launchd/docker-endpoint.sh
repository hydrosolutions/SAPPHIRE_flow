# shellcheck shell=bash
# docker-endpoint.sh — single source of truth for the Docker CLI binary and
# daemon endpoint used by every launchd job (Plan 199 T2, salvaged from the
# never-merged Plan 158 D8/T4).
#
# SOURCED, not executed:
#   source "$(dirname "${BASH_SOURCE[0]}")/docker-endpoint.sh"
# from a script that already has `set -uo pipefail` (or the `-e` variant)
# active. Defines two things:
#
#   DOCKER_BIN   — the docker CLI binary to invoke.
#   DOCKER_HOST  — exported, so it applies to every subsequent docker/compose
#                  call in the sourcing script without threading it through
#                  every invocation explicitly.
#
# Today (Docker Desktop, unchanged behaviour): DOCKER_BIN defaults to
# /usr/local/bin/docker (the path Docker Desktop symlinks its CLI to) and
# DOCKER_HOST to Desktop's per-session unix socket. A future headless
# container runtime repoints BOTH by overriding SAPPHIRE_DOCKER_BIN /
# SAPPHIRE_DOCKER_HOST at the launchd EnvironmentVariables level — no script
# edits, and no change to the contract shape.
#
# DOCKER_CMD is preserved as the pre-existing test-injection seam (see
# tests/unit/ops/test_launchd_prune_docker.py, test_recap_probe_wrapper.py):
# a script using this contract resolves its docker binary as
# `DOCKER="${DOCKER_CMD:-${DOCKER_BIN}}"`, so a test pointing DOCKER_CMD at a
# fake stub still wins over both the contract and its override.
#
# Every launchd wrapper must source this file — enumerated here so a future
# wrapper is a deliberate addition, not a silent omission (Plan 199 T2
# review finding: the branch's own list had already gone stale against
# main): start-sapphire.sh, prune-docker.sh, run-recap-probe.sh,
# run-nepal-forcing.sh.
#
# Spec: docs/plans/archive/199-salvage-plan-158.md T2.

# shellcheck disable=SC2034  # consumed by the sourcing script, not here.
DOCKER_BIN="${SAPPHIRE_DOCKER_BIN:-/usr/local/bin/docker}"
export DOCKER_HOST="${SAPPHIRE_DOCKER_HOST:-unix:///var/run/docker.sock}"
