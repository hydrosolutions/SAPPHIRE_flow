#!/bin/bash
# scripts/restore-rehearsal.sh — Plan 162 T5. Restores a pg_dump into a
# disposable, EPHEMERAL Postgres container (no named volume — a mistaken
# argument can never point this at a real volume on the host) and asserts
# on CONTENT, not merely on pg_restore's exit code. See
# docs/plans/162-robust-database-backup.md "T5 — rehearse the restore".
#
# Usage: scripts/restore-rehearsal.sh <dump-path>
set -uo pipefail

DUMP_PATH="${1:?usage: restore-rehearsal.sh <dump-path>}"
if [[ ! -f "${DUMP_PATH}" ]]; then
    echo "FAIL: dump not found: ${DUMP_PATH}" >&2
    exit 1
fi

DOCKER="${DOCKER_CMD:-docker}"
# Same manifest-list digest already pinned in docker-compose.yml (repo
# policy: docs/standards/security.md:554) — reused rather than minting a
# second pin for the same image family.
IMAGE="${RESTORE_IMAGE:-postgis/postgis:16-3.4@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5}"
CONTAINER="${RESTORE_CONTAINER_NAME:-sapphire-restore-rehearsal-$$}"
WAIT_ATTEMPTS="${RESTORE_WAIT_ATTEMPTS:-60}"
WAIT_INTERVAL="${RESTORE_WAIT_INTERVAL:-1}"

# Set BEFORE the `docker run` call returns (not after) so a signal landing
# between container creation and this assignment cannot skip cleanup.
MAY_EXIST=0
PASS=0
CHECKS_DONE=""

cleanup() {
    local rm_status=0
    if [[ "${MAY_EXIST}" -eq 1 ]]; then
        "${DOCKER}" rm -f "${CONTAINER}" >/dev/null 2>&1 || rm_status=1
    fi
    if [[ "${PASS}" -eq 1 && "${rm_status}" -eq 0 ]]; then
        echo "PASS: restore rehearsal succeeded (${CHECKS_DONE})"
    elif [[ "${PASS}" -eq 1 ]]; then
        echo "FAIL: assertions passed but teardown failed — ${CONTAINER} may still exist" >&2
        exit 1
    fi
}
trap cleanup EXIT

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

psql_exec() {
    "${DOCKER}" exec "${CONTAINER}" psql -U postgres -v ON_ERROR_STOP=1 -tAc "$1"
}

# --- launch: ephemeral container, no named volume. -------------------------
MAY_EXIST=1
"${DOCKER}" run -d --name "${CONTAINER}" -e POSTGRES_PASSWORD=rehearsal \
    "${IMAGE}" >/dev/null || fail "docker run failed"

# --- wait for the FINAL server, not the temp init server. The official
# entrypoint starts a temp server, runs init scripts, stops it, then starts
# the real one — pg_isready alone can succeed against the temp server. -----
ready=0
for ((i = 0; i < WAIT_ATTEMPTS; i++)); do
    if "${DOCKER}" logs "${CONTAINER}" 2>&1 | grep -q "PostgreSQL init process complete" \
        && "${DOCKER}" exec "${CONTAINER}" pg_isready -U postgres >/dev/null 2>&1 \
        && psql_exec "SELECT 1" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep "${WAIT_INTERVAL}"
done
[[ "${ready}" -eq 1 ]] || fail "final Postgres server never became ready"

# --- copy only the selected dump file in; never bind-mount its directory. --
"${DOCKER}" cp "${DUMP_PATH}" "${CONTAINER}:/tmp/restore.dump" >/dev/null 2>&1 \
    || fail "docker cp failed"

# --- restore into the (empty) default 'postgres' database. -----------------
"${DOCKER}" exec "${CONTAINER}" pg_restore --single-transaction --exit-on-error \
    -U postgres -d postgres /tmp/restore.dump >/dev/null 2>&1 || fail "pg_restore failed"

# --- content assertions: pg_restore exit 0 alone proves nothing. -----------
CHECKS_DONE="access_tokens row count, alembic_version, access_tokens_id_seq"

count="$(psql_exec "SELECT count(*) FROM access_tokens")" \
    || fail "access_tokens query failed (table missing from restore?)"
[[ "${count}" =~ ^[0-9]+$ ]] || fail "access_tokens row count unreadable: '${count}'"
[[ "${count}" -gt 0 ]] || fail "access_tokens has zero rows after restore"

version="$(psql_exec "SELECT version_num FROM alembic_version")" \
    || fail "alembic_version query failed"
[[ -n "${version}" ]] || fail "alembic_version is empty"

seq="$(psql_exec "SELECT last_value || '|' || is_called FROM access_tokens_id_seq")" \
    || fail "access_tokens_id_seq query failed"
last_value="${seq%%|*}"
is_called="${seq##*|}"
max_id="$(psql_exec "SELECT coalesce(max(id), 0) FROM access_tokens")" \
    || fail "access_tokens max(id) query failed"
if [[ "${is_called}" == "f" && "${last_value}" == "${max_id}" ]]; then
    fail "access_tokens_id_seq: last_value == MAX(id) with is_called=false — next nextval() collides with an existing row"
fi

# Plausibility only — the restore container cannot reach production
# Postgres (backend-only), and comparing against a live source that has
# moved on is semantically wrong for a historical dump anyway.
echo "note: no source-database comparison — content assertions plus alembic_version only" >&2

PASS=1
