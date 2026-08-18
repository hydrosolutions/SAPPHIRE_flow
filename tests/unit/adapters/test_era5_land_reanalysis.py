"""Plan 183 T1/T2 — ERA5-Land sloth-dynamic store adapter acceptance tests.

Red-first (Plan-105-safe): every test guards its import so a missing symbol
fails as a genuine assertion (``pytest.fail``), never a collection-time
``ImportError``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr
from shapely.geometry import box

from sapphire_flow.exceptions import ExtractionError
from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
    ExactExtractGridExtractor,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import StationWeatherSource

_EPOCH = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _import_adapter():
    try:
        from sapphire_flow.adapters.era5_land_reanalysis import (
            Era5LandReanalysisAdapter,
        )
    except ImportError:
        pytest.fail(
            "Era5LandReanalysisAdapter (sapphire_flow.adapters.era5_land_reanalysis) "
            "is not implemented yet — expected read_variable() to open a per-variable "
            "ERA5-Land zarr, apply the AQUAIRE unit mapping verbatim (precip x1000, "
            "temperature -273.15, radiation /86400 with sign untouched), and "
            "fetch_reanalysis() to basin-average precipitation/temperature only."
        )
    return Era5LandReanalysisAdapter


def _raw_dataset(
    *,
    variable: str,
    fill_value: float,
    n_time: int = 3,
    n_lat: int = 4,
    n_lon: int = 4,
    ocean_mask: bool = False,
) -> xr.Dataset:
    """A synthetic per-variable ERA5-Land-shaped dataset: dims (time,
    latitude, longitude), native units — the raw shape ``read_variable``
    must open, slice and transform. ``ocean_mask`` NaNs out half the grid
    (land-only store simulation) for the coverage test."""
    values = np.full((n_time, n_lat, n_lon), fill_value, dtype=np.float64)
    if ocean_mask:
        values[:, :, n_lon // 2 :] = np.nan
    return xr.Dataset(
        {
            variable: xr.DataArray(
                values,
                dims=["time", "latitude", "longitude"],
                coords={
                    # Real ERA5-Land zarr stores carry a tz-NAIVE time axis
                    # (like the MeteoSwiss NetCDFs the sibling adapter
                    # parses) — the synthetic fixture matches that.
                    "time": [datetime(2026, 4, 1 + i) for i in range(n_time)],
                    "latitude": np.linspace(46.0, 48.0, n_lat),
                    "longitude": np.linspace(6.0, 10.0, n_lon),
                },
            )
        }
    )


def _make_basin(station_id: StationId, *, geom: object | None = None) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code="test_basin",
        name="Test Basin",
        geometry=geom if geom is not None else box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network="test",
    )


def _make_config(
    station_id: StationId, *, nwp_source: str = "era5_land_sloth_dynamic"
) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source=nwp_source,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


class TestReadVariableMapping:
    """T1 acceptance: a known basin's daily series comes back in canonical
    units with a plausible range — and specifically that
    ``thermal_net_radiation`` is negative, catching a sign "correction" and
    a units error (wrong divisor) at once."""

    def test_precipitation_converts_metres_to_millimetres(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        ds = _raw_dataset(variable="total_precipitation_sum", fill_value=0.004)
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
        )

        da = adapter.read_variable(
            "precipitation",
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
        )

        assert float(da.values.max()) == pytest.approx(4.0)  # 0.004 m -> 4 mm

    def test_mean_temperature_converts_kelvin_to_celsius(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        ds = _raw_dataset(variable="temperature_2m_mean", fill_value=283.15)
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
        )

        da = adapter.read_variable(
            "mean_temperature",
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
        )

        assert float(da.values.max()) == pytest.approx(10.0)  # 283.15 K -> 10 degC

    def test_thermal_net_radiation_stays_negative_with_correct_divisor(self) -> None:
        """The core trap: raw daily accumulation is negative (downward-
        positive convention -> net loss). Dividing by 86400 (not 3600) must
        preserve the sign AND land in a plausible W/m^2 range; dividing by
        3600 would inflate the magnitude 24x past any plausible range."""
        era5_land_adapter_cls = _import_adapter()
        raw_j_per_m2 = -2_000_000.0  # a plausible daily net-loss accumulation
        ds = _raw_dataset(
            variable="surface_net_thermal_radiation_sum", fill_value=raw_j_per_m2
        )
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
        )

        da = adapter.read_variable(
            "thermal_net_radiation",
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
        )
        value = float(da.values.max())

        assert value < 0.0, (
            "thermal_net_radiation must stay negative — never sign-corrected"
        )
        # 86400 divisor: -2_000_000 / 86400 ~= -23.1 W/m^2 (plausible).
        # A 3600 divisor would instead give ~-555 W/m^2 (implausible, 24x too big).
        assert -100.0 < value < 0.0, (
            f"got {value} W/m^2 — outside plausible range; a 3600 divisor "
            "(hourly, not daily) would inflate the magnitude 24x"
        )

    def test_solar_net_radiation_uses_86400_not_3600_divisor(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        raw_j_per_m2 = 8_640_000.0  # exactly 100 W/m^2 if divided by 86400
        ds = _raw_dataset(
            variable="surface_net_solar_radiation_sum", fill_value=raw_j_per_m2
        )
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
        )

        da = adapter.read_variable(
            "solar_net_radiation",
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
        )

        assert float(da.values.max()) == pytest.approx(100.0)

    def test_unknown_variable_raises(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=lambda: _EPOCH,
            open_store=lambda path: _raw_dataset(
                variable="total_precipitation_sum", fill_value=0.0
            ),
        )

        with pytest.raises(Exception, match="unknown ERA5-Land variable"):
            adapter.read_variable(
                "wind_speed",
                ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 2, tzinfo=UTC)),
            )


class TestFetchReanalysisWritesCanonicalParameterNames:
    """T2 acceptance: writes MUST use SAP3-canonical parameter names
    (reusing ``AQUACAST_TO_CANONICAL_NAME``), never AQUAIRE's own
    ``mean_temperature`` — that would fail silently downstream."""

    def test_temperature_rows_use_canonical_name_not_mean_temperature(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        basin = _make_basin(sid)
        config = _make_config(sid)

        def _open(path: str) -> xr.Dataset:
            if "temperature_2m_mean" in path:
                return _raw_dataset(variable="temperature_2m_mean", fill_value=283.15)
            return _raw_dataset(variable="total_precipitation_sum", fill_value=0.004)

        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=_open,
        )

        rows = adapter.fetch_reanalysis(
            [config],
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
            ["temperature"],
        )

        assert rows, "expected temperature rows"
        parameters = {r.parameter for r in rows}
        assert parameters == {"temperature"}
        assert "mean_temperature" not in parameters

    def test_rows_tagged_with_era5_land_source(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        basin = _make_basin(sid)
        config = _make_config(sid)
        ds = _raw_dataset(variable="total_precipitation_sum", fill_value=0.004)
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
        )

        rows = adapter.fetch_reanalysis(
            [config],
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
            ["precipitation"],
        )

        assert rows
        assert all(r.source == ForcingSource.ERA5_LAND.value for r in rows)
        assert all(r.member_id is None for r in rows)
        assert all(r.spatial_type is SpatialRepresentation.BASIN_AVERAGE for r in rows)

    def test_radiation_parameter_not_requestable_operationally(self) -> None:
        """D2: radiation is deferred — requesting it yields no rows, not an
        exception (mirrors the MeteoSwiss adapter's "unsupported parameter
        -> empty" convention)."""
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        basin = _make_basin(sid)
        config = _make_config(sid)
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: _raw_dataset(
                variable="surface_net_solar_radiation_sum", fill_value=1.0
            ),
        )

        rows = adapter.fetch_reanalysis(
            [config],
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
            ["solar_net_radiation"],
        )

        assert rows == []

    def test_only_matching_nwp_source_configs_are_extracted(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        basin = _make_basin(sid)
        config = _make_config(sid, nwp_source="meteoswiss_open_data_reanalysis")
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: _raw_dataset(
                variable="total_precipitation_sum", fill_value=0.004
            ),
        )

        rows = adapter.fetch_reanalysis(
            [config],
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
            ["precipitation"],
        )

        assert rows == []


class TestLandCoverageCheck:
    """T2: a coastal/partially-masked basin averaging over too few land
    cells must be caught, not silently averaged over fewer cells than the
    polygon suggests."""

    def test_raises_when_basin_mostly_ocean(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        # Basin covers the full grid box; the synthetic grid NaNs out the
        # eastern half (ocean_mask=True) — well below the default 50% floor.
        basin = _make_basin(sid, geom=box(6.0, 46.0, 10.0, 48.0))
        config = _make_config(sid)
        ds = _raw_dataset(
            variable="total_precipitation_sum", fill_value=0.004, ocean_mask=True
        )
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
            min_land_fraction=0.9,
        )

        with pytest.raises(ExtractionError, match="land-mask coverage"):
            adapter.fetch_reanalysis(
                [config],
                ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
                ["precipitation"],
            )

    def test_raises_for_sub_cell_basin_with_no_contained_cell_centre(self) -> None:
        """M2 regression: a basin SMALLER than one grid cell — the ordinary
        Swiss-catchment case at ERA5-Land's ~11 km resolution — contains no
        cell CENTRE at all, so a centre-containment check would silently
        skip it (``total == 0``). Straddling one land and one ocean cell,
        ``exact_extract`` itself returns a PLAUSIBLE, non-NaN, land-only mean
        (it excludes the NaN cell from its coverage-weighted average rather
        than propagating NaN) — so the extractor's own out-of-extent guard
        does NOT catch this case either; only an intersecting-cells coverage
        check does."""
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        # A 4x4 grid, 1 degree spacing, well clear of the raster edges. The
        # two eastern columns (lon=7,8) are NaN (ocean); the two western
        # columns (lon=5,6) are land.
        values = np.full((1, 4, 4), 0.004, dtype=np.float64)
        values[:, :, 2:] = np.nan
        ds = xr.Dataset(
            {
                "total_precipitation_sum": xr.DataArray(
                    values,
                    dims=["time", "latitude", "longitude"],
                    coords={
                        "time": [datetime(2026, 4, 1)],
                        "latitude": [45.0, 46.0, 47.0, 48.0],
                        "longitude": [5.0, 6.0, 7.0, 8.0],
                    },
                )
            }
        )
        # Straddles the land/ocean boundary at lon=6.5, contained in neither
        # cell's centre (6.0 nor 7.0) and containing no grid point at all —
        # exactextract will still return a real land-only mean for it.
        basin = _make_basin(sid, geom=box(6.3, 46.2, 6.9, 46.8))
        config = _make_config(sid)
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
            min_land_fraction=0.9,
        )

        with pytest.raises(ExtractionError, match="land-mask coverage"):
            adapter.fetch_reanalysis(
                [config],
                ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
                ["precipitation"],
            )

    def test_sub_cell_basin_fully_inside_a_land_cell_passes(self) -> None:
        """Same sub-cell shape, but positioned entirely inside the LAND
        cell — no cell centre inside it either, but coverage should read
        100% land, not skip the check."""
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        values = np.array([[[0.004, np.nan], [0.004, np.nan]]], dtype=np.float64)
        ds = xr.Dataset(
            {
                "total_precipitation_sum": xr.DataArray(
                    values,
                    dims=["time", "latitude", "longitude"],
                    coords={
                        "time": [datetime(2026, 4, 1)],
                        "latitude": [46.0, 47.0],
                        "longitude": [6.0, 7.0],
                    },
                )
            }
        )
        # Entirely inside the land cell [5.5,6.5]x[45.5,46.5].
        basin = _make_basin(sid, geom=box(6.1, 45.6, 6.4, 45.9))
        config = _make_config(sid)
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
            min_land_fraction=0.9,
        )

        rows = adapter.fetch_reanalysis(
            [config],
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
            ["precipitation"],
        )

        assert rows

    def test_passes_when_basin_fully_land(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        sid = StationId(uuid.uuid4())
        basin = _make_basin(sid, geom=box(6.0, 46.0, 10.0, 48.0))
        config = _make_config(sid)
        ds = _raw_dataset(
            variable="total_precipitation_sum", fill_value=0.004, ocean_mask=False
        )
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={sid: basin},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
            min_land_fraction=0.9,
        )

        rows = adapter.fetch_reanalysis(
            [config],
            ensure_utc(datetime(2026, 4, 1, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 4, tzinfo=UTC)),
            ["precipitation"],
        )

        assert rows


class TestDiscoverBoundary:
    def test_returns_latest_time_in_store(self) -> None:
        era5_land_adapter_cls = _import_adapter()
        ds = _raw_dataset(
            variable="total_precipitation_sum", fill_value=0.004, n_time=5
        )
        adapter = era5_land_adapter_cls(
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=lambda: _EPOCH,
            open_store=lambda path: ds,
        )

        boundary = adapter.discover_boundary()

        assert boundary == ensure_utc(datetime(2026, 4, 5, tzinfo=UTC))
