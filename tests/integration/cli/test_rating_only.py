from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from sapphire_flow.adapters.dhm_files import parse_rating_tables
from sapphire_flow.cli.import_dhm_delivery import (
    load_station_metadata,
    register_stations,
)
from sapphire_flow.db.metadata import observations, rating_curves, rating_delivery_files
from sapphire_flow.services.rating_delivery import admit_rating_deliveries
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.rating_delivery import RatingDeliveryFile
from tests.integration.cli.test_import_dhm_delivery import _CHWRR, _NOW, _ensure_chwrr

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/dhm"


def delivery(name: str, day: date) -> RatingDeliveryFile:
    original = (
        (FIXTURE / "synthetic_rating_tables.txt")
        .read_bytes()
        .replace(b"Station No: 999", b"Station No: 450")
    )
    parsed = parse_rating_tables(original.decode())
    return RatingDeliveryFile(
        delivery_id=name,
        delivered_on=day,
        network="dhm",
        station_code=parsed.station_code,
        original=original,
        sha256=hashlib.sha256(original).hexdigest(),
        table=parsed,
    )


class TestRatingOnly:
    @pytest.mark.parametrize("reverse", [False, True])
    def test_both_originals_latest_candidates_retry_no_observations(
        self, db_connection: sa.Connection, reverse: bool
    ) -> None:
        conn = db_connection
        tenants = PgTenantStore(conn)
        tenant_id = _ensure_chwrr(tenants)
        register_stations(
            tenants,
            PgStationStore(conn),
            _CHWRR,
            load_station_metadata(FIXTURE / "stations.toml"),
            audit_log_store=PgAuditLogStore(conn),
            now=_NOW,
        )
        files = (
            delivery("september", date(2026, 9, 8)),
            delivery("october", date(2026, 10, 4)),
        )
        if reverse:
            files = tuple(reversed(files))
        assert admit_rating_deliveries(conn, tenant_id, files, now=_NOW) == 2
        assert admit_rating_deliveries(conn, tenant_id, files, now=_NOW) == 0
        assert (
            conn.scalar(sa.select(sa.func.count()).select_from(rating_delivery_files))
            == 2
        )
        assert set(conn.execute(sa.select(rating_curves.c.delivery_id)).scalars()) == {
            "october"
        }
        assert conn.scalar(sa.select(sa.func.count()).select_from(observations)) == 0

    def test_missing_station_is_not_created(self, db_connection: sa.Connection) -> None:
        tenant_id = _ensure_chwrr(PgTenantStore(db_connection))
        with pytest.raises(ValueError, match="missing, ambiguous or foreign"):
            admit_rating_deliveries(
                db_connection,
                tenant_id,
                (delivery("october", date(2026, 10, 4)),),
                now=_NOW,
            )
        assert conn_count(db_connection) == 0


def conn_count(conn: sa.Connection) -> int:
    return conn.scalar(sa.select(sa.func.count()).select_from(rating_delivery_files))


def test_existing_replace_refuses_native_original_with_derived_dependents(
    db_connection: sa.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sapphire_flow.adapters.dhm_files import parse_daily_flow, parse_rating_tables
    from sapphire_flow.cli.import_dhm_delivery import DELIVERY_ID, replace_delivery
    from sapphire_flow.db.metadata import rating_derived_discharge
    from sapphire_flow.store.observation_store import PgObservationStore
    from tests.integration.store import test_native_rating_forecasts as native_fixture

    original_delivery = delivery
    monkeypatch.setattr(
        native_fixture,
        "delivery",
        lambda identity, day: original_delivery(DELIVERY_ID, day),
    )
    native_fixture.native_pair(db_connection)
    metadata = load_station_metadata(FIXTURE / "stations.toml")
    daily = parse_daily_flow((FIXTURE / "synthetic_daily_flow.txt").read_text())
    ratings = parse_rating_tables((FIXTURE / "synthetic_rating_tables.txt").read_text())
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(ratings, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    before = db_connection.scalar(
        sa.select(sa.func.count()).select_from(rating_derived_discharge)
    )
    with (
        pytest.raises(sa.exc.DBAPIError, match="immutable"),
        db_connection.begin_nested(),
    ):
        replace_delivery(
            PgTenantStore(db_connection),
            PgStationStore(db_connection),
            PgRatingCurveStore(db_connection),
            PgObservationStore(db_connection),
            _CHWRR,
            files,
            audit_log_store=PgAuditLogStore(db_connection),
            now=_NOW,
        )
    assert (
        db_connection.scalar(
            sa.select(sa.func.count()).select_from(rating_derived_discharge)
        )
        == before
        == 1
    )
