#!/bin/bash
# run-nepal-forcing.sh — launchd wrapper for the Plan 192 Stage B Nepal
# gateway-forcing feed (HRU 12300). Runs ONE fetch-and-store against the
# standing `sapphire-nepal` Postgres, then routes the run's JSONL record to a
# host log.
#
# Mirrors scripts/launchd/run-recap-probe.sh deliberately — same stdin-fed
# script mechanism (`scripts/` is never baked into the image; see Dockerfile),
# same stream-purity gate, same failure routing. If you change one, look at the
# other.
#
# Spec: docs/plans/192-recap-second-stack-12300-operational-test.md
# Runbook: docs/operations/nepal-forcing-runbook.md

# Deliberately NOT `-e`: this script must read the run's exit code and branch
# on it rather than abort on the first non-zero command.
set -uo pipefail

DOCKER="${DOCKER_CMD:-/usr/local/bin/docker}"
export DOCKER_HOST=unix:///var/run/docker.sock

# Host-side paths are env-overridable so the wrapper is testable off-host;
# production values are the defaults.
REPO="${NEPAL_REPO:-/Users/sapphire/SAPPHIRE_flow}"
RUN_SCRIPT="${NEPAL_RUN_SCRIPT:-${REPO}/scripts/nepal_forcing_run.py}"
KEY_FILE="${NEPAL_KEY_FILE:-/Users/sapphire/.config/sapphire/recap_api_key}"
DB_PASSWORD_FILE="${NEPAL_DB_PASSWORD_FILE:-${REPO}/secrets/nepal_db_password}"
HOST_JSONL="${NEPAL_HOST_LOG:-/Users/sapphire/Library/Logs/sapphire-nepal-forcing.jsonl}"
HOST_SUMMARY="${NEPAL_HOST_SUMMARY:-/Users/sapphire/Library/Logs/sapphire-nepal-forcing.summary.log}"
NETWORK="${NEPAL_NETWORK:-sapphire-nepal_nepal}"
# Track whatever image the Swiss stack currently runs, so a deploy carries this
# feed forward automatically instead of silently pinning it to a stale build.
# (Container-name coupling matches run-recap-probe.sh; a project rename breaks both.)
SWISS_CONTAINER="${NEPAL_IMAGE_SOURCE_CONTAINER:-sapphire_flow-prefect-worker-1}"

log() { printf '[run-nepal-forcing] %s\n' "$1" >&2; }

# --- Guards: never invoke docker with an absent/empty credential. -----------
if [[ ! -r "${KEY_FILE}" ]]; then log "key file not readable: ${KEY_FILE}"; exit 1; fi
KEY="$(cat "${KEY_FILE}")"
if [[ -z "${KEY}" ]]; then log "key file is empty: ${KEY_FILE}"; exit 1; fi

if [[ ! -r "${DB_PASSWORD_FILE}" ]]; then log "db password file not readable: ${DB_PASSWORD_FILE}"; exit 1; fi
DB_PASSWORD="$(cat "${DB_PASSWORD_FILE}")"
if [[ -z "${DB_PASSWORD}" ]]; then log "db password file is empty: ${DB_PASSWORD_FILE}"; exit 1; fi

if [[ ! -r "${RUN_SCRIPT}" ]]; then log "run script not readable: ${RUN_SCRIPT}"; exit 1; fi

IMAGE="${NEPAL_IMAGE:-}"
if [[ -z "${IMAGE}" ]]; then
    IMAGE="$("${DOCKER}" inspect --format '{{.Config.Image}}' "${SWISS_CONTAINER}" 2>/dev/null)"
fi
if [[ -z "${IMAGE}" ]]; then
    log "could not resolve image (is ${SWISS_CONTAINER} running? set NEPAL_IMAGE to override)"
    exit 1
fi

# --- Run, non-root, script fed via stdin. ----------------------------------
STDOUT_BUF="$(mktemp)"
STDERR_BUF="$(mktemp)"
trap 'rm -f "${STDOUT_BUF}" "${STDERR_BUF}"' EXIT

# NOTE: no `--user app` here. This is `docker run`, so the image ENTRYPOINT
# runs and drops to the non-root `app` user itself via gosu
# (docker/entrypoint.sh). Passing `--user app` makes the entrypoint fail with
# `failed switching to "app": operation not permitted` because it can no longer
# chown /run/secrets or gosu. (The recap PROBE wrapper does pass `--user app` —
# correctly, because `docker exec` BYPASSES the entrypoint and would otherwise
# run as root. Same invariant, opposite flag.)
"${DOCKER}" run --rm -i --workdir /tmp --network "${NETWORK}" \
    -e DB_PASSWORD="${DB_PASSWORD}" \
    -e DATABASE_URL_TEMPLATE=postgresql+psycopg://sapphire@postgres:5432/sapphire \
    -e RECAP_API_KEY="${KEY}" \
    -e SAPPHIRE_DATA_DIR=/tmp \
    -e NEPAL_FORCING_LOG=/dev/stderr \
    "${IMAGE}" python - \
    <"${RUN_SCRIPT}" >"${STDOUT_BUF}" 2>"${STDERR_BUF}"
EXIT_CODE=$?

# --- JSONL purity gate (same contract as the recap probe). ------------------
# Append to the host JSONL only if every non-empty buffered line parses as
# JSON. A stray warning must never corrupt the analysis log. Note the run's
# OWN failures are valid JSON records and DO get appended — only infra noise
# is diverted.
PURE=1
while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" ]] && continue
    if ! python3 -c "import json,sys; json.loads(sys.argv[1])" "${line}" >/dev/null 2>&1; then
        PURE=0
        break
    fi
done <"${STDERR_BUF}"

FAILED=0
if ! cat "${STDOUT_BUF}" >>"${HOST_SUMMARY}"; then
    log "failed to append summary to ${HOST_SUMMARY}"
    FAILED=1
fi

if [[ "${PURE}" -eq 1 ]]; then
    if ! cat "${STDERR_BUF}" >>"${HOST_JSONL}"; then
        log "failed to append JSONL to ${HOST_JSONL}"
        FAILED=1
    fi
else
    log "run output impure (exit=${EXIT_CODE}) — routing to launchd log, JSONL untouched"
    cat "${STDERR_BUF}" >&2
    FAILED=1
fi

# A non-zero run exit is a REAL failure (gateway down, short horizon, store
# error) and must surface to launchd, not just sit in the JSONL.
if [[ "${EXIT_CODE}" -ne 0 ]]; then
    log "run exited ${EXIT_CODE} — see ${HOST_JSONL}"
    FAILED=1
fi

[[ "${FAILED}" -eq 0 ]] && exit 0
exit 1
