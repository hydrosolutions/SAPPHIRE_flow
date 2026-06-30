from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest
import xarray as xr
from shapely.geometry import box

from sapphire_flow.exceptions import ExtractionError
from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
    ExactExtractGridExtractor,
)
from sapphire_flow.protocols.grid_extractor import GridExtractor
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import BasinAverageForecast


def _make_grid(
    *,
    n_members: int = 3,
    n_times: int = 2,
    n_lat: int = 4,
    n_lon: int = 4,
    precip_fill: float = 10.0,
    temp_fill: float = 20.0,
) -> xr.Dataset:
    return xr.Dataset(
        {
            "precipitation": xr.DataArray(
                np.full(
                    (n_members, n_times, n_lat, n_lon), precip_fill, dtype=np.float32
                ),
                dims=["member", "valid_time", "latitude", "longitude"],
                coords={
                    "member": np.arange(n_members),
                    "valid_time": [
                        datetime(2026, 4, 1, h, tzinfo=UTC) for h in range(n_times)
                    ],
                    "latitude": np.linspace(46.0, 48.0, n_lat),
                    "longitude": np.linspace(6.0, 10.0, n_lon),
                },
            ),
            "temperature": xr.DataArray(
                np.full(
                    (n_members, n_times, n_lat, n_lon), temp_fill, dtype=np.float32
                ),
                dims=["member", "valid_time", "latitude", "longitude"],
                coords={
                    "member": np.arange(n_members),
                    "valid_time": [
                        datetime(2026, 4, 1, h, tzinfo=UTC) for h in range(n_times)
                    ],
                    "latitude": np.linspace(46.0, 48.0, n_lat),
                    "longitude": np.linspace(6.0, 10.0, n_lon),
                },
            ),
        }
    )


def _make_basin(station_id: StationId) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code="test_basin",
        name="Test Basin",
        geometry=box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        network="test",
    )


def _make_config(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="icon_ch2_eps",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
    )


class TestExactExtractGridExtractor:
    def test_protocol_conformance(self) -> None:
        assert isinstance(ExactExtractGridExtractor(), GridExtractor)

    def test_basic_extraction(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid = StationId(uuid.uuid4())
        basin = _make_basin(sid)
        config = _make_config(sid)
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        result = extractor.extract(grid, [config], {sid: basin}, ct, "icon_ch2_eps")

        assert sid in result
        forecast = result[sid]
        assert isinstance(forecast, BasinAverageForecast)
        assert forecast.nwp_source == "icon_ch2_eps"
        assert forecast.cycle_time == ct

        df = forecast.values
        precip_vals = df.filter(pl.col("parameter") == "precipitation")["value"]
        assert all(abs(v - 10.0) < 0.1 for v in precip_vals)
        temp_vals = df.filter(pl.col("parameter") == "temperature")["value"]
        assert all(abs(v - 20.0) < 0.1 for v in temp_vals)

    def test_multiple_basins(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid1 = StationId(uuid.uuid4())
        sid2 = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        result = extractor.extract(
            grid,
            [_make_config(sid1), _make_config(sid2)],
            {sid1: _make_basin(sid1), sid2: _make_basin(sid2)},
            ct,
            "icon_ch2_eps",
        )
        assert len(result) == 2
        assert sid1 in result
        assert sid2 in result

    def test_ensemble_members_preserved(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid(n_members=21)
        sid = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        result = extractor.extract(
            grid, [_make_config(sid)], {sid: _make_basin(sid)}, ct, "icon_ch2_eps"
        )
        df = result[sid].values
        unique_members = df["member_id"].unique().sort()
        assert len(unique_members) == 21

    def test_missing_basin_skipped(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid1 = StationId(uuid.uuid4())
        sid2 = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        result = extractor.extract(
            grid,
            [_make_config(sid1), _make_config(sid2)],
            {sid1: _make_basin(sid1)},
            ct,
            "icon_ch2_eps",
        )
        assert sid1 in result
        assert sid2 not in result

    def test_empty_basins_raises_extraction_error(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        with pytest.raises(ExtractionError):
            extractor.extract(grid, [_make_config(sid)], {}, ct, "icon_ch2_eps")

    def test_valid_time_utc(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        result = extractor.extract(
            grid, [_make_config(sid)], {sid: _make_basin(sid)}, ct, "icon_ch2_eps"
        )
        df = result[sid].values
        assert df["valid_time"].dtype == pl.Datetime("us", "UTC")

    def test_dataframe_row_count(self) -> None:
        n_members = 3
        n_times = 2
        n_params = 2  # precipitation + temperature
        extractor = ExactExtractGridExtractor()
        grid = _make_grid(n_members=n_members, n_times=n_times)
        sid = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))

        result = extractor.extract(
            grid, [_make_config(sid)], {sid: _make_basin(sid)}, ct, "icon_ch2_eps"
        )
        df = result[sid].values
        expected_rows = n_members * n_times * n_params
        assert len(df) == expected_rows


def _make_out_of_extent_basin(station_id: StationId) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code="out_of_extent",
        name="Out of Extent",
        geometry=box(29.9, 59.9, 30.1, 60.1),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        network="test",
    )


class TestOutOfExtent:
    def test_polygon_outside_grid_extent_raises(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))
        with pytest.raises(ExtractionError, match="outside grid extent"):
            extractor.extract(
                grid,
                [_make_config(sid)],
                {sid: _make_out_of_extent_basin(sid)},
                ct,
                "icon_ch2_eps",
            )

    def test_out_of_extent_message_lists_all_offenders(self) -> None:
        extractor = ExactExtractGridExtractor()
        grid = _make_grid()
        sid_a = StationId(uuid.uuid4())
        sid_b = StationId(uuid.uuid4())
        ct = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))
        with pytest.raises(ExtractionError) as exc_info:
            extractor.extract(
                grid,
                [_make_config(sid_a), _make_config(sid_b)],
                {
                    sid_a: _make_out_of_extent_basin(sid_a),
                    sid_b: _make_out_of_extent_basin(sid_b),
                },
                ct,
                "icon_ch2_eps",
            )
        msg = str(exc_info.value)
        assert str(sid_a) in msg and str(sid_b) in msg


class TestNaiveDatetimeLocalizedToUtc:
    def test_naive_datetime_localized_to_utc(self) -> None:
        # ICON valid_times are UTC. The parsed cube carries tz-naive values
        # (numpy.datetime64 / naive datetime); they are localized to UTC, not
        # shifted, and not rejected.
        from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
            _to_utc_datetime,
        )

        result = _to_utc_datetime(datetime(2026, 4, 1, 0, 0))
        assert result == datetime(2026, 4, 1, 0, 0, tzinfo=UTC)

    def test_naive_numpy_datetime64_localized_to_utc(self) -> None:
        from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
            _to_utc_datetime,
        )

        result = _to_utc_datetime(np.datetime64("2026-04-01T00:00:00"))
        assert result == datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
