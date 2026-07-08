#!/bin/bash
# install-launchd.sh — idempotent installer for the two SAPPHIRE
# LaunchAgents. Safe to re-run: if an agent is already loaded we
# bootout + bootstrap to apply any plist changes.
#
# Spec: docs/plans/046-mac-mini-staging-deployment.md §C1, §C3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
UID_VAL="$(id -u)"

PLISTS=(
    "ch.hydrosolutions.sapphire.plist"
    "ch.hydrosolutions.sapphire-watchdog.plist"
    "ch.hydrosolutions.sapphire-docker-prune.plist"
)

log() { printf '[install-launchd] %s\n' "$1"; }

mkdir -p "${AGENTS_DIR}"
mkdir -p "${HOME}/Library/Logs"

for plist in "${PLISTS[@]}"; do
    src="${SCRIPT_DIR}/${plist}"
    dst="${AGENTS_DIR}/${plist}"
    label="${plist%.plist}"

    if [ ! -f "${src}" ]; then
        log "ERROR: source plist not found: ${src}"
        exit 1
    fi

    log "validating ${plist}"
    if ! plutil -lint "${src}" >/dev/null; then
        log "ERROR: ${src} failed plutil lint"
        exit 1
    fi

    log "copying ${plist} -> ${dst}"
    cp "${src}" "${dst}"
    chmod 644 "${dst}"

    if launchctl print "gui/${UID_VAL}/${label}" >/dev/null 2>&1; then
        log "${label} already loaded; bootout + bootstrap for fresh state"
        launchctl bootout "gui/${UID_VAL}/${label}" 2>/dev/null || true
    fi

    log "bootstrap ${label}"
    launchctl bootstrap "gui/${UID_VAL}" "${dst}"
    launchctl enable "gui/${UID_VAL}/${label}"
done

log "done. Verify with: launchctl list | grep hydrosolutions"
