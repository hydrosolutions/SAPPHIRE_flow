#!/usr/bin/env bash
# bootstrap-mac-mini.sh — one-command bootstrap for the SAPPHIRE Flow
# Mac-mini staging deployment.
#
# Idempotent; safe to re-run. On a fresh Mac mini (with Docker Desktop
# installed, external USB SSD attached, auto-login configured), this
# script:
#
#   1. Verifies prerequisites (Apple Silicon, Docker Desktop, uv).
#   2. Creates ./secrets/ with db_password (if absent). Detects
#      optional slack_webhook_url.
#   3. Checks the USB backup disk at /Volumes/sapphire-backup.
#   4. Checks the pre-staged CAMELS-CH dataset under ~/camels-ch.
#   5. Brings up the compose stack (macmini overlay).
#   6. Waits for /api/v1/health to return status=ok (up to 300s).
#   7. Installs the two LaunchAgents (main stack + watchdog).
#   8. Prints a final summary + any remaining manual steps.
#
# Flags:
#   --dry-run    Print every intended command but do nothing.
#   --uninstall  Bootout LaunchAgents and compose down (leaves
#                secrets + plists in place for manual cleanup).
#   --help       Show usage.
#
# Spec: docs/plans/046-mac-mini-staging-deployment.md §Stream C;
#       docs/v0-launch-roadmap.md §1.5b.

set -euo pipefail

# --- Colour helpers (respect NO_COLOR) ---------------------------------------
if [ -n "${NO_COLOR:-}" ] || [ ! -t 1 ]; then
    C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_BOLD=""
else
    C_RESET=$'\033[0m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'
    C_BOLD=$'\033[1m'
fi

DRY_RUN=0
UNINSTALL=0
FAILED_STEP=""

log()     { printf '%s[bootstrap]%s %s\n'     "${C_CYAN}"   "${C_RESET}" "$1"; }
success() { printf '%s[bootstrap] OK%s %s\n'  "${C_GREEN}"  "${C_RESET}" "$1"; }
warn()    { printf '%s[bootstrap] !!%s %s\n'  "${C_YELLOW}" "${C_RESET}" "$1"; }
fail()    { printf '%s[bootstrap] FAIL%s %s\n' "${C_RED}"   "${C_RESET}" "$1" >&2; }
hdr()     { printf '\n%s==> %s%s\n' "${C_BOLD}" "$1" "${C_RESET}"; }

usage() {
    cat <<'USAGE'
Usage: ./scripts/bootstrap-mac-mini.sh [--dry-run] [--uninstall] [--help]

  --dry-run    Print each intended command with "would run:"; does nothing.
  --uninstall  Bootout LaunchAgents and compose down.
  --help       Show this message.

See docs/deployment/mac-mini-staging.md for the full runbook.
USAGE
}

for arg in "$@"; do
    case "${arg}" in
        --dry-run)   DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --help|-h)   usage; exit 0 ;;
        *) warn "ignoring unknown argument: ${arg}" ;;
    esac
done

on_err() {
    fail "step failed: ${FAILED_STEP:-unknown}"
    fail "review the output above and re-run after resolving the issue."
}
trap on_err ERR

run() {
    # Runs a command, or prints "would run" under --dry-run.
    if [ "${DRY_RUN}" -eq 1 ]; then
        printf '%s[bootstrap] would run:%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*"
        return 0
    fi
    "$@"
}

# --- REPO_ROOT: script lives at <repo>/scripts/bootstrap-mac-mini.sh ---------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
EXPECTED_PATH="/Users/sapphire/SAPPHIRE_flow"
BACKUP_SENTINEL="/Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume"
CAMELS_CH_DIR="${HOME}/camels-ch"

# ============================================================================
# UNINSTALL
# ============================================================================
if [ "${UNINSTALL}" -eq 1 ]; then
    hdr "Uninstalling SAPPHIRE Mac-mini stack"
    UID_VAL="$(id -u)"
    for label in ch.hydrosolutions.sapphire ch.hydrosolutions.sapphire-watchdog; do
        plist="${HOME}/Library/LaunchAgents/${label}.plist"
        if [ -f "${plist}" ]; then
            log "bootout ${label}"
            run launchctl bootout "gui/${UID_VAL}/${label}" 2>/dev/null || true
        else
            log "no plist at ${plist} (skipping)"
        fi
    done
    log "docker compose down"
    if [ -f "${REPO_ROOT}/docker-compose.macmini.yml" ]; then
        run docker compose \
            -f "${REPO_ROOT}/docker-compose.yml" \
            -f "${REPO_ROOT}/docker-compose.macmini.yml" \
            down || true
    else
        run docker compose -f "${REPO_ROOT}/docker-compose.yml" down || true
    fi
    success "uninstall complete. Remove ~/Library/LaunchAgents/ch.hydrosolutions.*.plist"
    success "and ${REPO_ROOT}/secrets/ manually if you want a full wipe."
    exit 0
fi

# ============================================================================
# INSTALL
# ============================================================================
hdr "SAPPHIRE Flow — Mac-mini bootstrap"
log "repo: ${REPO_ROOT}"
if [ "${DRY_RUN}" -eq 1 ]; then
    warn "DRY RUN — no changes will be made"
fi

# --- Step 1: Apple Silicon check --------------------------------------------
FAILED_STEP="arch check"
hdr "1. Architecture check"
ARCH="$(uname -m)"
if [ "${ARCH}" != "arm64" ]; then
    fail "expected arm64 (Apple Silicon), got ${ARCH}."
    fail "Mac-mini deployment targets Apple Silicon only."
    exit 1
fi
success "Apple Silicon (${ARCH})"

# --- Step 2: Docker Desktop ---------------------------------------------------
FAILED_STEP="Docker Desktop check"
hdr "2. Docker Desktop"
if ! command -v docker >/dev/null 2>&1; then
    fail "docker CLI not found."
    fail "Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    fail "Then re-run ./scripts/bootstrap-mac-mini.sh"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    fail "docker CLI present but daemon not responding."
    fail "Start Docker Desktop (open /Applications/Docker.app), wait for it"
    fail "to finish booting, then re-run ./scripts/bootstrap-mac-mini.sh"
    exit 1
fi
success "Docker Desktop up"

# --- Step 3: Homebrew + uv ---------------------------------------------------
FAILED_STEP="Homebrew/uv check"
hdr "3. Homebrew + uv"
if ! command -v brew >/dev/null 2>&1; then
    log "Homebrew missing — installing"
    run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    success "Homebrew present"
fi
if ! command -v uv >/dev/null 2>&1; then
    log "uv missing — installing via brew"
    run brew install uv
else
    success "uv present ($(uv --version 2>&1 | head -1))"
fi

# --- Step 4: Repo path sanity check -----------------------------------------
FAILED_STEP="repo path check"
hdr "4. Repo path"
if [ "${REPO_ROOT}" != "${EXPECTED_PATH}" ]; then
    warn "repo at ${REPO_ROOT} but LaunchAgent plists reference ${EXPECTED_PATH}"
    warn "On the actual Mac mini this must be ${EXPECTED_PATH} or the"
    warn "LaunchAgents will not find the compose files. Continuing anyway"
    warn "(dev-machine run)."
else
    success "repo at expected path"
fi

# --- Step 5: Secrets bootstrap -----------------------------------------------
FAILED_STEP="secrets bootstrap"
hdr "5. Secrets"
run mkdir -p "${REPO_ROOT}/secrets"
run chmod 700 "${REPO_ROOT}/secrets"
DB_PASS_FILE="${REPO_ROOT}/secrets/db_password"
if [ ! -f "${DB_PASS_FILE}" ]; then
    log "generating secrets/db_password"
    if [ "${DRY_RUN}" -eq 0 ]; then
        openssl rand -base64 32 > "${DB_PASS_FILE}"
        chmod 600 "${DB_PASS_FILE}"
    else
        printf '%s[bootstrap] would run:%s openssl rand -base64 32 > %s\n' \
            "${C_YELLOW}" "${C_RESET}" "${DB_PASS_FILE}"
    fi
    success "db_password generated"
else
    success "db_password already present"
fi
SLACK_FILE="${REPO_ROOT}/secrets/slack_webhook_url"
if [ ! -f "${SLACK_FILE}" ] || [ ! -s "${SLACK_FILE}" ]; then
    warn "Slack webhook not configured — watchdog will run log-only."
    warn "To enable Slack later:"
    warn "  echo 'https://hooks.slack.com/services/...' > ${SLACK_FILE}"
    warn "  chmod 600 ${SLACK_FILE}"
    warn "  launchctl kickstart -k gui/\$(id -u)/ch.hydrosolutions.sapphire-watchdog"
else
    success "Slack webhook configured"
fi

# --- Step 6: USB backup disk -------------------------------------------------
FAILED_STEP="USB backup disk check"
hdr "6. USB backup disk"
if [ ! -e "${BACKUP_SENTINEL}" ]; then
    if [ "${DRY_RUN}" -eq 1 ]; then
        warn "USB backup sentinel absent at ${BACKUP_SENTINEL}"
        warn "(dry-run: would abort here; continuing to show remaining steps)"
    else
        fail "USB backup sentinel absent at ${BACKUP_SENTINEL}"
        fail "Attach the external USB SSD (APFS, >= 500 GB) mounted at"
        fail "/Volumes/sapphire-backup, then:"
        fail "  mkdir -p /Volumes/sapphire-backup/pg_dumps"
        fail "  touch /Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume"
        fail "and re-run ./scripts/bootstrap-mac-mini.sh"
        exit 1
    fi
else
    success "USB backup sentinel present"
fi

# --- Step 7: CAMELS-CH staging ----------------------------------------------
FAILED_STEP="CAMELS-CH staging check"
hdr "7. CAMELS-CH dataset"
if [ ! -d "${CAMELS_CH_DIR}" ]; then
    if [ "${DRY_RUN}" -eq 1 ]; then
        warn "${CAMELS_CH_DIR} missing"
        warn "(dry-run: would abort here; continuing to show remaining steps)"
    else
        fail "${CAMELS_CH_DIR} missing."
        fail "Download CAMELS-CH (>= v1.0) and extract so that"
        fail "${CAMELS_CH_DIR}/CAMELS_CH/ exists with the full time-series tree."
        fail "Then re-run ./scripts/bootstrap-mac-mini.sh"
        exit 1
    fi
elif [ -z "$(ls -A "${CAMELS_CH_DIR}" 2>/dev/null || true)" ]; then
    if [ "${DRY_RUN}" -eq 1 ]; then
        warn "${CAMELS_CH_DIR} is empty"
        warn "(dry-run: would abort here; continuing)"
    else
        fail "${CAMELS_CH_DIR} is empty. Extract CAMELS-CH there."
        exit 1
    fi
else
    success "CAMELS-CH present"
fi

# --- Step 8: VERSION ----------------------------------------------------------
FAILED_STEP="VERSION resolve"
hdr "8. Version tag"
if [ -z "${VERSION:-}" ]; then
    VERSION="latest"
    warn "VERSION unset — defaulting to 'latest' (not a pinned release)"
    warn "For production, export VERSION=vX.Y.Z before bootstrap."
else
    success "VERSION=${VERSION}"
fi
export VERSION

# --- Step 9: Compose up -------------------------------------------------------
FAILED_STEP="docker compose up"
hdr "9. docker compose up -d"
run docker compose \
    -f "${REPO_ROOT}/docker-compose.yml" \
    -f "${REPO_ROOT}/docker-compose.macmini.yml" \
    up -d
success "compose up issued"

# --- Step 10: wait for health ------------------------------------------------
FAILED_STEP="health wait"
hdr "10. Waiting for /api/v1/health to return status=ok (max 300s)"
if [ "${DRY_RUN}" -eq 1 ]; then
    printf '%s[bootstrap] would run:%s health-check loop\n' \
        "${C_YELLOW}" "${C_RESET}"
else
    HEALTH_OK=0
    for _ in $(seq 1 60); do
        if curl -sf --max-time 5 http://localhost:8000/api/v1/health \
                2>/dev/null | jq -e '.status == "ok"' >/dev/null 2>&1; then
            HEALTH_OK=1
            printf '\n'
            break
        fi
        printf '.'
        sleep 5
    done
    if [ "${HEALTH_OK}" -ne 1 ]; then
        fail "health check did not return status=ok within 300s"
        fail "Check docker logs: docker compose logs api prefect-server"
        exit 1
    fi
    success "API healthy"
fi

# --- Step 11: LaunchAgent install --------------------------------------------
FAILED_STEP="LaunchAgent install"
hdr "11. LaunchAgent install"
run "${REPO_ROOT}/scripts/launchd/install-launchd.sh"
success "LaunchAgents installed"

# --- Step 12: final report ---------------------------------------------------
hdr "12. Summary"
printf '  %sStack:%s   docker compose -f docker-compose.yml -f docker-compose.macmini.yml\n' \
    "${C_BOLD}" "${C_RESET}"
printf '  %sAPI:%s     http://localhost:8000/api/v1/\n' "${C_BOLD}" "${C_RESET}"
printf '  %sPrefect:%s http://localhost:4200 (via SSH tunnel from team laptops)\n' \
    "${C_BOLD}" "${C_RESET}"
printf '\n'
if [ "${DRY_RUN}" -eq 0 ]; then
    log "LaunchAgents (expect both listed):"
    launchctl list 2>/dev/null | grep hydrosolutions || warn "(none found)"
fi
printf '\n'
printf '  %sNext steps:%s\n' "${C_BOLD}" "${C_RESET}"
printf '    * curl http://localhost:8000/api/v1/stations | jq . -- confirm empty DB\n'
printf '    * Run the Plan 046 §D2 5-station rehearsal from the runbook\n'
printf '    * Set up SSH tunnel from team laptops (docs/deployment/mac-mini-staging.md)\n'

if [ "${DRY_RUN}" -eq 0 ]; then
    success "bootstrap complete"
else
    success "dry run complete — no changes made"
fi
