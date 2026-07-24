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
: "${SAPPHIRE_API_DB_PASSWORD_FILE:?SAPPHIRE_API_DB_PASSWORD_FILE is required (path to the sapphire_api role's password secret)}"
: "${SAPPHIRE_WORKER_DB_PASSWORD_FILE:?SAPPHIRE_WORKER_DB_PASSWORD_FILE is required (path to the sapphire_worker role's password secret)}"

if [ ! -r "${SAPPHIRE_API_DB_PASSWORD_FILE}" ]; then
    echo "bootstrap-roles.sh: cannot read SAPPHIRE_API_DB_PASSWORD_FILE=${SAPPHIRE_API_DB_PASSWORD_FILE}" >&2
    exit 1
fi
if [ ! -r "${SAPPHIRE_WORKER_DB_PASSWORD_FILE}" ]; then
    echo "bootstrap-roles.sh: cannot read SAPPHIRE_WORKER_DB_PASSWORD_FILE=${SAPPHIRE_WORKER_DB_PASSWORD_FILE}" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Strip the SQLAlchemy driver suffix (+psycopg) for psql, same convention as
# flows/backup.py's _to_libpq_url.
OWNER_LIBPQ_URL=$(printf '%s' "${DATABASE_URL}" | sed 's|postgresql+psycopg://|postgresql://|')
API_PASSWORD=$(cat "${SAPPHIRE_API_DB_PASSWORD_FILE}")
WORKER_PASSWORD=$(cat "${SAPPHIRE_WORKER_DB_PASSWORD_FILE}")

psql -v ON_ERROR_STOP=1 \
     -v api_password="${API_PASSWORD}" \
     -v worker_password="${WORKER_PASSWORD}" \
     "${OWNER_LIBPQ_URL}" \
     -f "${SCRIPT_DIR}/bootstrap-roles.sql"
