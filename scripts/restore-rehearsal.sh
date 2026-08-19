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
# Same pin as docker-compose.yml/ci.yml — the owner's decision (2026-08-18,
# Plan 162 T5 restore-path fix, item 1), NOT the vendor swap an earlier round
# of this fix made. That round moved this to `imresamu/postgis` because
# `postgis/postgis:16-3.4` is linux/amd64 ONLY (verified via `docker buildx
# imagetools inspect`: postgis/postgis has never published an arm64 build for
# ANY 16-3.4 tag), so every rehearsal run on the arm64 mac-mini fell back to
# emulation. The owner chose to keep the already-vetted, compose-pinned
# vendor and accept that emulation instead: it costs speed only, DHM/AWS are
# x86 where this image is native, and this container's whole job is to hold
# a fully-restored production dump — every tenant's access-token hashes
# included — so a second, less-audited vendor namespace is not worth it just
# to buy native arm64 here. `RESTORE_IMAGE` still overrides for anyone who
# wants a native-arm64 image locally. Still digest-pinned per repo policy
# (docs/standards/security.md:554).
IMAGE="${RESTORE_IMAGE:-postgis/postgis:16-3.4@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5}"
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

# --- launch: ephemeral container, no named volume, no network. This
# container's whole job is to hold a fully-restored, decrypted dump,
# including access_tokens (token hashes, tenant_id, scopes) across every
# tenant. --network none closes any container-initiated exfiltration path
# regardless of how much the image is trusted — belt-and-braces alongside the
# already-vetted `postgis/postgis` pin above, not a substitute for it: nothing
# needs container-initiated network access, since every interaction below is
# `docker exec`/`docker cp` from the host. ---------------------------------
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
# `--` unambiguously ends option parsing so DB_NAME can never be swallowed
# as an option value or an option can never masquerade as the db name —
# the same defence-in-depth reasoning as pg_restore's explicit -d below.
# Stderr is captured (not discarded) for the same reason as pg_restore's
# below: a swallowed diagnostic hides the real failure reason. -------------
createdb_err="$("${DOCKER}" exec "${CONTAINER}" createdb -U postgres -- "${DB_NAME}" 2>&1 1>/dev/null)"
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
# The sequence check below deliberately targets pipeline_health, NOT
# access_tokens: migration 0047 gives access_tokens.id a UUID primary key
# (no sequence at all), so `access_tokens_id_seq` never exists — an earlier
# version of this check would have failed after every genuinely successful
# restore, and a vacuous test fake (inventing the sequence in the mock)
# hid that for a full review round. Exactly two tables in this schema use
# `BIGINT ... autoincrement=True` — audit_log (migration 0045) and
# pipeline_health (migration 0001) — and pipeline_health is the pair
# checked here because it is guaranteed non-empty in any real dump (the
# forecast/BAFU flows write health records continuously; audit_log may be
# sparse). ------------------------------------------------------------------
CHECKS_DONE="access_tokens row count, alembic_version, pipeline_health_id_seq"

count="$(psql_exec "SELECT count(*) FROM access_tokens" "${DB_NAME}")" \
    || fail "access_tokens query failed (table missing from restore?)"
[[ "${count}" =~ ^[0-9]+$ ]] || fail "access_tokens row count unreadable: '${count}'"
[[ "${count}" -gt 0 ]] || fail "access_tokens has zero rows after restore"

version="$(psql_exec "SELECT version_num FROM alembic_version" "${DB_NAME}")" \
    || fail "alembic_version query failed"
[[ -n "${version}" ]] || fail "alembic_version is empty"

# Guard: verify the relation exists BEFORE querying it. This is the fix for
# the exact class of bug that shipped once already (access_tokens_id_seq
# was queried directly for a full review round despite never existing) — a
# missing relation must fail loudly and name itself, never masquerade as
# some other query error.
seq_regclass="$(psql_exec "SELECT to_regclass('pipeline_health_id_seq')" "${DB_NAME}")" \
    || fail "pipeline_health_id_seq existence check query failed"
[[ -n "${seq_regclass}" ]] || fail "pipeline_health_id_seq does not exist (to_regclass returned null) — cannot run the sequence-collision check"

# SQL computes the next value, and every field is emitted as an INTEGER.
#
# This is deliberate. An earlier revision selected `last_value || '|' ||
# is_called` and branched in bash on `is_called == "t"` — which is wrong
# against a real database, and the live run on the mac-mini proved it:
# Postgres renders a boolean as `t` only when psql DISPLAYS a boolean
# column; casting it to text (which `||` does implicitly) yields `true`.
# Verified on the production database:
#
#     SELECT true          ->  t        (psql display form)
#     SELECT true::text    ->  true     (what `||` actually produces)
#     the script's query   ->  1362|true
#
# So the bash comparison never matched, next_val was computed as last_value
# instead of last_value + 1, and a perfectly healthy sequence (last_value ==
# max_id after the `setval(max, true)` every good dump restores) was
# reported as a collision. That is a FALSE POSITIVE on every healthy
# backup — a rehearsal that always fails is worse than no rehearsal.
#
# The unit-test fakes emitted `t`/`f`, copying psql's display form rather
# than what the real query returns, so the suite stayed green throughout.
# Emitting integers removes the rendering question from the shell entirely:
# there is no display-vs-cast ambiguity for `1` and `0`.
#
# next_val semantics: when is_called is true, nextval() already handed out
# last_value, so the next call emits last_value + 1; when false, the next
# call emits last_value verbatim.
seq="$(psql_exec "SELECT (CASE WHEN is_called THEN last_value + 1 ELSE last_value END) || '|' || last_value || '|' || is_called::int FROM pipeline_health_id_seq" "${DB_NAME}")" \
    || fail "pipeline_health_id_seq query failed"
IFS='|' read -r next_val last_value is_called_int <<< "${seq}"
[[ "${next_val}" =~ ^[0-9]+$ && "${last_value}" =~ ^[0-9]+$ && "${is_called_int}" =~ ^[01]$ ]] \
    || fail "pipeline_health_id_seq returned an unparseable row: '${seq}' (expected next|last|0-or-1)"
max_id="$(psql_exec "SELECT coalesce(max(id), 0) FROM pipeline_health" "${DB_NAME}")" \
    || fail "pipeline_health max(id) query failed"
[[ "${max_id}" =~ ^[0-9]+$ ]] || fail "pipeline_health max(id) unreadable: '${max_id}'"
# `-le`, not `-eq`. A sequence that was never advanced is the single most
# common real restore defect, and an equality-only predicate exempts it
# entirely: with the sequence at 1 and rows up to 5, next_val=1 != 5 passes,
# yet the first insert after recovery emits id=1 and dies on a duplicate
# key. Any next_val at or below MAX(id) means the sequence will hand out
# already-taken ids, so the restore is not usable. Legitimate cases are
# unaffected: a healthy dump restores `setval(max_id, true)` -> next_val =
# max_id + 1, and an empty table has max_id = 0 (coalesce) < next_val = 1.
if [[ "${next_val}" -le "${max_id}" ]]; then
    fail "pipeline_health_id_seq: next nextval() (${next_val}) is at or below MAX(pipeline_health.id) == ${max_id} — the next insert would collide on the primary key (last_value=${last_value}, is_called=${is_called_int})"
fi

# Plausibility only — the restore container cannot reach production
# Postgres (backend-only), and comparing against a live source that has
# moved on is semantically wrong for a historical dump anyway.
echo "note: no source-database comparison — content assertions plus alembic_version only" >&2

PASS=1
