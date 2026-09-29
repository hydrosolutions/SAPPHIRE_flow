"""Shared helpers for the Plan 510 real-Postgres tests: the three DHM import
commands driven through their service functions on any connection."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sapphire_flow.adapters.dhm_files import (
    DailyFlowFile,
    RatingTableFile,
    parse_daily_flow,
    parse_rating_tables,
)
from sapphire_flow.cli.import_dhm_delivery import (
    load_station_metadata,
    register_stations,
    replace_delivery,
    run_delivery_qc,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests/fixtures/dhm"
CONFIG = REPO_ROOT / "config.toml"
NOW = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
CHWRR = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)


def files() -> dict[str, tuple[DailyFlowFile, RatingTableFile]]:
    metadata = load_station_metadata(FIXTURES / "stations.toml")
    daily = parse_daily_flow((FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables((FIXTURES / "synthetic_rating_tables.txt").read_text())
    return {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }


def run_stations(conn: sa.Connection) -> None:
    register_stations(
        PgTenantStore(conn),
        PgStationStore(conn),
        CHWRR,
        load_station_metadata(FIXTURES / "stations.toml"),
        audit_log_store=PgAuditLogStore(conn),
        now=NOW,
    )


def run_replace(conn: sa.Connection) -> None:
    replace_delivery(
        PgTenantStore(conn),
        PgStationStore(conn),
        PgRatingCurveStore(conn),
        PgObservationStore(conn),
        CHWRR,
        files(),
        audit_log_store=PgAuditLogStore(conn),
        now=NOW,
    )


def run_qc(conn: sa.Connection) -> None:
    run_delivery_qc(
        PgTenantStore(conn),
        PgStationStore(conn),
        PgObservationStore(conn),
        CHWRR,
        CONFIG,
        audit_log_store=PgAuditLogStore(conn),
        now=NOW,
    )
