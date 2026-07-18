"""Plan 115b5 (Release B) — LOCKED acceptance tests for migration 0033.

Migration ``0033`` retires the ``camels-ch`` ``station_weather_sources``
REANALYSIS binding, guarded IN the migration (SELECT + raise, atomic with
the DELETE): a station left with NO surviving reanalysis binding after the
delete must abort the whole transaction rather than silently strand the
station's forcing reads.

The guard/delete predicate must match the hybrid reader's EFFECTIVE
membership rule (``station_store.py``'s ``fetch_reanalysis_bindings`` /
``_row_to_weather_source``), not a naive SQL filter — no ``status`` filter,
and a legacy NULL ``role`` on a ``camels-ch`` row still counts as
REANALYSIS. These are the review-flagged edge cases (115b5 plan, "Tests"
section); soundness notes are inline per test.
"""

from __future__ import annotations

import os
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import historical_forcing, station_weather_sources
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId

_START: UtcDatetime = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_END: UtcDatetime = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
_VALID_TIME: UtcDatetime = ensure_utc(datetime(2026, 1, 5, tzinfo=UTC))


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade can run the 0033
    guard/delete against seeded weather-source bindings without disturbing
    the shared session engine (migrated to head once)."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            yield engine, url
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _alembic_cfg(url: str) -> object:
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _seed_station(conn: sa.Connection, *, code: str, seed: int) -> StationId:
    station = make_station_config(code=code, rng=random.Random(seed))
    PgStationStore(conn).store_station(station)
    return station.id


def _insert_weather_source(
    conn: sa.Connection,
    *,
    station_id: StationId,
    nwp_source: str,
    role: str | None,
    status: str = "active",
    extraction_type: str = "basin_average",
) -> None:
    # Raw insert (not PgStationStore.store_weather_source) so a NULL role can
    # be seeded — the dataclass boundary requires a non-None WeatherSourceRole,
    # but a legacy pre-115a row could still carry a NULL role on real data.
    conn.execute(
        sa.insert(station_weather_sources).values(
            station_id=station_id,
            nwp_source=nwp_source,
            extraction_type=extraction_type,
            status=status,
            role=role,
        )
    )


def _insert_forcing_row(
    conn: sa.Connection,
    *,
    station_id: StationId,
    source: str,
    parameter: str = "precipitation",
    valid_time: UtcDatetime = _VALID_TIME,
    value: float = 1.23,
) -> None:
    conn.execute(
        sa.insert(historical_forcing).values(
            id=uuid4(),
            station_id=station_id,
            source=source,
            version="v1",
            valid_time=valid_time,
            parameter=parameter,
            spatial_type="basin_average",
            band_id=None,
            member_id=None,
            value=value,
        )
    )


def _camels_ch_row_count(conn: sa.Engine, station_id: StationId) -> int:
    with conn.connect() as c:
        return c.execute(
            sa.select(sa.func.count()).where(
                station_weather_sources.c.station_id == station_id,
                station_weather_sources.c.nwp_source == "camels-ch",
            )
        ).scalar_one()


class TestMigration0033GuardRaisesOnStrandedStation:
    """Guard RAISES on a would-be-stranded station (negative, load-bearing).

    Soundness: fails against an unconditional DELETE (no guard) — an
    unconditional delete would silently strand the station instead of
    raising, so this test is RED against that buggy variant.
    """

    def test_raises_and_deletes_nothing_even_with_forcing_rows_present(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="STRANDED-01", seed=101)
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="camels-ch",
                role="reanalysis",
            )
            # MeteoSwiss FORCING rows exist, but there is no MeteoSwiss
            # BINDING row — coverage is not membership (plan §"Why the guard
            # is REQUIRED"). This must still raise.
            _insert_forcing_row(
                conn, station_id=station_id, source="meteoswiss_rhiresd"
            )

        with pytest.raises(RuntimeError, match="no surviving reanalysis binding"):
            command.upgrade(cfg, "0033")

        # Atomic: the failed upgrade deleted nothing.
        assert _camels_ch_row_count(engine, station_id) == 1


class TestMigration0033EdgeCaseMembership:
    """Guard/delete predicate matches the reader's effective membership —
    no status filter, NULL role on camels-ch still counts as reanalysis.

    Soundness: fails against a ``status='active'``-only or
    ``role='reanalysis'``-only SQL predicate (either would miss these rows).
    """

    def test_inactive_camels_ch_only_station_still_raises(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="INACTIVE-01", seed=102)
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="camels-ch",
                role="reanalysis",
                status="inactive",
            )

        # fetch_reanalysis_bindings does not filter by status (station_store.py
        # :310-317) — an inactive-only camels-ch binding still counts as this
        # station's sole reanalysis binding, so retiring it must still raise.
        with pytest.raises(RuntimeError, match="no surviving reanalysis binding"):
            command.upgrade(cfg, "0033")

        assert _camels_ch_row_count(engine, station_id) == 1

    def test_legacy_null_role_camels_ch_only_station_raises(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="NULLROLE-01", seed=103)
            _insert_weather_source(
                conn, station_id=station_id, nwp_source="camels-ch", role=None
            )

        # A NULL role on camels-ch is still REANALYSIS to the reader
        # (_legacy_role_for_source's allowlist), so this must raise exactly
        # like an explicit role='reanalysis' row would.
        with pytest.raises(RuntimeError, match="no surviving reanalysis binding"):
            command.upgrade(cfg, "0033")

        assert _camels_ch_row_count(engine, station_id) == 1

    def test_legacy_null_role_camels_ch_is_deleted_when_survivor_present(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="NULLROLE-02", seed=104)
            _insert_weather_source(
                conn, station_id=station_id, nwp_source="camels-ch", role=None
            )
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="meteoswiss_open_data_reanalysis",
                role="reanalysis",
            )

        command.upgrade(cfg, "0033")

        assert _camels_ch_row_count(engine, station_id) == 0


class TestMigration0033GuardPassesAndDeletesOnlyTheBinding:
    """Guard PASSES + deletes only the camels-ch binding when a surviving
    non-camels-ch reanalysis binding exists; forcing rows are untouched."""

    def test_deletes_camels_ch_binding_keeps_meteoswiss_and_forcing_rows(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="SURVIVOR-01", seed=105)
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="camels-ch",
                role="reanalysis",
            )
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="meteoswiss_open_data_reanalysis",
                role="reanalysis",
            )
            _insert_forcing_row(conn, station_id=station_id, source="camels-ch")
            _insert_forcing_row(
                conn,
                station_id=station_id,
                source="camels-ch",
                valid_time=ensure_utc(datetime(2026, 1, 6, tzinfo=UTC)),
                value=4.56,
            )

        with engine.connect() as conn:
            forcing_before = conn.execute(
                sa.select(sa.func.count()).where(
                    historical_forcing.c.station_id == station_id,
                    historical_forcing.c.source == "camels-ch",
                )
            ).scalar_one()
        assert forcing_before == 2

        command.upgrade(cfg, "0033")

        with engine.connect() as conn:
            remaining = (
                conn.execute(
                    sa.select(station_weather_sources.c.nwp_source).where(
                        station_weather_sources.c.station_id == station_id
                    )
                )
                .scalars()
                .all()
            )
            forcing_after = conn.execute(
                sa.select(sa.func.count()).where(
                    historical_forcing.c.station_id == station_id,
                    historical_forcing.c.source == "camels-ch",
                )
            ).scalar_one()

        assert "camels-ch" not in remaining
        assert "meteoswiss_open_data_reanalysis" in remaining
        assert forcing_after == forcing_before, (
            "historical_forcing rows tagged camels-ch must survive the retire "
            "(115b3 validation reference + audit trail)"
        )


class TestMigration0033HybridReaderStillServesAfterRetire:
    def test_positive_station_resolves_reanalysis_forcing_via_meteoswiss_binding(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            default_hybrid_forcing_source,
        )
        from sapphire_flow.store.historical_forcing_store import (
            PgHistoricalForcingStore,
        )

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="HYBRID-01", seed=106)
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="camels-ch",
                role="reanalysis",
            )
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="meteoswiss_open_data_reanalysis",
                role="reanalysis",
            )
            _insert_forcing_row(
                conn,
                station_id=station_id,
                source="meteoswiss_rhiresd",
                parameter="precipitation",
            )

        command.upgrade(cfg, "0033")

        with engine.connect() as conn:
            station_store = PgStationStore(conn)
            bindings = station_store.fetch_reanalysis_bindings(station_id)
            forcing_source = default_hybrid_forcing_source(
                forcing_store=PgHistoricalForcingStore(conn)
            )
            rows = forcing_source.fetch_reanalysis(
                station_configs=bindings,
                start=_START,
                end=_END,
                parameters=["precipitation"],
            )

        assert len(bindings) == 1
        assert bindings[0].nwp_source == "meteoswiss_open_data_reanalysis"
        assert rows, (
            "hybrid reader must still resolve reanalysis forcing for the "
            "station using only its surviving MeteoSwiss binding"
        )
        assert all(r.station_id == station_id for r in rows)


class TestMigration0033DowngradeIsSafeNoop:
    def test_downgrade_resurrects_nothing(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn, code="DOWNGRADE-01", seed=107)
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="camels-ch",
                role="reanalysis",
            )
            _insert_weather_source(
                conn,
                station_id=station_id,
                nwp_source="meteoswiss_open_data_reanalysis",
                role="reanalysis",
            )

        command.upgrade(cfg, "0033")
        assert _camels_ch_row_count(engine, station_id) == 0

        # Must not raise, and must not resurrect the deleted binding.
        command.downgrade(cfg, "0032")
        assert _camels_ch_row_count(engine, station_id) == 0
