from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.weather_forecast_store import PgWeatherForecastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import make_station_config

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import StationId

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_CYCLE_A = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
_CYCLE_B = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
_NWP = "icon_ch2_eps"


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(rng=random.Random(99))
    PgStationStore(conn).store_station(station)
    return station.id


def _make_record(
    station_id: StationId,
    *,
    cycle_time: object = None,
    valid_time: object = None,
    parameter: str = "precipitation",
    member_id: int | None = 0,
    value: float = 1.5,
) -> WeatherForecastRecord:
    ct = cycle_time if cycle_time is not None else _CYCLE_A
    vt = (
        valid_time
        if valid_time is not None
        else ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
    )
    return WeatherForecastRecord(
        id=UUID(int=uuid4().int),
        station_id=station_id,
        nwp_source=_NWP,
        cycle_time=ct,  # type: ignore[arg-type]
        valid_time=vt,  # type: ignore[arg-type]
        parameter=parameter,
        spatial_type=SpatialRepresentation.POINT,
        band_id=None,
        member_id=member_id,
        value=value,
        created_at=_NOW,
    )


class TestStoreAndFetch:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt1 = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        vt2 = ensure_utc(datetime(2025, 1, 1, 2, tzinfo=UTC))
        r1 = _make_record(
            sid, cycle_time=_CYCLE_A, valid_time=vt1, member_id=0, value=1.0
        )
        r2 = _make_record(
            sid, cycle_time=_CYCLE_A, valid_time=vt2, member_id=0, value=2.0
        )

        store.store_weather_forecasts([r1, r2])

        fetched = store.fetch_weather_forecasts(sid, _NWP, _CYCLE_A)
        assert len(fetched) == 2
        fetched_ids = {r.id for r in fetched}
        assert r1.id in fetched_ids
        assert r2.id in fetched_ids

    def test_fetch_filters_by_parameter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r_prec = _make_record(
            sid, valid_time=vt, parameter="precipitation", member_id=0
        )
        r_temp = _make_record(sid, valid_time=vt, parameter="temperature", member_id=0)

        store.store_weather_forecasts([r_prec, r_temp])

        fetched = store.fetch_weather_forecasts(
            sid, _NWP, _CYCLE_A, parameters=["precipitation"]
        )
        assert len(fetched) == 1
        assert fetched[0].parameter == "precipitation"

    def test_fetch_returns_empty_for_wrong_cycle(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r = _make_record(sid, cycle_time=_CYCLE_A, valid_time=vt)
        store.store_weather_forecasts([r])

        fetched = store.fetch_weather_forecasts(sid, _NWP, _CYCLE_B)
        assert fetched == []


class TestStoreDedup:
    def test_store_dedup(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r = _make_record(sid, valid_time=vt, member_id=0)

        store.store_weather_forecasts([r])
        store.store_weather_forecasts([r])

        fetched = store.fetch_weather_forecasts(sid, _NWP, _CYCLE_A)
        assert len(fetched) == 1


class TestFetchLookback:
    def test_half_open_range(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt_before = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        vt_start = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        vt_mid = ensure_utc(datetime(2025, 1, 1, 2, tzinfo=UTC))
        vt_end = ensure_utc(datetime(2025, 1, 1, 3, tzinfo=UTC))

        r_before = _make_record(sid, valid_time=vt_before, member_id=0, value=0.0)
        r_start = _make_record(sid, valid_time=vt_start, member_id=0, value=1.0)
        r_mid = _make_record(sid, valid_time=vt_mid, member_id=0, value=2.0)
        r_end = _make_record(sid, valid_time=vt_end, member_id=0, value=3.0)

        store.store_weather_forecasts([r_before, r_start, r_mid, r_end])

        fetched = store.fetch_lookback(sid, _NWP, vt_start, vt_end)
        values = {r.value for r in fetched}
        assert 1.0 in values
        assert 2.0 in values
        assert 0.0 not in values
        assert 3.0 not in values

    def test_returns_empty_when_no_match(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        start = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 6, 2, tzinfo=UTC))
        result = store.fetch_lookback(sid, _NWP, start, end)
        assert result == []


class TestFetchReceivedCycles:
    def test_fetch_received_cycles_sorted(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r_a = _make_record(sid, cycle_time=_CYCLE_A, valid_time=vt, member_id=0)
        r_b = _make_record(sid, cycle_time=_CYCLE_B, valid_time=vt, member_id=0)
        store.store_weather_forecasts([r_a, r_b])

        window_start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        window_end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        cycles = store.fetch_received_cycles(_NWP, window_start, window_end)

        assert cycles == sorted(cycles)
        assert len(cycles) == 2
        assert _CYCLE_A in cycles
        assert _CYCLE_B in cycles

    def test_half_open_range_excludes_end(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r = _make_record(sid, cycle_time=_CYCLE_B, valid_time=vt, member_id=0)
        store.store_weather_forecasts([r])

        cycles = store.fetch_received_cycles(_NWP, _CYCLE_A, _CYCLE_B)
        assert cycles == []

    def test_returns_distinct_cycles(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt1 = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        vt2 = ensure_utc(datetime(2025, 1, 1, 2, tzinfo=UTC))
        r1 = _make_record(sid, cycle_time=_CYCLE_A, valid_time=vt1, member_id=0)
        r2 = _make_record(sid, cycle_time=_CYCLE_A, valid_time=vt2, member_id=0)
        store.store_weather_forecasts([r1, r2])

        window_start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        window_end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        cycles = store.fetch_received_cycles(_NWP, window_start, window_end)
        assert len(cycles) == 1


class TestFetchLatestCycleTime:
    def test_fetch_latest_cycle_time(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r_a = _make_record(sid, cycle_time=_CYCLE_A, valid_time=vt, member_id=0)
        r_b = _make_record(sid, cycle_time=_CYCLE_B, valid_time=vt, member_id=0)
        store.store_weather_forecasts([r_a, r_b])

        latest = store.fetch_latest_cycle_time(_NWP)
        assert latest == _CYCLE_B

    def test_returns_none_when_empty(self, db_connection: sa.Connection) -> None:
        _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)
        result = store.fetch_latest_cycle_time(_NWP)
        assert result is None

    def test_filters_by_nwp_source(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)

        vt = ensure_utc(datetime(2025, 1, 1, 1, tzinfo=UTC))
        r = WeatherForecastRecord(
            id=UUID(int=uuid4().int),
            station_id=sid,
            nwp_source="other_model",
            cycle_time=_CYCLE_B,
            valid_time=vt,
            parameter="precipitation",
            spatial_type=SpatialRepresentation.POINT,
            band_id=None,
            member_id=0,
            value=5.0,
            created_at=_NOW,
        )
        store.store_weather_forecasts([r])

        result = store.fetch_latest_cycle_time(_NWP)
        assert result is None


class TestMarkGapNoop:
    def test_mark_gap_noop(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgWeatherForecastStore(db_connection)
        store.mark_gap(sid, _NWP, _CYCLE_A, recoverable=True)
        store.mark_gap(sid, _NWP, _CYCLE_A, recoverable=False)
