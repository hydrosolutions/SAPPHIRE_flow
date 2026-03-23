#!/bin/sh
# entrypoint.sh — reads Docker secrets, constructs DATABASE_URL, drops to app user
set -e

# Read DB password from Docker secret (with env var fallback for local dev)
if [ -f /run/secrets/db_password ]; then
    DB_PASSWORD=$(cat /run/secrets/db_password)
else
    DB_PASSWORD="${DB_PASSWORD:?DB_PASSWORD is required (set via Docker secret or env var)}"
fi

# Construct DATABASE_URL if template is provided
if [ -n "${DATABASE_URL_TEMPLATE}" ]; then
    # Insert password into template URL: user@host → user:password@host
    export DATABASE_URL=$(echo "${DATABASE_URL_TEMPLATE}" | sed "s|://\([^@]*\)@|://\1:${DB_PASSWORD}@|")
fi

# Construct Prefect DB connection URL if template is provided
if [ -n "${PREFECT_API_DATABASE_CONNECTION_URL}" ]; then
    export PREFECT_API_DATABASE_CONNECTION_URL=$(echo "${PREFECT_API_DATABASE_CONNECTION_URL}" | sed "s|://\([^@]*\)@|://\1:${DB_PASSWORD}@|")
fi

# Fix secret file permissions for non-root user
chown -R app:app /run/secrets 2>/dev/null || true

# Drop to app user
exec gosu app "$@"
