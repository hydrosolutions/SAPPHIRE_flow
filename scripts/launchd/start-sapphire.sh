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

# Host-side paths are env-overridable for tests (production values are the
# defaults, so production behaviour is unchanged) — same convention as
# scripts/launchd/run-recap-probe.sh's RECAP_PROBE_SCRIPT etc.
REPO_ROOT="${SAPPHIRE_REPO_ROOT:-/Users/sapphire/SAPPHIRE_flow}"
BACKUP_DIR="${SAPPHIRE_BACKUP_DIR:-/Volumes/sapphire-backup/pg_dumps}"

# --- Backup target device verification (Plan 194 D1) ------------------------
# Mirrors the identical trio of functions in scripts/bootstrap-mac-mini.sh —
# kept duplicated rather than factored into a shared sourced file: two
# ~25-line copies are simpler to reason about than a new library file, and
# the plan's exit gates shellcheck exactly these two scripts (Plan 194).
#
# `backup_target_verified` returns success only if ALL hold:
#   1. The backup directory itself exists (a missing directory is invalid —
#      see `_backup_mount_root_verified` below for the SEPARATE check that
#      lets a caller decide whether it's SAFE to create it).
#   2. The backup directory's OWN device id (fixer round: NOT its parent's
#      — a nested bind-mount underneath a genuinely distinct mount root,
#      e.g. `pg_dumps/` itself bind-mounted back onto the boot disk, would
#      satisfy a parent-only check while still landing dumps on the wrong
#      device) differs from the device id of the path that actually holds
#      the data (REPO_ROOT — never `/`; see Plan 194 D1 for why `/` is not
#      a reliable split on this host).
#   3. Its mount root (the backup directory's parent) is a REAL,
#      currently-mounted volume per `mount` — not merely a directory that
#      happens to report a different device id. Docker silently creates a
#      missing bind-mount host path, which is how an absent disk becomes a
#      healthy-looking plain directory.
_backup_target_device_id() {
    # BSD stat (macOS, the production host) uses `-f FORMAT`; GNU stat
    # (Linux, used only for off-host CI) uses `-c FORMAT` — confusingly,
    # GNU stat's `-f` means "filesystem status", a different mode entirely.
    local path="$1"
    case "$(uname -s)" in
        Darwin) stat -f %d "${path}" 2>/dev/null ;;
        *)      stat -c %d "${path}" 2>/dev/null ;;
    esac
}

# Verifies the MOUNT ROOT alone — deliberately does not require anything
# inside it to exist yet, so a caller can use this to decide whether it is
# safe to `mkdir -p` a not-yet-created backup subdirectory underneath a
# volume that is genuinely mounted (as opposed to creating that directory
# on the boot disk because the mount root itself is missing). This is
# ONLY the pre-creation authorization check — `backup_target_verified`
# below always re-checks the backup directory itself once it exists, and
# is the one that actually decides whether the target is trustworthy.
_backup_mount_root_verified() {
    local mount_root="$1"
    local data_dir="$2"
    local mount_dev data_dev

    [ -d "${mount_root}" ] || return 1

    mount_dev="$(_backup_target_device_id "${mount_root}")" || return 1
    data_dev="$(_backup_target_device_id "${data_dir}")" || return 1
    [ -n "${mount_dev}" ] && [ -n "${data_dev}" ] || return 1
    [ "${mount_dev}" != "${data_dev}" ] || return 1

    mount | grep -q " on ${mount_root} "
}

backup_target_verified() {
    local backup_dir="$1"
    local data_dir="$2"
    local mount_root backup_dev data_dev

    [ -d "${backup_dir}" ] || return 1
    mount_root="$(dirname "${backup_dir}")"

    backup_dev="$(_backup_target_device_id "${backup_dir}")" || return 1
    data_dev="$(_backup_target_device_id "${data_dir}")" || return 1
    [ -n "${backup_dev}" ] && [ -n "${data_dev}" ] || return 1
    [ "${backup_dev}" != "${data_dev}" ] || return 1

    mount | grep -q " on ${mount_root} "
}

# Allow tests to `source` this script and call `backup_target_verified`
# directly without running the Docker-wait + compose-up flow (Plan 194).
if [ "${BASH_SOURCE[0]}" != "${0}" ]; then
    return 0
fi

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

# --- Backup target device verification (Plan 194 D3) -------------------------
# Unlike bootstrap-mac-mini.sh (interactive, fails closed), this is a
# launchd-triggered stack start: refusing to bring up the forecasting stack
# because a removable backup disk is absent would trade a backup outage for
# a *forecasting* outage, the exact mistake this plan exists to avoid.
# Check, WARN, and proceed regardless.
#
# `set -e` safety: the check runs as an `if` CONDITION, which is exempt
# from `-e`. A bare `backup_target_verified ...` statement would abort this
# script before `exec docker compose` on an unverified volume — turning a
# backup problem into exactly the forecasting outage this check exists to
# avoid. Keep it in the `if`.
if ! backup_target_verified "${BACKUP_DIR}" "${REPO_ROOT}"; then
    echo "WARNING: backup volume not verified at ${BACKUP_DIR} — dumps may land on the boot disk." >&2
fi

exec docker compose \
    -f "${REPO_ROOT}/docker-compose.yml" \
    -f "${REPO_ROOT}/docker-compose.macmini.yml" \
    up -d
