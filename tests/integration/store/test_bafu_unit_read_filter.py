from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import (
    bafu_unit_hold_releases,
    bafu_unit_read_holds,
    observations,
)
from sapphire_flow.store.bafu_unit_read_filter import trusted_unit_predicate
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 10, 28, tzinfo=UTC))


@pytest.fixture
def station(db_connection: sa.Connection) -> tuple[StationId, TenantId]:
    station = make_station_config(station_id=StationId(uuid4()), code="2206")
    PgStationStore(db_connection).store_station(station)
    for source, parameter in (
        ("measured", "discharge"),
        ("manual_import", "discharge"),
        ("measured", "water_level"),
    ):
        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station.id,
                timestamp=NOW,
                parameter=parameter,
                source=source,
                value=1.0,
                qc_status="raw",
            )
        )
    return station.id, station.tenant_id


def hold(
    conn: sa.Connection,
    station: tuple[StationId, TenantId],
    *,
    start: datetime = NOW,
    end: datetime | None = None,
) -> UUID:
    identity = uuid4()
    conn.execute(
        sa.insert(bafu_unit_read_holds).values(
            id=identity,
            station_id=station[0],
            tenant_id=station[1],
            starts_at=start,
            ends_at=end,
            reason="test legacy uncertainty",
            origin="maintenance",
            approval_sha256="a" * 64,
            registry_sha256="b" * 64,
        )
    )
    return identity


def visible(conn: sa.Connection) -> set[tuple[str, str]]:
    rows = conn.execute(
        sa.select(observations.c.source, observations.c.parameter).where(
            trusted_unit_predicate(observations)
        )
    ).all()
    return {(row.source, row.parameter) for row in rows}


class TestTrustedUnitPredicate:
    def test_no_hold_does_not_require_unit_evidence(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        assert ("measured", "discharge") in visible(db_connection)

    def test_hold_affects_only_measured_discharge(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        hold(db_connection, station)
        assert visible(db_connection) == {
            ("manual_import", "discharge"),
            ("measured", "water_level"),
        }

    def test_hold_end_is_exclusive(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        hold(db_connection, station, start=NOW - timedelta(days=1), end=NOW)
        assert ("measured", "discharge") in visible(db_connection)

    def test_named_release_does_not_override_another_hold(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        released = hold(db_connection, station)
        hold(db_connection, station)
        db_connection.execute(
            sa.insert(bafu_unit_hold_releases).values(
                id=uuid4(),
                hold_id=released,
                starts_at=NOW,
                ends_at=None,
                approval_sha256="c" * 64,
            )
        )
        assert ("measured", "discharge") not in visible(db_connection)

    def test_release_restores_only_its_subrange(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        identity = hold(db_connection, station)
        db_connection.execute(
            sa.insert(bafu_unit_hold_releases).values(
                id=uuid4(),
                hold_id=identity,
                starts_at=NOW,
                ends_at=NOW + timedelta(microseconds=1),
                approval_sha256="c" * 64,
            )
        )
        assert ("measured", "discharge") in visible(db_connection)
        later = NOW + timedelta(microseconds=1)
        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station[0],
                timestamp=later,
                parameter="discharge",
                source="measured",
                value=2.0,
                qc_status="raw",
            )
        )
        values = (
            db_connection.execute(
                sa.select(observations.c.value).where(
                    observations.c.source == "measured",
                    observations.c.parameter == "discharge",
                    trusted_unit_predicate(observations),
                )
            )
            .scalars()
            .all()
        )
        assert values == [1.0]

    def test_release_cannot_extend_beyond_named_hold(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        identity = hold(db_connection, station, end=NOW + timedelta(days=1))
        with (
            pytest.raises(DBAPIError, match="within its named hold"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(bafu_unit_hold_releases).values(
                    id=uuid4(),
                    hold_id=identity,
                    starts_at=NOW,
                    ends_at=None,
                    approval_sha256="c" * 64,
                )
            )
