"""PostgreSQL physical-idempotency test for the Plan 146 recap-reanalysis
snow ingest flow (Phase 2d, "major review fix").

``store_forcing`` returns ``None`` and a fake-store flow test can only report
``len(records)`` — it cannot prove the store's ``on_conflict_do_nothing()``
upsert actually dedups at the physical-row level. This test runs the flow
TWICE over an identical rolling window (same fetched rows, same version) and
asserts the PHYSICAL row count in ``historical_forcing`` is unchanged after
the second run — the one thing genuinely new to 146 (version-supersession
semantics are already LOCKED independently by
``tests/integration/store/test_historical_forcing_supersession.py`` and are
deliberately NOT re-proven here).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import historical_forcing
from sapphire_flow.flows.ingest_recap_reanalysis import ingest_recap_reanalysis_flow
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.pipeline_health_store import PgPipelineHealthStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import GatewayHruName, SnowReanalysisFetchResult
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId

_NOW = ensure_utc(datetime(2026, 6, 15, 6, 0, tzinfo=UTC))
_VALID_TIME = datetime(2026, 6, 1, tzinfo=UTC)


def _clock() -> UtcDatetime:
    return _NOW


@dataclass(frozen=True, kw_only=True, slots=True)
class _FixedSnowAdapter:
    """Returns the SAME fetch result (rows + version) every call — models a
    re-run over an overlapping window where the source has not changed."""

    station_id: StationId

    def fetch_snow_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        variables: list[str] | None = None,
    ) -> SnowReanalysisFetchResult:
        rows = [
            RawHistoricalForcing(
                station_id=self.station_id,
                source="recap_snow_reanalysis",
                version="2026-06-01T00:00:00+00:00",
                valid_time=ensure_utc(_VALID_TIME),
                parameter=parameter,
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=None,
                value=9.0,
            )
            for parameter in ("swe", "snow_depth", "snowmelt")
        ]
        return SnowReanalysisFetchResult(
            rows=rows,
            unavailable={},
            attempted={
                GatewayHruName("hru_test"): frozenset({"swe", "snow_depth", "snowmelt"})
            },
            resolved={self.station_id: GatewayHruName("hru_test")},
            skipped={},
        )


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(network="camels", code="RECAP-146-PG")
    PgStationStore(conn).store_station(station)
    PgStationStore(conn).store_weather_source(
        StationWeatherSource(
            station_id=station.id,
            nwp_source="era5_land",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    return station.id


def _physical_row_count(conn: sa.Connection, station_id: StationId) -> int:
    q = sa.select(sa.func.count()).where(
        historical_forcing.c.station_id == station_id,
        historical_forcing.c.source == "recap_snow_reanalysis",
    )
    return conn.execute(q).scalar_one()


class TestPhysicalIdempotency:
    def test_second_run_over_overlapping_window_stores_no_duplicate_rows(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        station_store = PgStationStore(db_connection)
        forcing_store = PgHistoricalForcingStore(db_connection)
        health_store = PgPipelineHealthStore(db_connection)
        adapter = _FixedSnowAdapter(station_id=sid)

        first = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )
        assert first.rows_stored == 3
        count_after_first = _physical_row_count(db_connection, sid)
        assert count_after_first == 3

        second = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )
        # The flow still fetches+attempts to store the same 3 rows (rows_stored
        # reports len(records), Plan 146 D2/D5 — "no watermark"), but the
        # PHYSICAL row count must not grow: on_conflict_do_nothing() upserts
        # the identical natural key with zero new rows.
        assert second.rows_stored == 3
        count_after_second = _physical_row_count(db_connection, sid)
        assert count_after_second == count_after_first == 3
