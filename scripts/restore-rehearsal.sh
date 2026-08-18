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
# NOT the docker-compose.yml pin. That digest
# (postgis/postgis:16-3.4@sha256:44126d872...) is linux/amd64 ONLY — verified
# via `docker buildx imagetools inspect`: postgis/postgis has never published
# an arm64 build for ANY 16-3.4 tag, so every rehearsal run on the arm64
# mac-mini silently fell back to emulation. imresamu/postgis publishes the
# same PostGIS 3.4 / PostgreSQL 16 build (maintained by the same engineer
# who publishes the official postgis/postgis images) as a genuine manifest
# list with both linux/amd64 and linux/arm64 — confirmed with
# `docker buildx imagetools inspect imresamu/postgis:16-3.4@sha256:...`.
# Still digest-pinned per repo policy (docs/standards/security.md:554).
IMAGE="${RESTORE_IMAGE:-imresamu/postgis:16-3.4@sha256:6da75969915039b7356058b4310d43fde88c275ada982c2dfee29da68445ff4d}"
CONTAINER="${RESTORE_CONTAINER_NAME:-sapphire-restore-rehearsal-$$}"
# Never the image's default 'postgres' database: the PostGIS image
# pre-initialises tiger/tiger_data/topology there, so a restore into it dies
# on `ERROR: schema "tiger" already exists`. Always createdb a fresh one.
DB_NAME="${RESTORE_DB_NAME:-rehearsal}"
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
    # $2, if given, is the target database; defaults to postgres (used only
    # by the readiness probe below, before DB_NAME exists).
    "${DOCKER}" exec "${CONTAINER}" psql -U postgres -d "${2:-postgres}" \
        -v ON_ERROR_STOP=1 -tAc "$1"
}

# --- launch: ephemeral container, no named volume, no network. The image
# was re-pinned (see docs/standards/security.md "Image pinning" caveat) to a
# third-party vendor not independently audited beyond confirming a genuine
# multi-arch manifest — and this container's whole job is to hold a
# fully-restored, decrypted dump, including access_tokens (token hashes,
# tenant_id, scopes) across every tenant. --network none closes any
# container-initiated exfiltration path regardless of image trust: nothing
# needs it, since every interaction below is `docker exec`/`docker cp` from
# the host. -------------------------------------------------------------
MAY_EXIST=1
"${DOCKER}" run -d --network none --name "${CONTAINER}" \
    -e POSTGRES_PASSWORD=rehearsal \
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

# --- createdb: a FRESH database, never the image's pre-populated default.
# Stderr is captured (not discarded) for the same reason as pg_restore's
# below: a swallowed diagnostic hides the real failure reason. -------------
createdb_err="$("${DOCKER}" exec "${CONTAINER}" createdb -U postgres "${DB_NAME}" 2>&1 1>/dev/null)"
createdb_status=$?
if [[ "${createdb_status}" -ne 0 ]]; then
    fail "createdb '${DB_NAME}' failed (exit ${createdb_status}): $(printf '%s' "${createdb_err}" | head -n 20)"
fi

# --- restore into that fresh database. --no-owner --no-acl because pg_dump
# does NOT back up cluster roles, but the dump is full of OWNER TO / ACL
# statements referencing them — without these flags a fresh cluster dies on
# `ERROR: role "sapphire" does not exist`. Roles are recreated afterwards by
# bootstrap-roles.sh, not by this rehearsal. -------------------------------
pg_restore_err="$("${DOCKER}" exec "${CONTAINER}" pg_restore --single-transaction \
    --exit-on-error --no-owner --no-acl -U postgres -d "${DB_NAME}" \
    /tmp/restore.dump 2>&1 1>/dev/null)"
pg_restore_status=$?
if [[ "${pg_restore_status}" -ne 0 ]]; then
    fail "pg_restore failed (exit ${pg_restore_status}): $(printf '%s' "${pg_restore_err}" | head -n 20)"
fi

# --- content assertions: pg_restore exit 0 alone proves nothing. -----------
CHECKS_DONE="access_tokens row count, alembic_version, access_tokens_id_seq"

count="$(psql_exec "SELECT count(*) FROM access_tokens" "${DB_NAME}")" \
    || fail "access_tokens query failed (table missing from restore?)"
[[ "${count}" =~ ^[0-9]+$ ]] || fail "access_tokens row count unreadable: '${count}'"
[[ "${count}" -gt 0 ]] || fail "access_tokens has zero rows after restore"

version="$(psql_exec "SELECT version_num FROM alembic_version" "${DB_NAME}")" \
    || fail "alembic_version query failed"
[[ -n "${version}" ]] || fail "alembic_version is empty"

seq="$(psql_exec "SELECT last_value || '|' || is_called FROM access_tokens_id_seq" "${DB_NAME}")" \
    || fail "access_tokens_id_seq query failed"
last_value="${seq%%|*}"
is_called="${seq##*|}"
max_id="$(psql_exec "SELECT coalesce(max(id), 0) FROM access_tokens" "${DB_NAME}")" \
    || fail "access_tokens max(id) query failed"
if [[ "${is_called}" == "f" && "${last_value}" == "${max_id}" ]]; then
    fail "access_tokens_id_seq: last_value == MAX(id) with is_called=false — next nextval() collides with an existing row"
fi

# Plausibility only — the restore container cannot reach production
# Postgres (backend-only), and comparing against a live source that has
# moved on is semantically wrong for a historical dump anyway.
echo "note: no source-database comparison — content assertions plus alembic_version only" >&2

PASS=1
