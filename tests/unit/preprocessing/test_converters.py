import uuid
from datetime import UTC, datetime

import polars as pl
import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.preprocessing.converters import (
    basin_avg_to_records,
    elevation_band_to_records,
    point_forecast_to_records,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import (
    BasinAverageForecast,
    ElevationBandForecast,
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


def _make_band_forecast() -> tuple[
    StationId, ElevationBandForecast, dict[tuple[int, int, datetime], float]
]:
    sid = StationId(uuid.uuid4())
    ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
    t1 = datetime(2026, 4, 1, 7, tzinfo=UTC)
    t2 = datetime(2026, 4, 1, 8, tzinfo=UTC)

    band_ids: list[int] = []
    member_ids: list[int] = []
    valid_times: list[datetime] = []
    parameters: list[str] = []
    values: list[float] = []
    expected: dict[tuple[int, int, datetime], float] = {}

    v = 0.0
    for band in (1000, 2000):
        for member in (0, 1):
            for vt in (t1, t2):
                band_ids.append(band)
                member_ids.append(member)
                valid_times.append(vt)
                parameters.append("precipitation")
                values.append(v)
                expected[(band, member, vt)] = v
                v += 1.0

    df = pl.DataFrame(
        {
            "valid_time": valid_times,
            "parameter": parameters,
            "member_id": member_ids,
            "band_id": band_ids,
            "value": values,
        },
        schema={
            "valid_time": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "member_id": pl.Int64,
            "band_id": pl.Int64,
            "value": pl.Float64,
        },
    )
    forecast = ElevationBandForecast(nwp_source="ifs_ecmwf", cycle_time=ct, values=df)
    return sid, forecast, expected


class TestElevationBandToRecords:
    def test_record_count_is_bands_times_members_times_timesteps(self) -> None:
        sid, forecast, _ = _make_band_forecast()
        records = elevation_band_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        # 2 bands × 2 members × 2 timesteps
        assert len(records) == 8

    def test_every_record_is_elevation_band_with_non_null_band_and_member(self) -> None:
        sid, forecast, _ = _make_band_forecast()
        records = elevation_band_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        for r in records:
            assert r.spatial_type == SpatialRepresentation.ELEVATION_BAND
            assert r.band_id is not None
            assert r.member_id is not None

    def test_distinct_band_ids_preserved(self) -> None:
        sid, forecast, _ = _make_band_forecast()
        records = elevation_band_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        assert {r.band_id for r in records} == {1000, 2000}

    def test_values_and_timestamps_preserved(self) -> None:
        sid, forecast, expected = _make_band_forecast()
        records = elevation_band_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        got = {(r.band_id, r.member_id, r.valid_time): r.value for r in records}
        assert got == expected


class TestReanalysisTagGuard:
    """Plan 115b4 §6C — a reanalysis provenance tag (e.g. ``meteoswiss_rhiresd``,
    ``camels-ch``) must never be written into ``weather_forecasts`` via any of
    the three converters. All three write ``WeatherForecastRecord.nwp_source``
    (``converters.py``), so all three must reject it.
    """

    @pytest.mark.parametrize(
        "reanalysis_tag",
        [
            "meteoswiss_rhiresd",
            "meteoswiss_rprelimd",
            "meteoswiss_tabsd",
            "meteoswiss_tmind",
            "meteoswiss_tmaxd",
            "meteoswiss_sreld",
            "camels-ch",
        ],
    )
    def test_basin_avg_to_records_rejects_reanalysis_tag(
        self, reanalysis_tag: str
    ) -> None:
        sid, forecast = _make_forecast()
        tagged = BasinAverageForecast(
            nwp_source=reanalysis_tag,
            cycle_time=forecast.cycle_time,
            values=forecast.values,
        )

        with pytest.raises(ConfigurationError, match=reanalysis_tag):
            basin_avg_to_records(sid, tagged, _fixed_clock, uuid.uuid4)

    def test_point_forecast_to_records_rejects_reanalysis_tag(self) -> None:
        sid, forecast = _make_point_forecast()
        tagged = PointForecast(
            nwp_source="camels-ch",
            cycle_time=forecast.cycle_time,
            values=forecast.values,
        )

        with pytest.raises(ConfigurationError, match="camels-ch"):
            point_forecast_to_records(sid, tagged, _fixed_clock, uuid.uuid4)

    def test_elevation_band_to_records_rejects_reanalysis_tag(self) -> None:
        sid, forecast, _ = _make_band_forecast()
        tagged = ElevationBandForecast(
            nwp_source="meteoswiss_tabsd",
            cycle_time=forecast.cycle_time,
            values=forecast.values,
        )

        with pytest.raises(ConfigurationError, match="meteoswiss_tabsd"):
            elevation_band_to_records(sid, tagged, _fixed_clock, uuid.uuid4)

    def test_genuine_forecast_source_is_not_rejected(self) -> None:
        sid, forecast = _make_forecast()
        # icon_ch2_eps is a FORECAST product tag, not a reanalysis one — must
        # pass straight through.
        records = basin_avg_to_records(sid, forecast, _fixed_clock, uuid.uuid4)
        assert len(records) == 3
