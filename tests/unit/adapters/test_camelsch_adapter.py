from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.adapters.camelsch_adapter import (
    attributes_to_station,
    geometry_to_basin,
    timeseries_to_forcing,
    timeseries_to_observations,
    timeseries_to_waterlevel_observations,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ObservationSource,
    SpatialRepresentation,
    StationKind,
    StationStatus,
)
from sapphire_flow.types.ids import BasinId, StationId

_NOW = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_CLOCK = lambda: _NOW  # noqa: E731
_STATION_ID = StationId(uuid4())
_BASIN_ID = BasinId(uuid4())

_ATTRS = pd.Series(
    {
        "gauge_name": "Test Station",
        "area": 150.0,
        "gauge_lon": 8.5,
        "gauge_lat": 47.4,
        "elev_mean": 500.0,
    }
)


def _make_ts_df(data: dict) -> pd.DataFrame:
    df = pd.DataFrame(
        data,
        index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
    )
    df.index.name = "date"
    return df


class TestTimeseriesToObservations:
    def test_converts_discharge_vol(self) -> None:
        df = _make_ts_df({"discharge_vol": [10.5, 20.3, 5.0]})
        result = timeseries_to_observations(df, _STATION_ID, _CLOCK)

        assert len(result) == 3
        obs = result[0]
        assert obs.station_id == _STATION_ID
        assert obs.parameter == "discharge"
        assert obs.value == 10.5
        assert obs.source == ObservationSource.MANUAL_IMPORT

    def test_skips_nan_values(self) -> None:
        df = _make_ts_df({"discharge_vol": [10.5, float("nan"), 5.0]})
        result = timeseries_to_observations(df, _STATION_ID, _CLOCK)

        assert len(result) == 2
        assert all(not pd.isna(o.value) for o in result)

    def test_timestamp_is_utc(self) -> None:
        df = _make_ts_df({"discharge_vol": [10.5, 20.3, 5.0]})
        result = timeseries_to_observations(df, _STATION_ID, _CLOCK)

        for obs in result:
            assert obs.timestamp.tzinfo is not None
            assert obs.timestamp.tzinfo == UTC

    def test_returns_empty_when_column_missing(self) -> None:
        df = _make_ts_df({"precipitation": [5.0, 3.0, 1.0]})
        result = timeseries_to_observations(df, _STATION_ID, _CLOCK)

        assert result == []


class TestTimeseriesToForcing:
    def test_converts_precipitation_and_temperature(self) -> None:
        df = _make_ts_df(
            {"precipitation": [5.0, 3.0, 1.0], "temperature_mean": [10.0, 12.0, 8.0]}
        )
        result = timeseries_to_forcing(df, _STATION_ID)

        assert len(result) == 6
        params = {r.parameter for r in result}
        assert params == {"precipitation", "temperature"}

    def test_parameter_name_mapping(self) -> None:
        df = _make_ts_df({"temperature_mean": [10.0, 12.0, 8.0]})
        result = timeseries_to_forcing(df, _STATION_ID, parameters=["temperature_mean"])

        assert all(r.parameter == "temperature" for r in result)

    def test_skips_nan(self) -> None:
        df = _make_ts_df({"precipitation": [5.0, float("nan"), 1.0]})
        result = timeseries_to_forcing(df, _STATION_ID, parameters=["precipitation"])

        assert len(result) == 2

    def test_spatial_type_is_basin_average(self) -> None:
        df = _make_ts_df({"precipitation": [5.0, 3.0, 1.0]})
        result = timeseries_to_forcing(df, _STATION_ID, parameters=["precipitation"])

        expected = SpatialRepresentation.BASIN_AVERAGE
        assert all(r.spatial_type == expected for r in result)

    def test_source_and_version(self) -> None:
        df = _make_ts_df({"precipitation": [5.0, 3.0, 1.0]})
        result = timeseries_to_forcing(df, _STATION_ID, parameters=["precipitation"])

        assert all(r.source == "camels-ch" for r in result)
        assert all(r.version == "1.0" for r in result)

    def test_band_id_and_member_id_are_none(self) -> None:
        df = _make_ts_df({"precipitation": [5.0, 3.0, 1.0]})
        result = timeseries_to_forcing(df, _STATION_ID, parameters=["precipitation"])

        assert all(r.band_id is None for r in result)
        assert all(r.member_id is None for r in result)

    def test_valid_time_is_utc(self) -> None:
        df = _make_ts_df({"precipitation": [5.0, 3.0, 1.0]})
        result = timeseries_to_forcing(df, _STATION_ID, parameters=["precipitation"])

        for r in result:
            assert r.valid_time.tzinfo is not None
            assert r.valid_time.tzinfo == UTC


class TestAttributesToStation:
    def test_creates_station_config(self) -> None:
        station = attributes_to_station("2004", _ATTRS, _BASIN_ID, _STATION_ID, _CLOCK)

        assert station.id == _STATION_ID
        assert station.code == "2004"
        assert station.name == "Test Station"
        assert station.location.lon == pytest.approx(8.5)
        assert station.location.lat == pytest.approx(47.4)
        assert station.basin_id == _BASIN_ID
        assert station.timezone == "Europe/Zurich"
        assert station.forecast_target == "discharge"
        assert station.measured_parameters == frozenset({"discharge"})
        assert station.station_status == StationStatus.ONBOARDING
        assert station.network == "bafu"
        assert station.wigos_id is None

    def test_falls_back_to_gauge_id_when_no_gauge_name(self) -> None:
        attrs = pd.Series({"area": 150.0, "gauge_lon": 8.5, "gauge_lat": 47.4})
        station = attributes_to_station("2004", attrs, None, _STATION_ID, _CLOCK)

        assert station.name == "2004"
        assert station.basin_id is None


class TestGeometryToBasin:
    def test_creates_basin(self) -> None:
        mp = MultiPolygon([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])])
        basin = geometry_to_basin("2004", mp, _ATTRS, _BASIN_ID, _CLOCK)

        assert basin.id == _BASIN_ID
        assert basin.code == "2004"
        assert basin.name == "Test Station"
        assert basin.area_km2 == pytest.approx(150.0)
        assert basin.band_geometries is None
        assert basin.network == "bafu"
        assert basin.geometry.equals(mp)

    def test_converts_polygon_to_multipolygon(self) -> None:
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        basin = geometry_to_basin("2004", polygon, _ATTRS, _BASIN_ID, _CLOCK)

        assert isinstance(basin.geometry, MultiPolygon)

    def test_attributes_stored_as_dict(self) -> None:
        mp = MultiPolygon([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])])
        basin = geometry_to_basin("2004", mp, _ATTRS, _BASIN_ID, _CLOCK)

        assert isinstance(basin.attributes, dict)
        assert basin.attributes["gauge_name"] == "Test Station"
        assert basin.attributes["area"] == pytest.approx(150.0)

    def test_falls_back_to_gauge_id_when_no_gauge_name(self) -> None:
        attrs = pd.Series({"area": 200.0})
        mp = MultiPolygon([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])])
        basin = geometry_to_basin("2007", mp, attrs, _BASIN_ID, _CLOCK)

        assert basin.name == "2007"


_LAKE_ATTRS = pd.Series(
    {
        "gauge_name": "Lake Murten",
        "area": 150.0,
        "gauge_lon": 7.12,
        "gauge_lat": 46.93,
        "elev_mean": 500.0,
        "water_body_type": "lake",
    }
)


class TestTimeseriesToWaterlevelObservations:
    def test_converts_waterlevel(self) -> None:
        df = _make_ts_df({"waterlevel": [1.5, 2.3, 1.8]})
        result = timeseries_to_waterlevel_observations(df, _STATION_ID, _CLOCK)

        assert len(result) == 3
        obs = result[0]
        assert obs.station_id == _STATION_ID
        assert obs.parameter == "water_level"
        assert obs.value == pytest.approx(1.5)
        assert obs.source == ObservationSource.MANUAL_IMPORT

    def test_skips_nan(self) -> None:
        df = _make_ts_df({"waterlevel": [1.5, float("nan"), 1.8]})
        result = timeseries_to_waterlevel_observations(df, _STATION_ID, _CLOCK)

        assert len(result) == 2
        assert all(not pd.isna(o.value) for o in result)

    def test_returns_empty_when_column_missing(self) -> None:
        df = _make_ts_df({"discharge_vol": [10.5, 20.3, 5.0]})
        result = timeseries_to_waterlevel_observations(df, _STATION_ID, _CLOCK)

        assert result == []

    def test_timestamp_is_utc(self) -> None:
        df = _make_ts_df({"waterlevel": [1.5, 2.3, 1.8]})
        result = timeseries_to_waterlevel_observations(df, _STATION_ID, _CLOCK)
        for obs in result:
            assert obs.timestamp.tzinfo is not None


class TestAttributesToStationClassification:
    def test_stream_station(self) -> None:
        attrs = pd.Series(
            {
                "gauge_name": "Test Stream",
                "area": 100.0,
                "gauge_lon": 8.5,
                "gauge_lat": 47.4,
                "water_body_type": "stream",
            }
        )
        station = attributes_to_station("2004", attrs, _BASIN_ID, _STATION_ID, _CLOCK)

        assert station.station_kind == StationKind.RIVER
        assert station.forecast_target == "discharge"
        assert station.measured_parameters == frozenset({"discharge"})

    def test_lake_station(self) -> None:
        station = attributes_to_station(
            "3001", _LAKE_ATTRS, _BASIN_ID, _STATION_ID, _CLOCK
        )

        assert station.station_kind == StationKind.LAKE
        assert station.forecast_target == "water_level"
        assert station.measured_parameters == frozenset({"water_level"})

    def test_missing_type_defaults_to_river(self) -> None:
        station = attributes_to_station("2004", _ATTRS, _BASIN_ID, _STATION_ID, _CLOCK)

        assert station.station_kind == StationKind.RIVER
        assert station.forecast_target == "discharge"
        assert station.measured_parameters == frozenset({"discharge"})
