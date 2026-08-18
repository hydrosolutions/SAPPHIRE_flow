# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""ERA5-Land forcing from the sloth-dynamic store (Plan 183).

Reads ``s3://sloth-dynamic/v1/era5/`` — the SAME store aquacast's data plane
(``aquaire``) reads — rather than reproducing the training lineage's
acquisition ourselves. The mapping below is transcribed VERBATIM from
``aquaire/src/aquaire/sources/era5.py`` (private; read as specification, see
Plan 183):

    AQUAIRE name           unit    store variable                     native  transform
    precipitation          mm/day  total_precipitation_sum            m       x 1000
    mean_temperature       degC    temperature_2m_mean                K       - 273.15
    solar_net_radiation    W/m2    surface_net_solar_radiation_sum    J/m2    / 86400
    thermal_net_radiation  W/m2    surface_net_thermal_radiation_sum  J/m2    / 86400

Three traps, each of which produces plausible-looking WRONG numbers if
missed:

1. Radiation is a daily ACCUMULATION (``cell_methods: time: sum``, J/m2 over
   the day) — the divisor is 86400 (seconds/day), NEVER 3600 (that is an
   hourly divisor and inflates radiation 24x).
2. Daily aggregation is UTC 00-24 (per the store's own ``history``
   provenance), not assumed from the label.
3. ``thermal_net_radiation`` is downward-positive and therefore legitimately
   NEGATIVE. Nothing here clips or negates it — "fixing" the sign would be
   wrong.

SAP3's canonical dynamic vocabulary is ``{"precipitation", "temperature"}`` —
``mean_temperature`` is NOT a SAP3 parameter. ``_canonical_parameter`` reuses
``AQUACAST_TO_CANONICAL_NAME`` (``models/aquacast/_shim.py``) as the single
source of truth for that rename, rather than re-deriving it — writing
``parameter="mean_temperature"`` would fail SILENTLY (``fetch_forcing``
returns nothing for an unmatched parameter filter rather than raising).

D2 (owner, 2026-08-18): only precipitation and temperature are basin-averaged
and persisted operationally (``fetch_reanalysis``, the ``WeatherReanalysisSource``
protocol method) — radiation ingestion is deferred because T3 validates only
against precipitation/temperature Caravan indices, so shipping radiation now
would ship unvalidated. ``read_variable`` (T1) still supports all four
mapped variables directly, for validation.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, ClassVar, Final

import numpy as np
import pandas as pd
import shapely
import structlog
import xarray as xr

from sapphire_flow.exceptions import AdapterError, ExtractionError
from sapphire_flow.models.aquacast import AQUACAST_TO_CANONICAL_NAME
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import RawHistoricalForcing

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
        ExactExtractGridExtractor,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource

log = structlog.get_logger(__name__)


def _mm_from_m(da: xr.DataArray) -> xr.DataArray:
    return da * 1000.0


def _degc_from_kelvin(da: xr.DataArray) -> xr.DataArray:
    return da - 273.15


def _watts_from_daily_joules(da: xr.DataArray) -> xr.DataArray:
    # Daily ACCUMULATION (J/m^2 over the day) -> mean W/m^2. 86400 = seconds
    # in a day; NEVER 3600 (hourly), which would inflate radiation 24x. Sign
    # is untouched — thermal_net_radiation is downward-positive and
    # legitimately negative.
    return da / 86400.0


class _Era5Variable:
    """One row of the AQUAIRE mapping table (frozen by convention — a plain
    class, not a dataclass, since ``transform`` is a function value and this
    is a fixed, internal, five-entry registry rather than a value users
    construct)."""

    __slots__ = ("aquaire_name", "store_variable", "transform")

    def __init__(
        self,
        *,
        aquaire_name: str,
        store_variable: str,
        transform: Callable[[xr.DataArray], xr.DataArray],
    ) -> None:
        self.aquaire_name = aquaire_name
        self.store_variable = store_variable
        self.transform = transform


_VARIABLE_REGISTRY: Final[tuple[_Era5Variable, ...]] = (
    _Era5Variable(
        aquaire_name="precipitation",
        store_variable="total_precipitation_sum",
        transform=_mm_from_m,
    ),
    _Era5Variable(
        aquaire_name="mean_temperature",
        store_variable="temperature_2m_mean",
        transform=_degc_from_kelvin,
    ),
    _Era5Variable(
        aquaire_name="solar_net_radiation",
        store_variable="surface_net_solar_radiation_sum",
        transform=_watts_from_daily_joules,
    ),
    _Era5Variable(
        aquaire_name="thermal_net_radiation",
        store_variable="surface_net_thermal_radiation_sum",
        transform=_watts_from_daily_joules,
    ),
)

_BY_AQUAIRE_NAME: Final[dict[str, _Era5Variable]] = {
    v.aquaire_name: v for v in _VARIABLE_REGISTRY
}

# D2: the only two AQUAIRE variables this adapter basin-averages/persists.
_OPERATIONAL_AQUAIRE_NAMES: Final[tuple[str, ...]] = (
    "precipitation",
    "mean_temperature",
)


def _canonical_parameter(aquaire_name: str) -> str:
    """SAP3-canonical parameter name for an AQUAIRE variable — reuses
    ``AQUACAST_TO_CANONICAL_NAME`` as the single source of truth (Plan 183)."""
    return AQUACAST_TO_CANONICAL_NAME.get(aquaire_name, aquaire_name)


_CANONICAL_TO_AQUAIRE: Final[dict[str, str]] = {
    _canonical_parameter(name): name for name in _OPERATIONAL_AQUAIRE_NAMES
}


def _default_store_opener(path: str) -> xr.Dataset:
    """Production store opener: read-only zarr v3 over s3fs. Imported lazily
    so unit tests never require ``s3fs`` or network access — every test
    injects ``open_store`` instead (dependency injection, CLAUDE.md
    Testability)."""
    import s3fs  # noqa: PLC0415

    fs = s3fs.S3FileSystem(anon=False)
    mapper = fs.get_mapper(path)
    return xr.open_zarr(mapper)


def _standardize_dims(ds: xr.Dataset) -> xr.Dataset:
    """Normalise the store's dimension names to ``valid_time``/``latitude``/
    ``longitude`` — the shape ``ExactExtractGridExtractor`` and this
    adapter's own time-slicing expect. ERA5-Land is already geographic
    (WGS84), so — unlike the MeteoSwiss LV95 adapter — this is a rename
    only, never a reprojection."""
    renames: dict[str, str] = {}
    if "time" in ds.dims and "valid_time" not in ds.dims:
        renames["time"] = "valid_time"
    if "lat" in ds.dims and "latitude" not in ds.dims:
        renames["lat"] = "latitude"
    if "lon" in ds.dims and "longitude" not in ds.dims:
        renames["lon"] = "longitude"
    return ds.rename(renames) if renames else ds


def _grid_step(coords: np.ndarray) -> float | None:
    """Median spacing of a coordinate axis, or ``None`` for a degenerate
    (<2-point) axis. Grid-agnostic — works for the real store's 0.1 deg. axis
    and any synthetic test grid alike."""
    unique = np.unique(coords)
    if unique.size < 2:
        return None
    return float(np.median(np.diff(unique)))


#: Grid-step multiples of margin applied around a bbox before subsetting.
#: >1 so a raster that would otherwise collapse to a single row/column right
#: at the bbox's edge (exact_extract/rioxarray need >=2 points per axis to
#: infer resolution) keeps enough neighbouring cells on both sides.
_BBOX_MARGIN_GRID_STEPS: Final[int] = 2


def _spatial_subset(
    da: xr.DataArray, bbox: tuple[float, float, float, float]
) -> xr.DataArray:
    """R2-2 fix: bound ``da`` to ``bbox`` (+ a small grid-step margin, so a
    cell whose CENTRE falls just outside the bbox but still OVERLAPS a
    polygon near the edge is retained) before any values materialise.
    Without this, ``read_variable`` sliced only on ``valid_time`` and the
    full global 1801x3600 ERA5-Land grid was read for every chunk of the
    40-year, two-parameter, ~296-basin backfill this plan exists to run —
    reading the whole planet to use a few hundred cells of it."""
    minx, miny, maxx, maxy = bbox
    lon_all = da["longitude"].values
    lat_all = da["latitude"].values
    lon_step = _grid_step(lon_all) or 0.0
    lat_step = _grid_step(lat_all) or 0.0
    lon_margin = _BBOX_MARGIN_GRID_STEPS * lon_step
    lat_margin = _BBOX_MARGIN_GRID_STEPS * lat_step
    lon_idx = np.nonzero(
        (lon_all >= minx - lon_margin) & (lon_all <= maxx + lon_margin)
    )[0]
    lat_idx = np.nonzero(
        (lat_all >= miny - lat_margin) & (lat_all <= maxy + lat_margin)
    )[0]
    if lon_idx.size == 0 or lat_idx.size == 0:
        raise ExtractionError(
            f"ERA5-Land spatial subset bbox {bbox} does not intersect the "
            "store's grid extent"
        )
    return da.isel(longitude=lon_idx, latitude=lat_idx)


def _fleet_bbox(
    configs: list[StationWeatherSource], basins: dict[StationId, Basin]
) -> tuple[float, float, float, float] | None:
    """The union bounding box of every config's basin — ``None`` when no
    config has a basin on record, in which case ``read_variable`` falls back
    to an unsliced (global) read rather than raising."""
    bounds = [
        basins[cfg.station_id].geometry.bounds
        for cfg in configs
        if cfg.station_id in basins
    ]
    if not bounds:
        return None
    return (
        min(b[0] for b in bounds),
        min(b[1] for b in bounds),
        max(b[2] for b in bounds),
        max(b[3] for b in bounds),
    )


def _assert_land_coverage(
    grid: xr.Dataset,
    parameter: str,
    configs: list[StationWeatherSource],
    basins: dict[StationId, Basin],
    min_land_fraction: float,
) -> None:
    """Standalone land-mask coverage check (Plan 183 T2; corrected in the
    fixer round — see M2/M3 below).

    The land-only grid is the trap: ocean cells are NaN, so a coastal or
    partially-masked catchment can silently average over fewer cells than it
    should. ``ExactExtractGridExtractor`` hardcodes ``ops=["mean"]`` and
    returns no contributing-cell count — modifying the SHARED extractor was
    rejected here (it is also used for NWP extraction, and any ``ops``
    change would need to be shown not to alter that output shape, which is
    outside this plan's scope). This checks coverage independently, over the
    first time slice (the land/ocean NaN mask is time-invariant).

    **M2 fix:** coverage is measured over INTERSECTING grid cells (each
    cell's footprint — half a grid-step wide/tall around its centre), not
    cell-CENTRE containment. ``ExactExtractGridExtractor`` runs
    ``exact_extract(..., ops=["mean"])`` (coverage-weighted over intersecting
    cells,`exact_extract_grid_extractor.py:121-134`) and only reports
    out-of-extent when EVERY value is NaN — a basin smaller than one grid
    cell (~11 km at ERA5-Land's 0.1 deg., the ordinary small-Swiss-catchment
    case) still intersects at least one cell and gets a real, coverage-
    weighted, non-NaN mean with zero land verification. The earlier
    centre-containment check had NO cell centre to find inside such a basin,
    so it silently skipped exactly the basins most at risk — the opposite of
    what "the extractor itself raises ExtractionError for this case" (the
    old comment) actually does.

    **M3 fix:** cropped to each basin's bounding box (+ one grid-step
    margin), never the full grid — ``read_variable`` only slices by
    ``valid_time``, so an unsliced 1801x3600 global ERA5-Land grid rebuilt
    per basin, per (year, station-batch, parameter) chunk was O(10^11)
    point-in-polygon predicates before extraction even began. Cropping first
    bounds the per-basin cost by the basin's own extent, not the globe's.

    **R2-1 fix (second fixer round, 2026-08-18):** "contributing cell" is a
    POSITIVE-AREA overlap test (``shapely.area(shapely.intersection(...)) >
    0``), not ``shapely.intersects`` (a boolean TOUCH test). ``intersects``
    is true for a cell that touches the basin only along an edge or at a
    corner — zero overlap area, zero weight in ``exact_extract``'s
    coverage-weighted mean. Counting such a cell toward ``total`` let a NaN
    ocean cell that merely brushes the boundary depress ``fraction`` and
    raise ``ExtractionError`` on a basin that was otherwise entirely fine —
    trading M2's false negative for a false positive on the same metric.

    **R2-2 fix (second fixer round, 2026-08-18):** the ``grid`` this function
    receives is now ALREADY spatially subset to the fleet's bounding box (see
    ``read_variable``'s ``bbox`` parameter, applied in ``fetch_reanalysis``)
    — the M3 crop below is a further, per-basin narrowing within that
    already-small window, not the only thing standing between this function
    and the full 1801x3600 global grid.
    """
    first_time = grid["valid_time"].values[0]
    da0 = grid[parameter].sel(valid_time=first_time).transpose("latitude", "longitude")
    lon_all = da0["longitude"].values
    lat_all = da0["latitude"].values
    values_all = da0.values

    lon_step = _grid_step(lon_all)
    lat_step = _grid_step(lat_all)

    low_coverage: list[str] = []
    for cfg in configs:
        basin = basins.get(cfg.station_id)
        if basin is None or lon_step is None or lat_step is None:
            continue
        geom = basin.geometry
        minx, miny, maxx, maxy = geom.bounds
        lon_idx = np.nonzero(
            (lon_all >= minx - lon_step) & (lon_all <= maxx + lon_step)
        )[0]
        lat_idx = np.nonzero(
            (lat_all >= miny - lat_step) & (lat_all <= maxy + lat_step)
        )[0]
        if lon_idx.size == 0 or lat_idx.size == 0:
            # No cells anywhere near the basin at all: out-of-extent
            # entirely — the extractor itself raises ExtractionError for
            # this case, not this check's job.
            continue

        lon_sub = lon_all[lon_idx]
        lat_sub = lat_all[lat_idx]
        lon_grid, lat_grid = np.meshgrid(lon_sub, lat_sub)
        cells = shapely.box(
            lon_grid - lon_step / 2,
            lat_grid - lat_step / 2,
            lon_grid + lon_step / 2,
            lat_grid + lat_step / 2,
        )
        # R2-1: a POSITIVE-AREA overlap test, not shapely.intersects (which is
        # also true for a cell that merely touches the basin along an edge
        # or at a corner, contributing zero weight to exact_extract's mean).
        overlap_area = shapely.area(shapely.intersection(geom, cells))
        contributing = overlap_area > 0.0
        total = int(contributing.sum())
        if total == 0:
            continue
        cropped_values = values_all[np.ix_(lat_idx, lon_idx)]
        land = int(np.sum(contributing & ~np.isnan(cropped_values)))
        fraction = land / total
        if fraction < min_land_fraction:
            low_coverage.append(
                f"{cfg.station_id}(land_fraction={fraction:.2f}, cells={total})"
            )

    if low_coverage:
        raise ExtractionError(
            f"ERA5-Land land-mask coverage below {min_land_fraction:.0%} for: "
            f"{low_coverage}"
        )


class Era5LandReanalysisAdapter:
    """``WeatherReanalysisSource`` over the sloth-dynamic ERA5-Land daily
    store (Plan 183) — the same store aquacast's data plane (``aquaire``)
    reads."""

    NWP_SOURCE: ClassVar[str] = ForcingSource.ERA5_LAND.value

    # The store's own schema version (the "v1" in
    # ``s3://sloth-dynamic/v1/era5/``) — used as the persisted `version`
    # field. Stable across re-fetches of the same window so re-runs upsert
    # cleanly on the natural key rather than accumulating duplicate,
    # never-reconciled rows under a fresh hash every time (there is no
    # single per-fetch "asset" to content-hash the way the MeteoSwiss NetCDF
    # adapter does).
    _STORE_VERSION: ClassVar[str] = "sloth-dynamic-v1"

    def __init__(
        self,
        *,
        store_root: str = "s3://sloth-dynamic/v1/era5",
        extractor: ExactExtractGridExtractor,
        basins: dict[StationId, Basin],
        clock: Callable[[], UtcDatetime],
        open_store: Callable[[str], xr.Dataset] = _default_store_opener,
        min_land_fraction: float = 0.5,
    ) -> None:
        if not (0.0 < min_land_fraction <= 1.0):
            raise ValueError(
                f"min_land_fraction must be in (0.0, 1.0], got {min_land_fraction}"
            )
        self._store_root = store_root.rstrip("/")
        self._extractor = extractor
        self._basins = basins
        self._clock = clock
        self._open_store = open_store
        self._min_land_fraction = min_land_fraction

    def _variable_path(self, store_variable: str) -> str:
        return f"{self._store_root}/{store_variable}.zarr"

    def read_variable(
        self,
        aquaire_name: str,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> xr.DataArray:
        """T1 — open one variable's zarr read-only, apply the AQUAIRE
        mapping verbatim (unit transform, native -> canonical), sliced to
        the half-open ``[start, end)`` window. Returns a
        ``(valid_time, latitude, longitude)`` DataArray in AQUAIRE's
        canonical units. Supports all four mapped variables — only two are
        operationally persisted, see ``fetch_reanalysis``/D2.

        **R2-2 fix:** when ``bbox`` is given, the DataArray is ALSO spatially
        subset (``_spatial_subset``, + a one-grid-step margin) before
        anything materialises — ``fetch_reanalysis`` passes the fleet's own
        bounding box. Without this, the store's full global 1801x3600 grid
        was read for every chunk of the backfill regardless of how few
        basins were requested (``bbox=None`` — the default, used directly by
        callers like T3 validation that want the whole window — keeps the
        old unsliced-in-space behaviour)."""
        var = _BY_AQUAIRE_NAME.get(aquaire_name)
        if var is None:
            raise AdapterError(f"unknown ERA5-Land variable: {aquaire_name!r}")

        ds = _standardize_dims(
            self._open_store(self._variable_path(var.store_variable))
        )
        if var.store_variable not in ds.data_vars:
            raise AdapterError(
                f"expected variable {var.store_variable!r} absent from "
                f"ERA5-Land store; got {list(ds.data_vars)}"
            )

        start_ts = pd.Timestamp(start).tz_localize(None)
        end_ts = pd.Timestamp(end).tz_localize(None) - pd.Timedelta(microseconds=1)
        da = ds[var.store_variable].sel(valid_time=slice(start_ts, end_ts))
        if bbox is not None:
            da = _spatial_subset(da, bbox)
        return var.transform(da)

    def discover_boundary(self) -> UtcDatetime | None:
        """The store's own published high-water mark — the latest daily
        ``valid_time`` any variable's zarr carries (all four variables share
        one time axis by construction: one store, daily aggregates)."""
        var = _VARIABLE_REGISTRY[0]
        ds = _standardize_dims(
            self._open_store(self._variable_path(var.store_variable))
        )
        times = ds["valid_time"].values
        if times.size == 0:
            return None
        latest = pd.Timestamp(times.max()).to_pydatetime()
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return ensure_utc(latest)  # type: ignore[arg-type]

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        """T2 — basin-average onto SAP3 polygons via the SAME
        ``ExactExtractGridExtractor`` used for MeteoSwiss NWP, restricted to
        {"precipitation", "temperature"} (D2 — radiation deferred)."""
        requested = set(parameters)
        aquaire_names = [
            name
            for canonical, name in _CANONICAL_TO_AQUAIRE.items()
            if canonical in requested
        ]
        if not aquaire_names:
            return []

        matching = self._matching_configs(station_configs)
        if not matching:
            return []

        cycle_time = self._clock()
        # R2-2: bound the read to this fleet's basin extent, not the store's
        # full global grid — see read_variable's ``bbox``/_spatial_subset.
        bbox = _fleet_bbox(matching, self._basins)
        rows: list[RawHistoricalForcing] = []
        for aquaire_name in aquaire_names:
            canonical = _canonical_parameter(aquaire_name)
            da = self.read_variable(aquaire_name, start, end, bbox=bbox)
            grid = da.to_dataset(name=canonical)
            _assert_land_coverage(
                grid, canonical, matching, self._basins, self._min_land_fraction
            )
            extracted = self._extractor.extract(
                grid, matching, self._basins, cycle_time, self.NWP_SOURCE
            )
            for station_id, forecast in extracted.items():
                for record in forecast.values.iter_rows(named=True):
                    rows.append(
                        RawHistoricalForcing(
                            station_id=station_id,
                            source=ForcingSource.ERA5_LAND.value,
                            version=self._STORE_VERSION,
                            valid_time=ensure_utc(record["valid_time"]),
                            parameter=canonical,
                            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                            band_id=None,
                            member_id=None,
                            value=float(record["value"]),
                        )
                    )

        log.info(
            "era5_land_reanalysis.fetch_completed",
            nwp_source=self.NWP_SOURCE,
            start=start.isoformat(),
            end=end.isoformat(),
            row_count=len(rows),
        )
        return rows

    def _matching_configs(
        self, station_configs: list[StationWeatherSource]
    ) -> list[StationWeatherSource]:
        return [
            c
            for c in station_configs
            if c.nwp_source == self.NWP_SOURCE
            and c.role is WeatherSourceRole.REANALYSIS
            and c.status is WeatherSourceStatus.ACTIVE
            and c.extraction_type is SpatialRepresentation.BASIN_AVERAGE
        ]
