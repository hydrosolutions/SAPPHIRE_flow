#!/bin/sh
# Plan 147 Slice D — idempotent least-privilege DB role bootstrap wrapper.
#
# Runs as the DB owner from the `init` service, AFTER `alembic upgrade head`
# (docker-compose.yml). Reads the owner connection from $DATABASE_URL (set by
# entrypoint.sh from DATABASE_URL_TEMPLATE + the owner db_password secret)
# and the two scoped-role passwords from their own secret files, then applies
# docker/bootstrap-roles.sql via psql. Safe to re-run on every deploy —
# see bootstrap-roles.sql for the idempotency contract.
set -e

: "${DATABASE_URL:?DATABASE_URL is required (owner connection; set by entrypoint.sh)}"
: "${SAPPHIRE_API_DB_PASSWORD_FILE:?SAPPHIRE_API_DB_PASSWORD_FILE is required (path to the sapphire_api role password secret)}"
: "${SAPPHIRE_WORKER_DB_PASSWORD_FILE:?SAPPHIRE_WORKER_DB_PASSWORD_FILE is required (path to the sapphire_worker role password secret)}"
: "${SAPPHIRE_BACKUP_DB_PASSWORD_FILE:?SAPPHIRE_BACKUP_DB_PASSWORD_FILE is required (path to the sapphire_backup role password secret)}"

if [ ! -r "${SAPPHIRE_API_DB_PASSWORD_FILE}" ]; then
    echo "bootstrap-roles.sh: cannot read SAPPHIRE_API_DB_PASSWORD_FILE=${SAPPHIRE_API_DB_PASSWORD_FILE}" >&2
    exit 1
fi
if [ ! -r "${SAPPHIRE_WORKER_DB_PASSWORD_FILE}" ]; then
    echo "bootstrap-roles.sh: cannot read SAPPHIRE_WORKER_DB_PASSWORD_FILE=${SAPPHIRE_WORKER_DB_PASSWORD_FILE}" >&2
    exit 1
fi
if [ ! -r "${SAPPHIRE_BACKUP_DB_PASSWORD_FILE}" ]; then
    echo "bootstrap-roles.sh: cannot read SAPPHIRE_BACKUP_DB_PASSWORD_FILE=${SAPPHIRE_BACKUP_DB_PASSWORD_FILE}" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# Strip the SQLAlchemy driver suffix (+psycopg) for psql. This is the OWNER
# migration connection only — unrelated to the backup identity, which
# (Plan 162 T2) reaches pg_dump via allowlisted PG* env vars in
# flows/backup.py, never a URL.
OWNER_LIBPQ_URL=$(printf '%s' "${DATABASE_URL}" | sed 's|postgresql+psycopg://|postgresql://|')
API_PASSWORD=$(cat "${SAPPHIRE_API_DB_PASSWORD_FILE}")
WORKER_PASSWORD=$(cat "${SAPPHIRE_WORKER_DB_PASSWORD_FILE}")
BACKUP_PASSWORD=$(cat "${SAPPHIRE_BACKUP_DB_PASSWORD_FILE}")

psql -v ON_ERROR_STOP=1 \
     -v api_password="${API_PASSWORD}" \
     -v worker_password="${WORKER_PASSWORD}" \
     -v backup_password="${BACKUP_PASSWORD}" \
     "${OWNER_LIBPQ_URL}" \
     -f "${SCRIPT_DIR}/bootstrap-roles.sql"
