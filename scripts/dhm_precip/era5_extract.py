"""Plan 174 (M-A5) — point extraction of the M-A4 ERA5-Land hourly-mm
product at the 26 gauge locations.

Phase 2 (2b/2c/2d): axis/registration/source-integrity validators (D2, D5.1,
D7), the two named operators (D1, D11), and station-set validation (D8).
Phase 3 (3a/3b): the per-station grid+elevation table and the
operator-sensitivity envelope (D1a). No gauge value and no QC mask are ever
read here (D4) — this module produces a complete, unmasked ERA5 series per
station.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import structlog

from sapphire_flow.types.datetime import UtcDatetime
from scripts.dhm_precip.domain_types import (
    DatumReconciliationStatus,
    ExtractionOperator,
    OrographySource,
    SensitivityDeltaUnit,
    SensitivityScope,
    SensitivityStatistic,
    Station,
    StationCoordinate,
    VerticalDatum,
)
from scripts.dhm_precip.era5_deaccumulate import (
    ACCUMULATION_RULE_ID,
    OUTPUT_SCHEMA_VERSION,
    validate_output_schema,
)
from scripts.dhm_precip.era5_errors import (
    Era5OrographyError,
    Era5SchemaValidationError,
    ExtractionPostConditionError,
    NonFiniteExtractionError,
    SourceChecksumMismatchError,
    StationOutsideGridError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_manifest import checksum_file
from scripts.dhm_precip.era5_request import GRID_SPACING_DEG
from scripts.dhm_precip.loader import SchemaMismatchError, load_station_coordinates
from scripts.dhm_precip.seasons import Season, season_for

if TYPE_CHECKING:
    from pathlib import Path

    import xarray as xr

    from scripts.dhm_precip.domain_types import StationCoordinateTable
    from scripts.dhm_precip.params import DhmPrecipParams

log = structlog.get_logger(__name__)

# D9/era5_transform.py:316 — the exact literal M-A4 writes. Copied (not
# imported) so this module never depends on Plan 171's transform internals
# beyond its public D9 contract (out of scope: changing 171's semantics).
_EXPECTED_PERIOD_ENDING_CONVENTION = "hour t covers t-1 -> t (UTC)"
_REGISTRATION_TOLERANCE_DEG = 1e-6
_EARTH_RADIUS_KM = 6371.0088  # WGS84 spherical radius (plan "Measured facts")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# --- 2b: axis, registration, source-integrity (D2, D5.1, D7) ---


def assert_source_checksum(path: Path, *, expected_sha256: str) -> None:
    """D7/D9/m-2 — checked BEFORE the payload is decoded: `checksum_file`
    reads raw bytes only, never opens `path` as a NetCDF/HDF5 file."""
    actual = checksum_file(path)
    if actual != expected_sha256:
        raise SourceChecksumMismatchError(
            f"source product at {path} has sha256 {actual}, expected "
            f"{expected_sha256} per the acquisition manifest"
        )


def assert_registration(lat: np.ndarray, lon: np.ndarray) -> None:
    """D2 — whole-0.1 deg cell-centre registration, asserted against the
    product's OWN coordinate vector (constraint 3) rather than assumed."""
    for name, axis in (("latitude", lat), ("longitude", lon)):
        residual = np.abs(axis / GRID_SPACING_DEG - np.round(axis / GRID_SPACING_DEG))
        offenders = residual * GRID_SPACING_DEG
        if bool((offenders > _REGISTRATION_TOLERANCE_DEG).any()):
            raise ExtractionPostConditionError(
                f"{name} coordinate vector is not whole-{GRID_SPACING_DEG} deg "
                f"cell-centre registered (D2): max offset "
                f"{float(offenders.max())} deg exceeds tolerance "
                f"{_REGISTRATION_TOLERANCE_DEG} deg"
            )


def assert_required_attrs_literal(ds: xr.Dataset) -> None:
    """D5.1 — the declared attrs must equal the EXACT expected literals, not
    merely be present/non-blank (which `validate_output_schema` already
    checks)."""
    expected = {
        "period_ending_convention": _EXPECTED_PERIOD_ENDING_CONVENTION,
        "accumulation_rule": ACCUMULATION_RULE_ID,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
    }
    for attr, expected_value in expected.items():
        actual = ds.attrs.get(attr)
        if actual != expected_value:
            raise ExtractionPostConditionError(
                f"attr {attr!r} = {actual!r}, expected exactly {expected_value!r}"
            )


def assert_utc_epoch_encoding(ds: xr.Dataset) -> None:
    """D5.0 — CF-encoded, naive-denoting-UTC: an integer UTC-epoch encoding
    on `valid_time`, checked on a REOPENED dataset (`.encoding` is only
    populated by xarray at open time). There is no "naive axis raises" test
    (D5.0) — a tz-aware dtype is unwritable by the pinned encoder, so this
    checks the epoch/semantic markers a real M-A4 product always carries,
    never the (always-naive) dtype itself."""
    enc = ds["valid_time"].encoding
    units = str(enc.get("units", ""))
    if not units.startswith("hours since 1970-01-01"):
        raise ExtractionPostConditionError(
            f"'valid_time' on-disk units {units!r} do not denote a UTC epoch (D5.0)"
        )
    if str(enc.get("dtype", "")) not in ("int64", "int32"):
        raise ExtractionPostConditionError(
            f"'valid_time' on-disk dtype {enc.get('dtype')!r} is not an "
            "integer CF time encoding (D5.0)"
        )


def assert_extraction_source_valid(
    ds: xr.Dataset,
    *,
    expected_year: int,
    expected_area: tuple[float, float, float, float],
) -> None:
    """2b's full mechanical validation (D5.1): delegates the axis/dims/dtype/
    grid-shape/attrs-presence checks to M-A4's own hardened
    `validate_output_schema` (this module never re-implements them), wrapped
    into this plan's own error hierarchy, plus the two extraction-specific
    checks `validate_output_schema` does not perform: exact-literal attrs
    and cell-centre registration. Call `assert_utc_epoch_encoding` and
    `assert_source_checksum` separately (the former needs a REOPENED
    dataset; the latter must run BEFORE `ds` is even opened).

    `expected_area` is intentionally float-permissive (D2's registration
    arithmetic is exact on floats; only test fixtures use a sub-degree
    sub-box) — `validate_output_schema`'s own parameter is narrower
    (`tuple[int, int, int, int]`, matching its `StudyArea` caller) purely
    because Plan 171's real box happens to be whole degrees, not because
    the underlying arithmetic requires it; out of scope to widen that
    signature here (constraint: no Plan 171 semantics change)."""
    try:
        validate_output_schema(
            ds,
            expected_year=expected_year,
            expected_area=expected_area,  # pyright: ignore[reportArgumentType]
        )
    except Era5SchemaValidationError as exc:
        raise ExtractionPostConditionError(str(exc)) from exc
    assert_registration(ds["latitude"].values, ds["longitude"].values)
    assert_required_attrs_literal(ds)


# --- 2c: the two operators (D1, D11) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractedSeries:
    station: Station
    operator: ExtractionOperator
    valid_time: np.ndarray
    values: np.ndarray
    """mm, one value per `valid_time` stamp. May contain NaN (D11.2/D11.3);
    the caller decides whether that is fatal."""
    grid_lat: float
    grid_lon: float
    grid_i: int
    grid_j: int
    n_finite: int
    n_nan: int


def assert_station_within_grid(
    coord: StationCoordinate, *, lat: np.ndarray, lon: np.ndarray
) -> None:
    """D11.1 — raised BEFORE any extraction. `.sel(method="nearest")` snaps
    an out-of-range target to the boundary and returns a real number; that
    silent relocation must never happen, so this checks bounds independently
    first. Half-spacing allowance per D2's registration."""
    half = GRID_SPACING_DEG / 2.0
    lat_lo, lat_hi = float(lat.min()) - half, float(lat.max()) + half
    lon_lo, lon_hi = float(lon.min()) - half, float(lon.max()) + half
    if not (lat_lo <= coord.lat <= lat_hi) or not (lon_lo <= coord.lon <= lon_hi):
        raise StationOutsideGridError(
            f"station {coord.station!r} at (lat={coord.lat}, lon={coord.lon}) "
            f"lies outside the product grid bounds lat=[{lat_lo}, {lat_hi}] "
            f"lon=[{lon_lo}, {lon_hi}] (D11.1)"
        )


def extract_nearest_series(ds: xr.Dataset, coord: StationCoordinate) -> ExtractedSeries:
    """D1 — THE operator. Bounds-checked first (D11.1), then a plain nearest
    lookup on coordinates already proven in range."""
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    assert_station_within_grid(coord, lat=lat, lon=lon)
    picked = ds["precipitation"].sel(
        latitude=coord.lat, longitude=coord.lon, method="nearest"
    )
    values = np.asarray(picked.values, dtype=np.float64)
    n_nan = int(np.isnan(values).sum())
    grid_lat = float(picked["latitude"].values)
    grid_lon = float(picked["longitude"].values)
    return ExtractedSeries(
        station=coord.station,
        operator=ExtractionOperator.NEAREST,
        valid_time=ds["valid_time"].values,
        values=values,
        grid_lat=grid_lat,
        grid_lon=grid_lon,
        grid_i=int(np.argmin(np.abs(lat - grid_lat))),
        grid_j=int(np.argmin(np.abs(lon - grid_lon))),
        n_finite=values.size - n_nan,
        n_nan=n_nan,
    )


def extract_bilinear_series(
    ds: xr.Dataset, coord: StationCoordinate
) -> ExtractedSeries:
    """D1a's sensitivity comparand only — never a primary. Bounds-checked
    identically to nearest; a missing (NaN) contributing neighbour
    propagates to NaN for that hour (D11.3), counted, never silently
    substituted."""
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    assert_station_within_grid(coord, lat=lat, lon=lon)
    interpolated = ds["precipitation"].interp(
        latitude=coord.lat,
        longitude=coord.lon,
        method="linear",
        kwargs={"fill_value": np.nan, "bounds_error": False},
    )
    values = np.asarray(interpolated.values, dtype=np.float64)
    n_nan = int(np.isnan(values).sum())
    nearest_i = int(np.argmin(np.abs(lat - coord.lat)))
    nearest_j = int(np.argmin(np.abs(lon - coord.lon)))
    return ExtractedSeries(
        station=coord.station,
        operator=ExtractionOperator.BILINEAR,
        valid_time=ds["valid_time"].values,
        values=values,
        grid_lat=coord.lat,
        grid_lon=coord.lon,
        grid_i=nearest_i,
        grid_j=nearest_j,
        n_finite=values.size - n_nan,
        n_nan=n_nan,
    )


def assert_no_missing_primary(result: ExtractedSeries) -> None:
    """D11.2 — any NaN at any station is a typed failure for the PRIMARY
    (nearest) series. Never applied to bilinear (D11.3's own, different,
    counted-not-fatal policy)."""
    if result.n_nan > 0:
        raise NonFiniteExtractionError(
            f"station {result.station!r} nearest series has {result.n_nan} "
            f"non-finite hour(s) out of {result.n_finite + result.n_nan} — "
            "ERA5-Land over this box should be complete (D11.2)"
        )


# --- 2d: station set, cardinality, uniqueness (D8) ---


def load_expected_station_coordinates(
    coords_path: Path, *, expected_stations: frozenset[Station]
) -> StationCoordinateTable:
    """D8 — thin wrapper: the loader already validates exact station-set
    equality, no duplicates and one row per station (`loader.py:251,301`,
    M-13). This module only translates its `SchemaMismatchError` into the
    M-A5 error hierarchy so a single CLI exit-code table covers both."""
    try:
        return load_station_coordinates(
            coords_path, expected_stations=expected_stations
        )
    except SchemaMismatchError as exc:
        raise StationSetMismatchError(str(exc)) from exc


def assert_expected_station_cardinality(
    expected_stations: frozenset[Station], *, expected_count: int
) -> None:
    """D8/2d (CORRECTED 2026-08-16, blocker) — pin the CARDINALITY
    independently of the inventory, BEFORE any extraction.

    2d admitted the workbook-derived usable-station inventory as this plan's
    single boundary input, which removed the only thing pinning the count:
    the loader checks the extracted set *equals* the inventory, and an
    inventory of 25 satisfies that perfectly. A workbook that silently
    yields 25 stations would extract 25 and publish happily, because the
    publication check compares against `len(stations)` — i.e. against
    itself. Equality to a self-supplied list is not a constraint.

    `expected_count` is deliberately a hard-coded number on the frozen
    parameter object (`DhmPrecipParams.expected_station_count = 26`) and NOT
    derived: it is a tripwire on the boundary input, and deriving it from
    the same source it guards would defeat it. If the delivery legitimately
    changes size, the number is updated in one place, as a visible decision.
    """
    actual = len(expected_stations)
    if actual != expected_count:
        raise StationSetMismatchError(
            f"the workbook-derived usable-station inventory has {actual} "
            f"stations, but Plan 174 D8/2d pins expected_station_count="
            f"{expected_count}; refusing to extract (a self-supplied "
            "inventory cannot validate its own size)"
        )


# --- 3a: per-station grid + elevation table ---


def _to_utc_datetime(ts: np.datetime64) -> UtcDatetime:
    return UtcDatetime(ts.astype("datetime64[s]").item().replace(tzinfo=UTC))


@dataclass(frozen=True, kw_only=True, slots=True)
class StationGridElevationRow:
    station: Station
    lat: float
    lon: float
    grid_lat: float
    grid_lon: float
    grid_i: int
    grid_j: int
    offset_km: float
    station_elev_m: float
    station_elevation_datum: VerticalDatum
    orography_elev_m: float
    orography_elevation_datum: VerticalDatum
    orography_source: OrographySource
    orography_product_id: str
    orography_product_version: str
    elev_mismatch_m: float
    datum_reconciled: DatumReconciliationStatus
    shared_cell_id: str
    stations_in_cell: int


def build_station_grid_elevation_table(
    stations: StationCoordinateTable,
    *,
    precip_lat: np.ndarray,
    precip_lon: np.ndarray,
    orography_ds: xr.Dataset,
    orography_source: OrographySource,
    orography_product_id: str,
    orography_product_version: str,
    orography_vertical_reference: VerticalDatum,
) -> tuple[StationGridElevationRow, ...]:
    """3a — every column D9 specifies for `station_grid_elevation.csv`. No
    gauge value; no interpretation of the mismatch (that is M-A6's job).
    Per-station orography FINITENESS is checked HERE (D3a: moved out of 1b,
    which validates only the aggregated grid)."""
    orography_lat = orography_ds["latitude"].values
    orography_lon = orography_ds["longitude"].values
    nearest_index: dict[Station, tuple[int, int]] = {}
    rows: list[StationGridElevationRow] = []
    for station, coord in stations.by_station.items():
        assert_station_within_grid(coord, lat=precip_lat, lon=precip_lon)
        grid_i = int(np.argmin(np.abs(precip_lat - coord.lat)))
        grid_j = int(np.argmin(np.abs(precip_lon - coord.lon)))
        grid_lat = float(precip_lat[grid_i])
        grid_lon = float(precip_lon[grid_j])
        nearest_index[station] = (grid_i, grid_j)

        oro_i = int(np.argmin(np.abs(orography_lat - grid_lat)))
        oro_j = int(np.argmin(np.abs(orography_lon - grid_lon)))
        orography_elev_m = float(orography_ds["orography_elev_m"].values[oro_i, oro_j])
        if not math.isfinite(orography_elev_m):
            raise Era5OrographyError(
                f"station {station!r}'s orography cell (grid_i={oro_i}, "
                f"grid_j={oro_j}) is non-finite — per-station orography "
                "finiteness is required (D3a, moved from 1b to 3a)"
            )

        station_elev_m = coord.elev_m
        rows.append(
            StationGridElevationRow(
                station=station,
                lat=coord.lat,
                lon=coord.lon,
                grid_lat=grid_lat,
                grid_lon=grid_lon,
                grid_i=grid_i,
                grid_j=grid_j,
                offset_km=_haversine_km(coord.lat, coord.lon, grid_lat, grid_lon),
                station_elev_m=station_elev_m,
                station_elevation_datum=VerticalDatum.UNKNOWN,
                orography_elev_m=orography_elev_m,
                orography_elevation_datum=orography_vertical_reference,
                orography_source=orography_source,
                orography_product_id=orography_product_id,
                orography_product_version=orography_product_version,
                elev_mismatch_m=station_elev_m - orography_elev_m,
                datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
                shared_cell_id=f"{grid_i}_{grid_j}",
                stations_in_cell=0,  # filled in below, second pass
            )
        )

    cell_counts: dict[tuple[int, int], int] = {}
    for grid_i, grid_j in nearest_index.values():
        cell_counts[(grid_i, grid_j)] = cell_counts.get((grid_i, grid_j), 0) + 1
    return tuple(
        replace(row, stations_in_cell=cell_counts[(row.grid_i, row.grid_j)])
        for row in rows
    )


# --- 3b: operator-sensitivity envelope (D1a) ---


def _season_series(valid_time: np.ndarray, params: DhmPrecipParams) -> list[Season]:
    return [season_for(_to_utc_datetime(ts), params) for ts in valid_time]


def build_operator_sensitivity_table(
    nearest_by_station: dict[Station, ExtractedSeries],
    bilinear_by_station: dict[Station, ExtractedSeries],
    *,
    params: DhmPrecipParams,
) -> pl.DataFrame:
    """D1a — the pinned population/seasons/thresholds/quantile grid, per
    station and summarised across stations. NEVER a ranking; NEVER phrased
    as a veto. Hours excluded because bilinear is NaN under D11.3 are
    excluded from BOTH operators' statistics (D1a's own rule) and counted."""
    frames: list[pl.DataFrame] = []
    for station, nearest in nearest_by_station.items():
        bilinear = bilinear_by_station[station]
        seasons = [str(s) for s in _season_series(nearest.valid_time, params)]
        frames.append(
            pl.DataFrame(
                {
                    "station": [str(station)] * nearest.values.size,
                    "season": seasons,
                    "nearest_mm_h": nearest.values,
                    "bilinear_mm_h": bilinear.values,
                }
            )
        )
    long = (
        pl.concat(frames)
        if frames
        else pl.DataFrame(
            schema={
                "station": pl.Utf8,
                "season": pl.Utf8,
                "nearest_mm_h": pl.Float64,
                "bilinear_mm_h": pl.Float64,
            }
        )
    )
    # D11.3: hours excluded from BOTH operators wherever EITHER is NaN.
    common_finite = long.filter(
        pl.col("nearest_mm_h").is_not_null()
        & pl.col("bilinear_mm_h").is_not_null()
        & pl.col("nearest_mm_h").is_finite()
        & pl.col("bilinear_mm_h").is_finite()
    )
    excluded = long.height - common_finite.height

    threshold = pl.lit(params.wet_threshold_mm_per_h)

    def _wet(col: str) -> pl.Expr:
        return (
            (pl.col(col) >= threshold)
            if params.wet_threshold_side == ">="
            else (pl.col(col) > threshold)
        )

    rows: list[dict[str, object]] = []

    def _add_quantile_rows(
        frame: pl.DataFrame, *, scope: str, station: str | None, season: str
    ) -> None:
        for q in params.quantile_grid:
            nearest_q = frame.select(
                pl.col("nearest_mm_h").quantile(q, interpolation="linear")
            ).item()
            bilinear_q = frame.select(
                pl.col("bilinear_mm_h").quantile(q, interpolation="linear")
            ).item()
            delta = None
            if nearest_q is not None and bilinear_q is not None:
                delta = nearest_q - bilinear_q
            ratio = (
                nearest_q / bilinear_q
                if (bilinear_q not in (None, 0.0) and nearest_q is not None)
                else None
            )
            rows.append(
                {
                    "scope": scope,
                    "station": station,
                    "season": season,
                    "statistic": SensitivityStatistic.QUANTILE,
                    "quantile": q,
                    "nearest_value": nearest_q,
                    "bilinear_value": bilinear_q,
                    "delta_absolute": delta,
                    "delta_unit": SensitivityDeltaUnit.MM_PER_H,
                    "ratio": ratio,
                    "n_hours_common_finite": frame.height,
                    "n_hours_excluded": excluded,
                    "n_wet_nearest": frame.filter(_wet("nearest_mm_h")).height,
                    "n_wet_bilinear": frame.filter(_wet("bilinear_mm_h")).height,
                    "sign_agreement_fraction": None,
                }
            )

    def _add_wet_rows(
        frame: pl.DataFrame, *, scope: str, station: str | None, season: str
    ) -> None:
        nearest_wet = frame.filter(_wet("nearest_mm_h"))
        bilinear_wet = frame.filter(_wet("bilinear_mm_h"))
        nearest_mean = (
            nearest_wet.select(pl.col("nearest_mm_h").mean()).item()
            if nearest_wet.height
            else None
        )
        bilinear_mean = (
            bilinear_wet.select(pl.col("bilinear_mm_h").mean()).item()
            if bilinear_wet.height
            else None
        )
        delta_mean = (
            nearest_mean - bilinear_mean
            if nearest_mean is not None and bilinear_mean is not None
            else None
        )
        ratio_mean = (
            nearest_mean / bilinear_mean
            if bilinear_mean not in (None, 0.0) and nearest_mean is not None
            else None
        )
        rows.append(
            {
                "scope": scope,
                "station": station,
                "season": season,
                "statistic": SensitivityStatistic.WET_MEAN_INTENSITY,
                "quantile": None,
                "nearest_value": nearest_mean,
                "bilinear_value": bilinear_mean,
                "delta_absolute": delta_mean,
                "delta_unit": SensitivityDeltaUnit.MM_PER_H,
                "ratio": ratio_mean,
                "n_hours_common_finite": frame.height,
                "n_hours_excluded": excluded,
                "n_wet_nearest": nearest_wet.height,
                "n_wet_bilinear": bilinear_wet.height,
                "sign_agreement_fraction": None,
            }
        )
        nearest_freq = nearest_wet.height / frame.height if frame.height else None
        bilinear_freq = bilinear_wet.height / frame.height if frame.height else None
        delta_freq = (
            nearest_freq - bilinear_freq
            if nearest_freq is not None and bilinear_freq is not None
            else None
        )
        ratio_freq = (
            nearest_freq / bilinear_freq
            if (bilinear_freq not in (None, 0.0) and nearest_freq is not None)
            else None
        )
        rows.append(
            {
                "scope": scope,
                "station": station,
                "season": season,
                "statistic": SensitivityStatistic.WET_FREQUENCY,
                "quantile": None,
                "nearest_value": nearest_freq,
                "bilinear_value": bilinear_freq,
                "delta_absolute": delta_freq,
                "delta_unit": SensitivityDeltaUnit.FRACTION,
                "ratio": ratio_freq,
                "n_hours_common_finite": frame.height,
                "n_hours_excluded": excluded,
                "n_wet_nearest": nearest_wet.height,
                "n_wet_bilinear": bilinear_wet.height,
                "sign_agreement_fraction": None,
            }
        )

    # Derived from the FULL input (never from `common_finite`, which can be
    # empty when every hour is excluded — D1a still reports that case, with
    # `n_hours_common_finite == 0` and the full excluded count, rather than
    # silently emitting no row at all).
    seasons_present = ["ALL", "JJAS", *sorted({s for s in long["season"]})]
    seasons_present = list(dict.fromkeys(seasons_present))  # de-dup, keep order
    stations_present = sorted({str(s) for s in nearest_by_station})

    for station in stations_present:
        per_station = common_finite.filter(pl.col("station") == station)
        for season in seasons_present:
            frame = (
                per_station
                if season == "ALL"
                else per_station.filter(pl.col("season") == season)
            )
            _add_quantile_rows(
                frame, scope=SensitivityScope.STATION, station=station, season=season
            )
            _add_wet_rows(
                frame, scope=SensitivityScope.STATION, station=station, season=season
            )

    # ACROSS_STATION rows: same statistics pooled over all stations, plus
    # the sign-agreement fraction (station is null on these rows).
    for season in seasons_present:
        frame = (
            common_finite
            if season == "ALL"
            else common_finite.filter(pl.col("season") == season)
        )
        _add_quantile_rows(
            frame, scope=SensitivityScope.ACROSS_STATION, station=None, season=season
        )
        _add_wet_rows(
            frame, scope=SensitivityScope.ACROSS_STATION, station=None, season=season
        )

        # Sign agreement: per-station sign of (nearest_median - bilinear_median)
        # at the median quantile, fraction of stations agreeing on the
        # majority sign — reported once per season on the QUANTILE/median rows.
        if frame.height and stations_present:
            signs: list[int] = []
            for station in stations_present:
                station_frame = frame.filter(pl.col("station") == station)
                if station_frame.height == 0:
                    continue
                nearest_med = station_frame.select(
                    pl.col("nearest_mm_h").quantile(0.5, interpolation="linear")
                ).item()
                bilinear_med = station_frame.select(
                    pl.col("bilinear_mm_h").quantile(0.5, interpolation="linear")
                ).item()
                if nearest_med is None or bilinear_med is None:
                    continue
                signs.append(1 if nearest_med >= bilinear_med else -1)
            if signs:
                majority = 1 if sum(1 for s in signs if s > 0) >= len(signs) / 2 else -1
                agreement = sum(1 for s in signs if s == majority) / len(signs)
                for row in rows:
                    if (
                        row["scope"] == SensitivityScope.ACROSS_STATION
                        and row["season"] == season
                        and row["statistic"] == SensitivityStatistic.QUANTILE
                        and row["quantile"] == 0.5
                    ):
                        row["sign_agreement_fraction"] = agreement

    return pl.DataFrame(rows, infer_schema_length=None)
