from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import xarray as xr
from shapely.geometry import box

from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter
from sapphire_flow.exceptions import ExtractionError
from sapphire_flow.preprocessing.mesh_basin_extractor import MeshBasinExtractor
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import BasinAverageForecast

# A tiny synthetic unstructured mesh of 4 cell centres (lon, lat). NOT a lat/lon
# grid — this is the (valid_time, member, values) layout the real ICON cube uses.
_CELL_LON = np.array([6.0, 6.1, 8.0, 8.1], dtype=np.float64)
_CELL_LAT = np.array([46.0, 46.1, 48.0, 48.1], dtype=np.float64)
# Basin box(5.9, 45.9, 6.2, 46.2) encloses exactly cells 0 and 1.
_BASIN_TWO_CELLS = box(5.9, 45.9, 6.2, 46.2)


def _make_mesh(
    values_3d: np.ndarray,
    *,
    param: str = "precipitation",
    lon: np.ndarray = _CELL_LON,
    lat: np.ndarray = _CELL_LAT,
) -> xr.Dataset:
    n_members, n_times, _ = values_3d.shape
    return xr.Dataset(
        {
            param: xr.DataArray(
                values_3d.astype(np.float64),
                dims=["member", "valid_time", "values"],
                coords={
                    "member": np.arange(n_members),
                    "valid_time": [
                        datetime(2026, 4, 1, h, tzinfo=UTC) for h in range(n_times)
                    ],
                    "latitude": ("values", lat),
                    "longitude": ("values", lon),
                },
            )
        }
    )


def _broadcast_cells(
    cell_values: list[float], n_members: int, n_times: int
) -> np.ndarray:
    base = np.array(cell_values, dtype=np.float64)
    return np.broadcast_to(base, (n_members, n_times, base.size)).copy()


def _make_basin(geometry: object) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code="test_basin",
        name="Test Basin",
        geometry=geometry,
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


_CT = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))


class TestMeshKnownAnswer:
    def test_known_answer_basin_mean(self) -> None:
        # Cells 0,1 (in basin) carry 10 and 30 → count-weighted mean 20.0.
        mesh = _make_mesh(_broadcast_cells([10.0, 30.0, 100.0, 200.0], 3, 2))
        sid = StationId(uuid.uuid4())
        result = MeshBasinExtractor().extract(
            mesh,
            [_make_config(sid)],
            {sid: _make_basin(_BASIN_TWO_CELLS)},
            _CT,
            "icon_ch2_eps",
        )
        df = result[sid].values
        assert df.height == 3 * 2  # members × valid_times, one param
        assert df["value"].to_list() == [20.0] * 6


class TestMeshOutOfExtentAndNanParity:
    def test_out_of_extent_raises(self) -> None:
        mesh = _make_mesh(_broadcast_cells([10.0, 30.0, 100.0, 200.0], 2, 2))
        sid = StationId(uuid.uuid4())
        # Far outside the mesh domain → nearest cell well beyond the threshold.
        far_basin = _make_basin(box(29.9, 59.9, 30.1, 60.1))
        with pytest.raises(ExtractionError, match="outside grid extent"):
            MeshBasinExtractor().extract(
                mesh, [_make_config(sid)], {sid: far_basin}, _CT, "icon_ch2_eps"
            )

    def test_single_all_nan_member_dropped(self) -> None:
        values = _broadcast_cells([10.0, 30.0, 100.0, 200.0], 3, 2)
        # Member 0 is all-NaN over the basin's cells (0 and 1) → that member
        # is dropped; members 1 and 2 retained.
        values[0, :, 0:2] = np.nan
        mesh = _make_mesh(values)
        sid = StationId(uuid.uuid4())
        result = MeshBasinExtractor().extract(
            mesh,
            [_make_config(sid)],
            {sid: _make_basin(_BASIN_TWO_CELLS)},
            _CT,
            "icon_ch2_eps",
        )
        members = set(result[sid].values["member_id"].to_list())
        assert members == {1, 2}
        assert result[sid].values["value"].to_list() == [20.0] * 4

    def test_all_members_missing_raises(self) -> None:
        values = _broadcast_cells([10.0, 30.0, 100.0, 200.0], 2, 2)
        values[:, :, 0:2] = np.nan  # every member NaN over the basin's cells
        mesh = _make_mesh(values)
        sid = StationId(uuid.uuid4())
        with pytest.raises(ExtractionError, match="outside grid extent"):
            MeshBasinExtractor().extract(
                mesh,
                [_make_config(sid)],
                {sid: _make_basin(_BASIN_TWO_CELLS)},
                _CT,
                "icon_ch2_eps",
            )


class TestMeshOutputSchema:
    def test_output_schema_matches_basin_average_forecast(self) -> None:
        mesh = _make_mesh(_broadcast_cells([10.0, 30.0, 100.0, 200.0], 2, 2))
        sid = StationId(uuid.uuid4())
        result = MeshBasinExtractor().extract(
            mesh,
            [_make_config(sid)],
            {sid: _make_basin(_BASIN_TWO_CELLS)},
            _CT,
            "icon_ch2_eps",
        )
        forecast = result[sid]
        assert isinstance(forecast, BasinAverageForecast)
        assert forecast.nwp_source == "icon_ch2_eps"
        assert forecast.cycle_time == _CT
        assert dict(forecast.values.schema) == {
            "valid_time": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "member_id": pl.Int64,
            "value": pl.Float64,
        }


class TestMeshNearestCellFallback:
    def test_sub_cell_basin_snaps_to_nearest_cell(self) -> None:
        # A basin too small to capture any centroid, but within
        # _MAX_NEAREST_CELL_DEG of cell 0 (6.0, 46.0, value 10) → snaps to it.
        mesh = _make_mesh(_broadcast_cells([10.0, 30.0, 100.0, 200.0], 2, 2))
        sid = StationId(uuid.uuid4())
        tiny_basin = _make_basin(box(6.02, 46.02, 6.03, 46.03))
        result = MeshBasinExtractor().extract(
            mesh, [_make_config(sid)], {sid: tiny_basin}, _CT, "icon_ch2_eps"
        )
        assert result[sid].values["value"].to_list() == [10.0] * 4


_REAL_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent
    / "fixtures"
    / "meteoswiss_nwp"
    / "icon_ch2_eps_202604231200"
)


class TestMeshRealFixtureEndToEnd:
    def test_real_mesh_parse_attach_extract(self, tmp_path: Path) -> None:
        files = sorted(_REAL_FIXTURE_DIR.glob("*.grib2"))
        if not files:
            pytest.skip(f"fixtures not found at {_REAL_FIXTURE_DIR}")
        import httpx

        transport = httpx.MockTransport(lambda _req: httpx.Response(404))
        client = httpx.Client(transport=transport, base_url="https://dummy")
        adapter = MeteoSwissNwpAdapter(
            stac_base_url="https://data.geo.admin.ch/api/stac/v1",
            stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
            scratch_path=tmp_path,
            http_client=client,
        )
        # Feed the raw parsed cube EXACTLY as run_forecast_cycle_flow does:
        # no manual localization. cfgrib yields tz-naive datetime64 valid_time;
        # the extractor must localize it to UTC internally (ICON times are UTC).
        cube = adapter._parse_grib_files(files)

        sid = StationId(uuid.uuid4())
        # A CH basin spanning many mesh cells.
        basin = _make_basin(box(7.0, 46.5, 8.0, 47.5))
        result = MeshBasinExtractor().extract(
            cube, [_make_config(sid)], {sid: basin}, _CT, "icon_ch2_eps"
        )
        assert isinstance(result[sid], BasinAverageForecast)
        df = result[sid].values
        assert df.height > 0
        assert dict(df.schema) == {
            "valid_time": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "member_id": pl.Int64,
            "value": pl.Float64,
        }
        # All 21 ICON-CH2-EPS members survive for a well-covered CH basin.
        assert set(df["member_id"].to_list()) == set(range(21))
        assert set(df["parameter"].to_list()) == {"precipitation", "temperature"}
