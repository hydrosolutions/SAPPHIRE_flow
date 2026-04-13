import uuid
from datetime import UTC, datetime

import polars as pl

from sapphire_flow.preprocessing.converters import (
    basin_avg_to_records,
    point_forecast_to_records,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import (
    BasinAverageForecast,
    PointForecast,
    WeatherForecastRecord,
)


def _make_forecast() -> tuple[StationId, BasinAverageForecast]:
    sid = StationId(uuid.uuid4())
    ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
    df = pl.DataFrame(
        {
            "valid_time": [
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
                datetime(2026, 4, 1, 7, tzinfo=UTC),
            ],
            "parameter": ["precipitation", "precipitation", "temperature"],
            "member_id": [0, 0, 1],
            "value": [2.5, 3.0, 15.0],
        },
        schema={
            "valid_time": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "member_id": pl.Int64,
            "value": pl.Float64,
        },
    )
    forecast = BasinAverageForecast(nwp_source="icon_ch2_eps", cycle_time=ct, values=df)
    return sid, forecast


def _fixed_clock() -> UtcDatetime:
    return ensure_utc(datetime(2026, 4, 1, 10, tzinfo=UTC))


class TestBasinAvgToRecords:
    def test_produces_correct_record_count(self) -> None:
        sid, forecast = _make_forecast()
        records = basin_avg_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        assert len(records) == 3

    def test_record_fields(self) -> None:
        sid, forecast = _make_forecast()
        now = ensure_utc(datetime(2026, 4, 1, 10, tzinfo=UTC))

        def clock() -> UtcDatetime:
            return now

        counter = iter(range(100))

        def id_gen() -> uuid.UUID:
            return uuid.UUID(int=next(counter))

        records = basin_avg_to_records(sid, forecast, clock, id_gen)

        r = records[0]
        assert isinstance(r, WeatherForecastRecord)
        assert r.station_id == sid
        assert r.nwp_source == "icon_ch2_eps"
        assert r.spatial_type == SpatialRepresentation.BASIN_AVERAGE
        assert r.band_id is None
        assert r.is_gap is False
        assert r.gap_status is None
        assert r.created_at == now
        assert r.parameter == "precipitation"
        assert r.member_id == 0
        assert abs(r.value - 2.5) < 0.01

    def test_all_records_have_unique_ids(self) -> None:
        sid, forecast = _make_forecast()
        records = basin_avg_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        ids = [r.id for r in records]
        assert len(set(ids)) == len(ids)

    def test_cycle_time_from_forecast(self) -> None:
        sid, forecast = _make_forecast()
        records = basin_avg_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        for r in records:
            assert r.cycle_time == forecast.cycle_time


def _make_point_forecast() -> tuple[StationId, PointForecast]:
    sid = StationId(uuid.uuid4())
    ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
    df = pl.DataFrame(
        {
            "valid_time": [
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 7, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
                datetime(2026, 4, 1, 8, tzinfo=UTC),
            ],
            "parameter": ["precipitation"] * 6 + ["temperature"] * 6,
            "member_id": [0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2],
            "value": [1.0, 1.1, 1.2, 2.0, 2.1, 2.2, 10.0, 10.1, 10.2, 11.0, 11.1, 11.2],
        },
        schema={
            "valid_time": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "member_id": pl.Int64,
            "value": pl.Float64,
        },
    )
    forecast = PointForecast(nwp_source="icon_ch2_eps", cycle_time=ct, values=df)
    return sid, forecast


class TestPointForecastToRecords:
    def test_produces_correct_record_count(self) -> None:
        sid, forecast = _make_point_forecast()
        records = point_forecast_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        # 2 valid_times × 2 parameters × 3 members = 12
        assert len(records) == 12

    def test_correct_field_mapping(self) -> None:
        sid, forecast = _make_point_forecast()
        now = ensure_utc(datetime(2026, 4, 1, 10, tzinfo=UTC))

        def clock() -> UtcDatetime:
            return now

        counter = iter(range(100))

        def id_gen() -> uuid.UUID:
            return uuid.UUID(int=next(counter))

        records = point_forecast_to_records(sid, forecast, clock, id_gen)

        r = records[0]
        assert isinstance(r, WeatherForecastRecord)
        assert r.station_id == sid
        assert r.nwp_source == "icon_ch2_eps"
        assert r.cycle_time == forecast.cycle_time
        assert r.spatial_type == SpatialRepresentation.POINT
        assert r.band_id is None
        assert r.is_gap is False
        assert r.gap_status is None
        assert r.created_at == now

    def test_empty_dataframe_returns_empty_list(self) -> None:
        sid = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        df = pl.DataFrame(
            schema={
                "valid_time": pl.Datetime("us", "UTC"),
                "parameter": pl.Utf8,
                "member_id": pl.Int64,
                "value": pl.Float64,
            }
        )
        forecast = PointForecast(nwp_source="icon_ch2_eps", cycle_time=ct, values=df)
        records = point_forecast_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        assert records == []
