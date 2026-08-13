#!/bin/bash
# install-launchd.sh — idempotent installer for the SAPPHIRE LaunchAgents /
# LaunchDaemons. Safe to re-run: if a label is already loaded it boots out +
# re-bootstraps to apply any plist changes.
#
# Domain concept (Plan 158 D2/D2b): each plist is installed into either the
# per-user "agent" domain (gui/<uid> — dies at logout) or the system-wide
# "daemon" domain (survives logout/reboot, requires root). A daemon install
# NEVER escalates privileges itself: it refuses and tells the operator to
# re-run under sudo. Before a system bootstrap it boots out any stale
# gui/<uid>/<label> registration, so one label is never live in two domains
# at once.
#
# --dry-run prints the install decisions (which domain each label targets,
# the privileged-step reminder for the daemon) and calls neither plutil nor
# launchctl — it is a pure preview, safe to run anywhere (including CI,
# which has neither binary). It does NOT enumerate every action a real run
# performs (validation, directory creation, existing-registration
# bootout/reload, `launchctl enable`) — treat it as a routing preview, not a
# byte-for-byte transcript of a real install.
#
# Spec: docs/plans/046-mac-mini-staging-deployment.md §C1, §C3;
#       docs/plans/158-session-independent-operational-stack.md D2/D2b/T2.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
DAEMONS_DIR="/Library/LaunchDaemons"
# When re-invoked via `sudo`, $(id -u)/$HOME resolve to root, not the
# operator — SUDO_UID is the operator's real uid, needed so gui/<uid> checks
# (stale-bootout, "already loaded") target the right session. Falls back to
# `id -u` when not under sudo.
UID_VAL="${SUDO_UID:-$(id -u)}"

PLISTS=(
    "ch.hydrosolutions.sapphire.plist"
    "ch.hydrosolutions.sapphire-watchdog.plist"
    "ch.hydrosolutions.sapphire-docker-prune.plist"
)

# Per-label launchd domain. "daemon" survives logout/reboot; "agent" does
# not. Plan 158 D2: the watchdog moves to the system domain FIRST — it is
# the piece that must report an outage it does not itself cause. The
# stack-starter and prune jobs stay "agent" here; their conversion is D5/T5,
# a separate host cutover (out of scope for this installer change).
domain_for_label() {
    case "$1" in
        ch.hydrosolutions.sapphire-watchdog) printf 'daemon\n' ;;
        *) printf 'agent\n' ;;
    esac
}

log() { printf '[install-launchd] %s\n' "$1"; }

usage() {
    cat <<'USAGE'
Usage: ./scripts/launchd/install-launchd.sh [--dry-run] [--label <label>]

  --dry-run        Preview the domain routing (agent vs. daemon) for each
                    label; calls neither plutil nor launchctl and writes
                    nothing. Not a full transcript of every action a real
                    run performs.
  --label <label>  Install only this one label (e.g.
                    ch.hydrosolutions.sapphire-watchdog) instead of every
                    entry in PLISTS.
  --help           Show this message.

A "daemon"-domain label (the watchdog) requires root and is NEVER installed
by escalating privileges automatically — re-run the whole command with sudo.
Run without --label, a daemon-domain label is SKIPPED (with a warning) when
not root, so a routine agent-only re-run still succeeds.
USAGE
}

DRY_RUN=0
ONLY_LABEL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --label)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                log "ERROR: --label requires a value"
                exit 1
            fi
            ONLY_LABEL="$2"
            shift 2
            ;;
        --label=*)
            ONLY_LABEL="${1#--label=}"
            if [ -z "${ONLY_LABEL}" ]; then
                log "ERROR: --label requires a value"
                exit 1
            fi
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log "ERROR: unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

# A root FULL sweep (no --label) is refused outright: under sudo, $HOME
# resolves to root's home, so the agent-domain plists in this sweep would
# be copied under /var/root/Library/LaunchAgents while being bootstrapped
# into gui/<SUDO_UID> (the real operator's session) — they would load once,
# immediately, but never again after the next logout/reboot, since launchd
# re-scans ~/Library/LaunchAgents under the REAL operator's $HOME, not
# root's. Split into two runs instead: an unprivileged full sweep (agent
# jobs only, daemon skipped with a warning — see install_daemon below) plus
# a privileged --label for the one daemon-domain job.
if [ "$(id -u)" -eq 0 ] && [ -z "${ONLY_LABEL}" ]; then
    log "ERROR: refusing a root full sweep (no --label given)."
    log "  Under sudo, \$HOME resolves to root's home, so agent-domain"
    log "  plists would be installed under the wrong user's LaunchAgents"
    log "  and would not survive the next login/reboot."
    log "  Run in two steps instead:"
    log "    1. (unprivileged) ${SCRIPT_DIR}/install-launchd.sh"
    log "    2. (privileged)   sudo ${SCRIPT_DIR}/install-launchd.sh --label ch.hydrosolutions.sapphire-watchdog"
    exit 1
fi

if [ "${DRY_RUN}" -eq 0 ]; then
    mkdir -p "${HOME}/Library/Logs"
fi

# Plan 147 Slice C: the watchdog reads its admin probe token from
# ./secrets/health_probe_token, a HOST secret file (NOT a Docker/Compose
# mount). The plist passes --probe-token-path ./secrets/health_probe_token
# EXPLICITLY (resolved against WorkingDirectory — see the plist comment +
# docs/standards/cicd.md § Access-token pepper + probe-token rotation).
# Warn (don't fail): this installer also runs before the token-CLI
# bootstrap on a fresh host.
if [ ! -f "${SCRIPT_DIR}/../../secrets/health_probe_token" ]; then
    log "WARNING: ./secrets/health_probe_token not found — the watchdog's"
    log "  BAFU-freshness probe will degrade to found=False (401) until it"
    log "  is created. See docs/standards/cicd.md § Access-token pepper +"
    log "  probe-token rotation."
fi

# --- Resolve targets (full PLISTS sweep, or a single --label) --------------
TARGETS=("${PLISTS[@]}")
if [ -n "${ONLY_LABEL}" ]; then
    TARGETS=()
    for plist in "${PLISTS[@]}"; do
        if [ "${plist%.plist}" = "${ONLY_LABEL}" ]; then
            TARGETS=("${plist}")
        fi
    done
    if [ "${#TARGETS[@]}" -eq 0 ]; then
        log "ERROR: --label ${ONLY_LABEL} does not match any entry in PLISTS"
        exit 1
    fi
fi

install_agent() {
    local plist="$1" label="$2" src="$3"
    local dst="${AGENTS_DIR}/${plist}"

    if [ "${DRY_RUN}" -eq 1 ]; then
        log "[dry-run] ${label}: install as LaunchAgent -> gui/${UID_VAL}"
        log "[dry-run]   cp ${src} ${dst}; chmod 644 ${dst}"
        log "[dry-run]   launchctl bootstrap gui/${UID_VAL} ${dst}"
        return 0
    fi

    log "validating ${plist}"
    if ! plutil -lint "${src}" >/dev/null; then
        log "ERROR: ${src} failed plutil lint"
        exit 1
    fi

    mkdir -p "${AGENTS_DIR}"
    log "copying ${plist} -> ${dst}"
    cp "${src}" "${dst}"
    chmod 644 "${dst}"

    if launchctl print "gui/${UID_VAL}/${label}" >/dev/null 2>&1; then
        log "${label} already loaded (gui); bootout + bootstrap for fresh state"
        launchctl bootout "gui/${UID_VAL}/${label}" 2>/dev/null || true
    fi

    log "bootstrap gui/${UID_VAL}/${label}"
    launchctl bootstrap "gui/${UID_VAL}" "${dst}"
    launchctl enable "gui/${UID_VAL}/${label}"
}

install_daemon() {
    local plist="$1" label="$2" src="$3"
    local dst="${DAEMONS_DIR}/${plist}"

    if [ "${DRY_RUN}" -eq 1 ]; then
        log "[dry-run] ${label}: install as LaunchDaemon -> system (REQUIRES ROOT)"
        log "[dry-run]   boot out stale gui/${UID_VAL}/${label} if present"
        log "[dry-run]   cp ${src} ${dst}; chown root:wheel ${dst}; chmod 644 ${dst}"
        log "[dry-run]   launchctl bootstrap system ${dst}"
        log "[dry-run]   NOT run automatically — re-run under sudo:"
        log "[dry-run]     sudo ${SCRIPT_DIR}/install-launchd.sh --label ${label}"
        return 0
    fi

    if [ "$(id -u)" -ne 0 ]; then
        if [ -n "${ONLY_LABEL}" ]; then
            log "ERROR: ${label} is a LaunchDaemon (system domain) and requires root."
            log "  This installer refuses to escalate privileges itself."
            log "  Re-run:  sudo ${SCRIPT_DIR}/install-launchd.sh --label ${label}"
            exit 1
        fi
        log "WARNING: skipping ${label} — LaunchDaemon (system domain) requires root."
        log "  This installer refuses to escalate privileges itself. Run once,"
        log "  explicitly, when ready:"
        log "    sudo ${SCRIPT_DIR}/install-launchd.sh --label ${label}"
        return 0
    fi

    log "validating ${plist}"
    if ! plutil -lint "${src}" >/dev/null; then
        log "ERROR: ${src} failed plutil lint"
        exit 1
    fi

    if launchctl print "gui/${UID_VAL}/${label}" >/dev/null 2>&1; then
        log "boot out stale gui/${UID_VAL}/${label} before system bootstrap"
        if ! launchctl bootout "gui/${UID_VAL}/${label}"; then
            log "ERROR: failed to boot out stale gui/${UID_VAL}/${label}."
            log "  Refusing to bootstrap system/${label} while a gui/<uid>"
            log "  registration may still be active — installing both would"
            log "  race the same state file and duplicate Slack/dead-man"
            log "  traffic (Plan 158's one-domain invariant). Resolve the"
            log "  stale gui registration manually, then re-run:"
            log "    sudo ${SCRIPT_DIR}/install-launchd.sh --label ${label}"
            exit 1
        fi
        # Belt-and-suspenders: bootout can report success (exit 0) while
        # the registration is still visible for a beat, or a raced
        # re-registration can slip back in. Verify it is actually gone
        # before proceeding to the system-domain bootstrap.
        if launchctl print "gui/${UID_VAL}/${label}" >/dev/null 2>&1; then
            log "ERROR: gui/${UID_VAL}/${label} still registered after bootout."
            log "  Refusing to bootstrap system/${label} while both domains"
            log "  could end up active at once. Resolve the stale gui"
            log "  registration manually, then re-run:"
            log "    sudo ${SCRIPT_DIR}/install-launchd.sh --label ${label}"
            exit 1
        fi
    fi

    log "copying ${plist} -> ${dst}"
    cp "${src}" "${dst}"
    chown root:wheel "${dst}"
    chmod 644 "${dst}"

    if launchctl print "system/${label}" >/dev/null 2>&1; then
        log "${label} already loaded (system); bootout + bootstrap for fresh state"
        launchctl bootout "system/${label}" 2>/dev/null || true
    fi

    log "bootstrap system/${label}"
    launchctl bootstrap system "${dst}"
    launchctl enable "system/${label}"
}

for plist in "${TARGETS[@]}"; do
    src="${SCRIPT_DIR}/${plist}"
    label="${plist%.plist}"

    if [ ! -f "${src}" ]; then
        log "ERROR: source plist not found: ${src}"
        exit 1
    fi

    domain="$(domain_for_label "${label}")"
    case "${domain}" in
        daemon) install_daemon "${plist}" "${label}" "${src}" ;;
        agent)  install_agent  "${plist}" "${label}" "${src}" ;;
        *)
            log "ERROR: unknown domain '${domain}' for ${label}"
            exit 1
            ;;
    esac
done

if [ "${DRY_RUN}" -eq 1 ]; then
    log "dry-run complete — nothing written, plutil/launchctl not invoked"
else
    log "done. Verify agents with: launchctl list | grep hydrosolutions"
    log "     verify daemons with: sudo launchctl print system/<label>"
fi
