#!/bin/sh
set -e
chown -R app:app /run/secrets 2>/dev/null || true
exec gosu app "$@"
