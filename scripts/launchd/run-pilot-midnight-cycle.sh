#!/bin/bash
# run-pilot-midnight-cycle.sh — launchd wrapper that triggers ONE forecast
# cycle per day with the issue time pinned to exactly 00:00:00Z.
#
# WHY THIS EXISTS (and why it is temporary):
#
# The `cmal_small` group pilot cannot produce a forecast on the SCHEDULED
# cycles. Measured on staging 2026-09-25: the assignment had been active
# since 09-22 and every scheduled cycle produced ZERO pilot rows, while a
# hand-triggered run pinned to 00:00:00 produced forecasts for both pilot
# stations immediately.
#
# The cause is a handful of seconds. `_resolve_cycle_time` returns the WALL
# CLOCK when no cycle_time parameter is supplied, so the cron-fired cycles
# stamp themselves 00:00:06, 06:00:07, 18:00:07 … and the future window's
# `valid_time >= issue_time` rule then excludes the issue-day bucket, which
# a daily model needs. The model refuses with a typed input-data failure.
#
# ⛔ This script is a MONITORING SCAFFOLD, not the fix. The fix is Plan 326
# (honour a per-model issue-hour restriction, and settle whether a forecast
# carries a logical issue time distinct from its run time). Delete this
# script when 326 lands.
#
# ⚠️ It runs a FULL forecast cycle — every model, all stations — because the
# cycle is all-or-nothing. That is the cost of a daily extra run, accepted
# deliberately while the pilot is observed.
#
# 🔑 No collision with the scheduled cycle: this run's `issued_at` is exactly
# 00:00:00Z while the scheduled one is 00:00:0xZ, and forecast uniqueness is
# (station, model, issued_at, parameter). They coexist.
set -uo pipefail

# shellcheck source=scripts/launchd/docker-endpoint.sh
source "$(dirname "${BASH_SOURCE[0]}")/docker-endpoint.sh"
DOCKER="${DOCKER_CMD:-${DOCKER_BIN}}"

CONTAINER="${PILOT_CYCLE_CONTAINER:-sapphire_flow-prefect-worker-1}"
DEPLOYMENT="${PILOT_CYCLE_DEPLOYMENT:-forecast-cycle/forecast-cycle}"

# TODAY'S midnight UTC, computed on the HOST. ⚠️ `date -u` matters: launchd
# fires in LOCAL time (CEST here), so deriving the date from local time would
# pin yesterday's midnight for any run between 00:00 and 02:00 CEST.
CYCLE_TIME="$(date -u +%Y-%m-%dT00:00:00)"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] triggering ${DEPLOYMENT} with cycle_time=${CYCLE_TIME}"

"${DOCKER}" exec "${CONTAINER}" \
    prefect deployment run "${DEPLOYMENT}" --param "cycle_time=${CYCLE_TIME}"
status=$?

if [ "${status}" -ne 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] TRIGGER FAILED (exit ${status})" >&2
    exit "${status}"
fi

# ⛔ Deliberately does NOT wait for the run, and does NOT check whether a
# pilot forecast was produced. A trigger script that blocks for ~25 minutes
# holds a launchd slot for no benefit, and a scaffold that judges its own
# output is how a monitoring period starts believing itself. Read the result
# from the `forecasts` table.
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] triggered; outcome is in the forecasts table, not here"
