from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import historical_forcing, station_weather_sources
from sapphire_flow.types.datetime import ensure_utc
from tests.integration.store.test_forecast_store import _seed_station

if TYPE_CHECKING:
    from collections.abc import Iterator

_UTC_2020_01_01 = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_UTC_2026_05_01 = ensure_utc(datetime(2026, 5, 1, tzinfo=UTC))
_UTC_2030_01_01 = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade/downgrade can
    mutate the schema without disturbing the shared session-scoped
    integration engine."""
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


class TestMigration0033RetireCamelsWeatherBinding:
    """Plan 115b4 §5E — a SEPARATE, LATER release from the reader flip (5D).
    The migration deletes ONLY the camels-ch station_weather_sources row; the
    historical_forcing rows (the audit trail) are untouched and remain
    readable by a direct source-keyed fetch.
    """

    def test_upgrade_deletes_only_the_camels_ch_binding(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        # Land at the pre-retire head first, so the camels-ch binding can be
        # seeded exactly as Release A would have left it on staging.
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            conn.execute(
                sa.insert(station_weather_sources).values(
                    station_id=station_id,
                    nwp_source="camels-ch",
                    extraction_type="point",
                    status="active",
                    role="reanalysis",
                )
            )
            conn.execute(
                sa.insert(station_weather_sources).values(
                    station_id=station_id,
                    nwp_source="meteoswiss_open_data_reanalysis",
                    extraction_type="basin_average",
                    status="active",
                    role="forecast",
                )
            )
            conn.execute(
                sa.insert(historical_forcing).values(
                    id=sa.func.gen_random_uuid(),
                    station_id=station_id,
                    source="camels-ch",
                    version="v1",
                    valid_time=sa.func.now(),
                    parameter="precipitation",
                    spatial_type="basin_average",
                    band_id=None,
                    member_id=None,
                    value=5.0,
                )
            )

        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            remaining_bindings = conn.execute(
                sa.select(station_weather_sources.c.nwp_source).where(
                    station_weather_sources.c.station_id == station_id
                )
            ).all()
            forcing_rows = conn.execute(
                sa.select(historical_forcing.c.source).where(
                    historical_forcing.c.station_id == station_id
                )
            ).all()

        # The camels-ch BINDING is gone; the non-camels binding survives.
        assert [r[0] for r in remaining_bindings] == ["meteoswiss_open_data_reanalysis"]
        # The camels-ch forcing ROW (the audit trail) is untouched.
        assert [r[0] for r in forcing_rows] == ["camels-ch"]

    def test_downgrade_is_a_noop_not_a_fabricated_restore(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        # The migration chain must stay mechanically traversable (other
        # revisions' downgrade paths walk THROUGH 0033) — so downgrade()
        # must NOT raise. But it also must NOT resurrect any camels-ch
        # binding: which stations had one is exactly the information
        # upgrade() destroyed, so a "restore" would be fabricated.
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")
        with engine.begin() as conn:
            station_id = _seed_station(conn)
            conn.execute(
                sa.insert(station_weather_sources).values(
                    station_id=station_id,
                    nwp_source="camels-ch",
                    extraction_type="point",
                    status="active",
                    role="reanalysis",
                )
            )
        command.upgrade(cfg, "head")

        # Must complete without raising.
        command.downgrade(cfg, "0032")

        with engine.connect() as conn:
            remaining = conn.execute(
                sa.select(station_weather_sources.c.nwp_source).where(
                    station_weather_sources.c.station_id == station_id
                )
            ).all()

        # No fabricated resurrection — the binding stays gone.
        assert remaining == []

    def test_hybrid_reader_serves_rows_at_release_a_head_without_0033(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        # Plan 115b4 §5E/Tests: "a station on the hybrid reader serves rows;
        # a test that the retire migration is absent from Release A's
        # head." Release A ships NO new migration (the flip is pure
        # code/config) — this proves the hybrid reader's serving capability
        # does NOT depend on 0033 having been applied: a station serves rows
        # through the REAL PgHistoricalForcingStore while the schema sits at
        # 0032 (Release A's head), one revision BEFORE the camels-ch retire.
        from alembic import command
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source,
        )
        from sapphire_flow.store.historical_forcing_store import (
            PgHistoricalForcingStore,
        )
        from sapphire_flow.types.enums import (
            SpatialRepresentation,
            WeatherSourceRole,
            WeatherSourceStatus,
        )
        from sapphire_flow.types.station import StationWeatherSource

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0032")

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            conn.execute(
                sa.insert(historical_forcing).values(
                    id=sa.func.gen_random_uuid(),
                    station_id=station_id,
                    source="meteoswiss_rhiresd",
                    version="v1",
                    valid_time=_UTC_2026_05_01,
                    parameter="precipitation",
                    spatial_type="basin_average",
                    band_id=None,
                    member_id=None,
                    value=6.0,
                )
            )

        with engine.connect() as conn:
            forcing_store = PgHistoricalForcingStore(conn)
            reader = select_reanalysis_source(
                forcing_store=forcing_store, mode="hybrid"
            )
            binding = StationWeatherSource(
                station_id=station_id,
                nwp_source="meteoswiss_open_data_reanalysis",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
            rows = reader.fetch_reanalysis(
                [binding],
                start=_UTC_2020_01_01,
                end=_UTC_2030_01_01,
                parameters=["precipitation"],
            )

        assert len(rows) == 1
        assert rows[0].source == "meteoswiss_rhiresd"
        assert rows[0].value == 6.0
