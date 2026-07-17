#!/usr/bin/env python3
# ruff: noqa: T201
"""Distribution-shift audit — Plan 115b4 §5C (Release A pre-flip gate).

Flipping the reanalysis reader default from ``single`` to ``hybrid`` (§5D)
changes WHERE a past-dynamic feature's value comes from. A model fitted on
CAMELS-CH-sourced features that suddenly reads MeteoSwiss-sourced features
(§5B: RhiresD/RprelimD/TabsD/TminD/TmaxD/SrelD) is fed a different
distribution for the same declared feature name (Plan 072 §175). The same
read path serves training, hindcast, AND live forecast past-dynamic inputs.

This is a MANUAL pre-flip gate, run against the LIVE deployment DB — repo
review alone cannot settle it (today's shipped models declare no past-dynamic
features that overlap the affected parameter set, as far as the source code
shows, but that is an INFERENCE from the code, not a fact about what is
actually ACTIVE on a given deployment; see docs/plans/115b4-reader-flip-cutover.md
§5C). Run this before Release A ships on a deployment, and disposition any
flagged model (retrain on the MeteoSwiss-sourced series, or hold the flip for
the affected stations/groups) BEFORE flipping ``reanalysis_source``.

Usage:
    uv run python scripts/audit_distribution_shift.py

Environment:
    DATABASE_URL  PostgreSQL connection string (required)

Exit code: 0 if no ACTIVE assignment (station or group) resolves to a model
declaring one of the affected past-dynamic parameters; 1 if any is flagged
(requires a human disposition before the flip) or the run cannot enumerate
assignments at all (a DB/store failure — a silent pass would be worse than a
loud failure here).
"""

from __future__ import annotations

import os
import sys

import sqlalchemy as sa
import structlog

from sapphire_flow.logging import configure_api_logging
from sapphire_flow.types.enums import ModelAssignmentStatus

configure_api_logging()
log = structlog.get_logger(__name__)

# The parameters whose SOURCE changes under the flip (Plan 115b4 §5B) — a
# model declaring any of these as past-dynamic is potentially affected.
_AFFECTED_PARAMETERS = frozenset(
    {
        "precipitation",
        "temperature",
        "temperature_min",
        "temperature_max",
        "relative_sunshine_duration",
    }
)


def main(argv: list[str] | None = None) -> int:
    del argv  # no CLI arguments today
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL environment variable is not set.\n"
            "Example: postgresql+psycopg://postgres:postgres@localhost:5432/sapphire",
            file=sys.stderr,
        )
        return 1

    log.info("database_connecting", url=database_url.split("@")[-1])
    engine = sa.create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.connect() as conn:
            from sapphire_flow.services.model_registry import discover_models
            from sapphire_flow.store.station_group_store import PgStationGroupStore
            from sapphire_flow.store.station_store import PgStationStore

            station_store = PgStationStore(conn)
            group_store = PgStationGroupStore(conn)
            models = discover_models()

            stations = station_store.fetch_all_stations()
            active_station_model_ids = {
                a.model_id
                for s in stations
                for a in station_store.fetch_model_assignments(s.id)
                if a.status is ModelAssignmentStatus.ACTIVE
            }

            # Group assignments are keyed by group_id; enumerate every group
            # touching an onboarded station via fetch_groups_for_station, then
            # de-duplicate.
            seen_group_ids = {
                g.id
                for s in stations
                for g in group_store.fetch_groups_for_station(s.id)
            }
            active_group_model_ids = {
                a.model_id
                for gid in seen_group_ids
                for a in group_store.fetch_group_model_assignments(gid)
                if a.status is ModelAssignmentStatus.ACTIVE
            }

            active_model_ids = active_station_model_ids | active_group_model_ids
    except Exception:
        log.exception("distribution_shift_audit_failed")
        print(
            "ERROR: could not enumerate active model assignments — treating "
            "as NOT SAFE to flip (a silent pass would be worse than a loud "
            "failure).",
            file=sys.stderr,
        )
        return 1

    print("=== Distribution-shift audit — active model assignments ===")
    print(f"Active distinct model_ids: {sorted(str(m) for m in active_model_ids)}")

    flagged: list[str] = []
    for model_id in sorted(active_model_ids, key=str):
        model = models.get(model_id)
        if model is None:
            print(
                f"  {model_id}: NOT DISCOVERABLE via entry points — cannot "
                "inspect its declared past_dynamic_features. Disposition "
                "manually."
            )
            flagged.append(str(model_id))
            continue
        past_dynamic = set(model.data_requirements.past_dynamic_features)
        overlap = past_dynamic & _AFFECTED_PARAMETERS
        status = f"FLAGGED (overlap={sorted(overlap)})" if overlap else "clear"
        print(f"  {model_id}: past_dynamic_features={sorted(past_dynamic)} -> {status}")
        if overlap:
            flagged.append(str(model_id))

    print()
    if flagged:
        print(
            f"FLAGGED models requiring disposition before the flip: {sorted(flagged)}"
        )
        print(
            "Retrain on the MeteoSwiss-sourced series, or hold the flip for "
            "the affected stations/groups, before shipping Release A."
        )
        return 1

    print("No active model declares an affected past-dynamic parameter. Safe to flip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
