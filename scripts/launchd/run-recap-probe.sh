#!/bin/bash
# run-recap-probe.sh — launchd wrapper for the recap Data Gateway
# availability probe. Hardcoded to the one host (mac mini, user
# `sapphire`) and container (`sapphire_flow-prefect-worker-1`) it runs
# against — no portability indirection for a single-host experiment
# (mirrors how start-sapphire.sh hardcodes /Users/sapphire/SAPPHIRE_flow).
#
# `docker exec`s the probe (scripts/recap_probe_loop.py, fed via stdin —
# `scripts/` is never baked into the image; see Dockerfile / Plan 122) into
# the running prefect-worker container as the non-root `app` user, then
# routes its output: container stdout (the terse summary) always appends
# to the host summary log; container stderr (JSONL) appends to the host
# JSONL log ONLY if the exec exits 0 AND every non-empty stderr line
# parses as JSON. Otherwise the raw buffer + a banner go to this script's
# own stderr (-> the launchd log), the JSONL is left untouched, and the
# script exits non-zero. An infra failure (Docker error, ImportError,
# missing key) or a stray warning line must never write non-JSON into the
# JSONL — it would break the pandas analysis.
#
# Spec: docs/plans/132-recap-probe-deployment-reconciliation.md §1.

# Deliberately NOT `-e`: this script must read the `docker exec` exit code
# and branch on it (append to the JSONL only on a clean, pure run;
# otherwise route the failure to the launchd log) rather than abort on the
# first non-zero command.
set -uo pipefail

# Docker binary: absolute path in production (Docker Desktop symlinks its
# CLI there). DOCKER_CMD lets tests inject a fake docker stub — the same
# mechanism tests/unit/ops/test_launchd_prune_docker.py uses, not PATH
# injection.
DOCKER="${DOCKER_CMD:-/usr/local/bin/docker}"

# The value the live deployment uses and that produced records under
# launchd on 2026-07-20 (proven).
export DOCKER_HOST=unix:///var/run/docker.sock

CONTAINER="sapphire_flow-prefect-worker-1"

# Host-side paths are env-overridable for tests (production values are the
# defaults, so production behaviour is unchanged). These override only the
# *host-side* paths; the container-side RECAP_PROBE_LOG=/dev/stderr below is
# fixed. RECAP_PROBE_SCRIPT is included here (beyond the KEY_FILE/HOST_JSONL/
# HOST_SUMMARY set) because it is a host-side path fed to the container via
# stdin redirection: on a non-mac-mini test host the hardcoded production
# path does not exist, and a missing redirection target fails in the shell
# before docker is even invoked, so it must be overridable for the wrapper
# to be testable off-host (e.g. Linux CI).
PROBE_SCRIPT="${RECAP_PROBE_SCRIPT:-/Users/sapphire/SAPPHIRE_flow/scripts/recap_probe_loop.py}"
KEY_FILE="${RECAP_PROBE_KEY_FILE:-/Users/sapphire/.config/sapphire/recap_api_key}"
HOST_JSONL="${RECAP_PROBE_HOST_LOG:-/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl}"
HOST_SUMMARY="${RECAP_PROBE_HOST_SUMMARY:-/Users/sapphire/Library/Logs/sapphire-recap-probe.summary.log}"

log() { printf '[run-recap-probe] %s\n' "$1" >&2; }

# --- Key-file guard first. -----------------------------------------------
# With `set -e` off, `$(cat "$KEY_FILE")` on a missing/unreadable/empty
# file silently yields an empty string and the probe would otherwise run
# with no key. Never invoke docker with an absent/empty key.
if [[ ! -r "${KEY_FILE}" ]]; then
    log "key file not readable: ${KEY_FILE}"
    exit 1
fi
KEY="$(cat "${KEY_FILE}")"
if [[ -z "${KEY}" ]]; then
    log "key file is empty: ${KEY_FILE}"
    exit 1
fi

# --- Run the probe inside the container, non-root, fed via stdin. --------
STDOUT_BUF="$(mktemp)"
STDERR_BUF="$(mktemp)"
trap 'rm -f "${STDOUT_BUF}" "${STDERR_BUF}"' EXIT

"${DOCKER}" exec -i --user app --workdir /tmp \
    -e RECAP_API_KEY="${KEY}" \
    -e RECAP_TEST_HRU=12300 \
    -e RECAP_PROBE_LOG=/dev/stderr \
    "${CONTAINER}" python - \
    <"${PROBE_SCRIPT}" >"${STDOUT_BUF}" 2>"${STDERR_BUF}"
EXIT_CODE=$?

# --- JSONL purity gate. ---------------------------------------------------
# Append the buffer to the host JSONL only if the exec exited 0 AND every
# non-empty buffered line parses as JSON (a per-line check — a stray
# warning can reach stderr even on a 0 exit). The `|| [[ -n "${line}" ]]`
# clause makes the loop process a final line that has NO trailing newline
# (plain `read` returns non-zero and drops it), so an unterminated non-JSON
# warning cannot slip past the gate.
PURE=1
if [[ "${EXIT_CODE}" -ne 0 ]]; then
    PURE=0
else
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" ]] && continue
        if ! python3 -c "import json,sys; json.loads(sys.argv[1])" "${line}" >/dev/null 2>&1; then
            PURE=0
            break
        fi
    done <"${STDERR_BUF}"
fi

# Persist both streams, explicitly checking each append: with `set -e` off a
# failed `cat >>` would otherwise be swallowed and the branch would still
# exit 0, silently dropping data. Any failure (impure run, or a failed
# append) exits non-zero so launchd surfaces it.
FAILED=0

# Container stdout (the terse summary) always appends to the summary log.
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
    log "probe run impure or failed (exit=${EXIT_CODE}) — routing to launchd log, JSONL left untouched"
    cat "${STDERR_BUF}" >&2
    FAILED=1
fi

[[ "${FAILED}" -eq 0 ]] && exit 0
exit 1
