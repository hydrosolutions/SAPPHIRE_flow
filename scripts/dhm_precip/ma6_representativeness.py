"""Plan 184 (M-A6) task T4 — representativeness, CHARACTERISED, not
decomposed (D5).

One point gauge against one ~110 km2 ERA5-Land cell cannot empirically
separate grid representativeness error from model error (D5). This module
computes the four things D5/Exit-6 name instead of a decomposition:

1. The operator-sensitivity envelope, read AS PUBLISHED from the precip
   bundle's `operator_sensitivity.csv` (D9) — never re-derived, and never
   validated in a way that would reject `sign_agreement_fraction == null`
   on `scope == STATION` rows, which is how every STATION row is
   legitimately published.
2. Station-to-grid elevation mismatch, read from `station_grid_elevation.csv`
   and carried as a DESCRIPTIVE COVARIATE labelled `datum_reconciled =
   UNRECONCILED` (D7) — never a causal explanation of precipitation phase,
   since ERA5-Land's `total_precipitation` is interpolated from ERA5, not
   regenerated on the 0.1 deg grid, and ERA5-Land elevation-corrects
   temperature/humidity/pressure, not precipitation.
3. Neighbouring-cell variability — the spread of ERA5-Land's own period-
   total precipitation across the immediate on-grid neighbours of each
   station's assigned cell, a property of the ERA5-Land FIELD itself,
   independent of the gauge and of D2/D13's retention rules (which govern
   gauge-vs-ERA5 pairing, not ERA5-vs-ERA5 spatial spread).
4. The Kirtipur/Khumaltar within-cell pair (D8) — DESCRIPTIVE ONLY, no
   lower-bound claim, computed on hours retained for BOTH stations, with
   the common-retained count and each station's own gauge-retained
   exposure carried beside it. n = 1 pair, one valley, one separation.

Out of scope (D5, D8): any decomposition of representativeness from model
error; any lower-bound claim from the within-cell pair.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: era5_extract.py:12 — xarray ships partial type stubs; the same
# three rules are relaxed repo-wide for every module that touches it.
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import TYPE_CHECKING

import polars as pl
import xarray as xr

from scripts.dhm_precip.domain_types import DatumReconciliationStatus, Station
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_extract_manifest import assert_payload_checksum_matches
from scripts.dhm_precip.era5_manifest import product_artifact_path
from scripts.dhm_precip.era5_request import STUDY_YEARS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from scripts.dhm_precip.era5_extract_manifest import ExtractionManifest
    from scripts.dhm_precip.ma6_pairs import GaugeMaskedPopulation

# D8's named pair — Kirtipur and Khumaltar, 4.33 km apart, one shared 0.1 deg
# cell. Named here so callers use the canonical constants rather than string
# literals scattered across call sites.
KIRTIPUR = Station("Kirtipur")
KHUMALTAR = Station("Khumaltar")

_OPERATOR_SENSITIVITY_CSV = "operator_sensitivity.csv"
_ELEVATION_CSV = "station_grid_elevation.csv"

# --- 1: the operator-sensitivity envelope (D9) ---

# The columns D9's real bundle publishes (`era5_extract.py`'s
# `build_operator_sensitivity_table`). This is a STRUCTURAL check only —
# columns present — never a check on any column's VALUES: `statistic`'s
# three members and `sign_agreement_fraction`'s legitimate STATION-row
# nulls are read AS PUBLISHED, never asserted against here (D9).
_OPERATOR_SENSITIVITY_REQUIRED_COLUMNS: tuple[str, ...] = (
    "scope",
    "station",
    "season",
    "statistic",
    "quantile",
    "nearest_value",
    "bilinear_value",
    "delta_absolute",
    "delta_unit",
    "ratio",
    "n_hours_common_finite",
    "n_hours_excluded",
    "n_wet_nearest",
    "n_wet_bilinear",
    "sign_agreement_fraction",
)


def read_operator_sensitivity_envelope(
    bundle_dir: Path, manifest: ExtractionManifest
) -> pl.DataFrame:
    """D9 — read `operator_sensitivity.csv` AS PUBLISHED. Checksum-verified
    against the manifest (P4a, reused verbatim from `era5_extract_manifest`
    — never re-derived), then checked structurally (required columns
    present) and NOTHING ELSE.

    ⛔ This must NOT reject `sign_agreement_fraction == null` on
    `scope == STATION` rows — verified on the real bundle, every one of its
    1,300 STATION rows carries a null there (only `ACROSS_STATION` rows
    populate it), and a validator demanding it non-null rejects every valid
    station row (D9's own trap)."""
    assert_payload_checksum_matches(bundle_dir, manifest, _OPERATOR_SENSITIVITY_CSV)
    path = bundle_dir / _OPERATOR_SENSITIVITY_CSV
    if not path.exists():
        raise ExtractionInputAbsentError(
            f"{_OPERATOR_SENSITIVITY_CSV} is absent at {bundle_dir} despite "
            "a manifest checksum entry for it"
        )
    frame = pl.read_csv(path)
    missing = [
        c for c in _OPERATOR_SENSITIVITY_REQUIRED_COLUMNS if c not in frame.columns
    ]
    if missing:
        raise ExtractionPostConditionError(
            f"{_OPERATOR_SENSITIVITY_CSV} at {bundle_dir} is missing "
            f"column(s) {missing} — cannot be D9's envelope"
        )
    return frame


# --- 2: station-to-grid elevation mismatch as a descriptive covariate (D7) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class ElevationMismatchCovariate:
    """One station's row of `station_grid_elevation.csv`, read verbatim.

    `datum_reconciled` is `UNRECONCILED` for every row today (station datum
    is `UNKNOWN`) — this type carries that label; it never asserts a datum
    and never converts the mismatch into a phase explanation (D7: ERA5-Land
    elevation-corrects temperature/humidity/pressure, not precipitation)."""

    station: Station
    station_elev_m: float
    orography_elev_m: float
    elev_mismatch_m: float
    datum_reconciled: DatumReconciliationStatus
    grid_i: int
    grid_j: int
    shared_cell_id: str
    stations_in_cell: int


_ELEVATION_REQUIRED_COLUMNS: tuple[str, ...] = (
    "station",
    "grid_i",
    "grid_j",
    "station_elev_m",
    "orography_elev_m",
    "elev_mismatch_m",
    "datum_reconciled",
    "shared_cell_id",
    "stations_in_cell",
)


def read_elevation_mismatch_covariates(
    bundle_dir: Path, manifest: ExtractionManifest
) -> tuple[ElevationMismatchCovariate, ...]:
    """D7 — the station-to-grid elevation mismatch, read as a DESCRIPTIVE
    COVARIATE, never a causal explanation of precipitation phase. Checksum-
    verified against the manifest (P4a), never re-derived from orography or
    coordinates."""
    assert_payload_checksum_matches(bundle_dir, manifest, _ELEVATION_CSV)
    path = bundle_dir / _ELEVATION_CSV
    if not path.exists():
        raise ExtractionInputAbsentError(
            f"{_ELEVATION_CSV} is absent at {bundle_dir} despite a manifest "
            "checksum entry for it"
        )
    frame = pl.read_csv(path)
    missing = [c for c in _ELEVATION_REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ExtractionPostConditionError(
            f"{_ELEVATION_CSV} at {bundle_dir} is missing column(s) {missing}"
        )
    return tuple(
        ElevationMismatchCovariate(
            station=Station(str(row["station"])),
            station_elev_m=float(row["station_elev_m"]),
            orography_elev_m=float(row["orography_elev_m"]),
            elev_mismatch_m=float(row["elev_mismatch_m"]),
            datum_reconciled=DatumReconciliationStatus(str(row["datum_reconciled"])),
            grid_i=int(row["grid_i"]),
            grid_j=int(row["grid_j"]),
            shared_cell_id=str(row["shared_cell_id"]),
            stations_in_cell=int(row["stations_in_cell"]),
        )
        for row in frame.iter_rows(named=True)
    )


def _elevation_row_for(
    elevation_rows: tuple[ElevationMismatchCovariate, ...], station: Station
) -> ElevationMismatchCovariate:
    for row in elevation_rows:
        if row.station == station:
            return row
    raise StationSetMismatchError(
        f"{station!r} is absent from the given elevation-mismatch covariate table"
    )


# --- 3: neighbouring-cell variability ---

# The 8 immediate on-grid neighbours (Chebyshev distance 1) of a station's
# assigned nearest cell — clipped at domain edges, never wrapped.
_NEIGHBOUR_OFFSETS: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


@dataclass(frozen=True, kw_only=True, slots=True)
class NeighbourCellStat:
    """One station's neighbouring-cell variability: the spread of ERA5-
    Land's own period-total precipitation across the immediate on-grid
    neighbours of its assigned cell. A property of the ERA5-Land FIELD,
    independent of the gauge — this is NOT a gauge-vs-ERA5 comparison and
    carries no retained-hour count."""

    station: Station
    grid_i: int
    grid_j: int
    years: tuple[int, ...]
    assigned_total_mm: float
    neighbour_total_mm: tuple[float, ...]
    n_neighbours: int
    neighbour_range_mm: float
    neighbour_cv: float | None
    """`None` when fewer than 2 on-grid neighbours exist, or when their mean
    is exactly zero (a coefficient of variation is undefined there)."""


def _neighbour_cells(
    grid_i: int, grid_j: int, *, n_lat: int, n_lon: int
) -> tuple[tuple[int, int], ...]:
    cells: list[tuple[int, int]] = []
    for d_i, d_j in _NEIGHBOUR_OFFSETS:
        i, j = grid_i + d_i, grid_j + d_j
        if 0 <= i < n_lat and 0 <= j < n_lon:
            cells.append((i, j))
    return tuple(cells)


def _neighbour_range_and_cv(
    neighbour_totals: Sequence[float],
) -> tuple[float, float | None]:
    if not neighbour_totals:
        raise ValueError(
            "a station with zero on-grid neighbours cannot report "
            "neighbouring-cell variability — every station in the 26-"
            "station set has at least one on-grid neighbour in practice; an "
            "empty stencil here signals a domain/grid mismatch, not a "
            "valid edge case to silently paper over"
        )
    lo, hi = min(neighbour_totals), max(neighbour_totals)
    rng = hi - lo
    if len(neighbour_totals) < 2:
        return rng, None
    mean = fmean(neighbour_totals)
    if mean == 0.0:
        return rng, None
    return rng, pstdev(neighbour_totals, mu=mean) / mean


def _read_cell_totals(
    data_root: Path, cells: frozenset[tuple[int, int]], *, years: tuple[int, ...]
) -> dict[tuple[int, int], float]:
    """Reads the ALREADY-PUBLISHED `hourly_mm` annual products directly —
    this is a spatial-spread diagnostic over an existing, already-validated
    product (D9/D5.1 validate it at acquisition/extraction time), not a
    re-acquisition or a re-validation of it.

    Loads each year's FULL grid into memory once (`.load()`, the same
    pattern `extract_era5.py`'s own per-station extraction already uses),
    then sums the needed cells with plain numpy indexing — never a
    per-cell lazy `.isel()` against the still-on-disk dataset, which issues
    one HDF5 chunk-decompression per (cell, year) and was measured to still
    be running after 5+ minutes for ~200 cells x 6 years on the real
    product. NaN propagates via a plain `.sum(axis=0)` (no `skipna`
    argument to suppress it); the caller checks finiteness before trusting
    any total."""
    totals: dict[tuple[int, int], float] = dict.fromkeys(cells, 0.0)
    for year in years:
        path = product_artifact_path(year, data_root)
        if not path.exists():
            raise ExtractionInputAbsentError(
                f"acquired ERA5-Land product for {year} is missing at {path}"
            )
        with xr.open_dataset(path, engine="h5netcdf") as reopened:
            values = reopened["precipitation"].load().to_numpy()
        for i, j in cells:
            totals[(i, j)] += float(values[:, i, j].sum())
    return totals


def compute_neighbour_cell_variability(
    elevation_rows: tuple[ElevationMismatchCovariate, ...],
    *,
    data_root: Path,
    years: tuple[int, ...] = STUDY_YEARS,
) -> tuple[NeighbourCellStat, ...]:
    """3 — for every station in `elevation_rows`, the spread of ERA5-Land's
    own period-total precipitation across its assigned cell's immediate
    on-grid neighbours, over `years` (defaults to the full acquired
    archive, `era5_request.STUDY_YEARS`, reused rather than re-derived)."""
    if not years:
        raise ValueError("compute_neighbour_cell_variability needs at least one year")
    first_path = product_artifact_path(years[0], data_root)
    if not first_path.exists():
        raise ExtractionInputAbsentError(
            f"acquired ERA5-Land product for {years[0]} is missing at {first_path}"
        )
    with xr.open_dataset(first_path, engine="h5netcdf") as ds:
        n_lat = ds.sizes["latitude"]
        n_lon = ds.sizes["longitude"]

    per_station: dict[Station, tuple[tuple[int, int], tuple[tuple[int, int], ...]]] = {}
    needed: set[tuple[int, int]] = set()
    for row in elevation_rows:
        assigned = (row.grid_i, row.grid_j)
        neighbours = _neighbour_cells(row.grid_i, row.grid_j, n_lat=n_lat, n_lon=n_lon)
        per_station[row.station] = (assigned, neighbours)
        needed.add(assigned)
        needed.update(neighbours)

    totals = _read_cell_totals(data_root, frozenset(needed), years=years)
    for (i, j), total in totals.items():
        if not math.isfinite(total):
            raise ExtractionPostConditionError(
                f"grid cell (grid_i={i}, grid_j={j}) has a non-finite period "
                f"total over {years} — ERA5-Land over this box should be "
                "complete"
            )

    results: list[NeighbourCellStat] = []
    for row in elevation_rows:
        assigned_cell, neighbour_cells = per_station[row.station]
        neighbour_totals = tuple(totals[c] for c in neighbour_cells)
        rng, cv = _neighbour_range_and_cv(neighbour_totals)
        results.append(
            NeighbourCellStat(
                station=row.station,
                grid_i=row.grid_i,
                grid_j=row.grid_j,
                years=years,
                assigned_total_mm=totals[assigned_cell],
                neighbour_total_mm=neighbour_totals,
                n_neighbours=len(neighbour_totals),
                neighbour_range_mm=rng,
                neighbour_cv=cv,
            )
        )
    return tuple(results)


# --- 4: the Kirtipur/Khumaltar within-cell pair (D8) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class WithinCellPairResult:
    """D8 — DESCRIPTIVE ONLY. No lower-bound claim follows from this: two
    gauges with unbiased errors still differ over a finite sample by noise
    alone. n = 1 pair, one valley, one separation — never a network-wide
    estimate."""

    station_a: Station
    station_b: Station
    shared_cell_id: str
    n_common_retained: int
    """Hours retained for BOTH stations (D8) — never either station's own
    retention count."""
    n_a_gauge_retained: int
    """Station A's own whole-record gauge-retained exposure (Exit 7 — "each
    station's own exposure"), independent of B's retention."""
    n_b_gauge_retained: int
    mean_difference_mm_per_h: float | None
    """mean(a - b) over the n_common_retained COMMON hours. `None` when
    n_common_retained == 0."""
    accumulated_difference_mm: float | None
    """sum(a) - sum(b) over the same common hours."""
    sum_a_mm: float | None
    sum_b_mm: float | None


def compute_within_cell_pair(
    gauge_population: GaugeMaskedPopulation,
    elevation_rows: tuple[ElevationMismatchCovariate, ...],
    *,
    station_a: Station,
    station_b: Station,
) -> WithinCellPairResult:
    """D8 — computed on hours retained for BOTH stations, never on either
    station's own retention (the recurring failure mode this milestone
    keeps reproducing: two means over populations that are not the same).
    `shared_cell_id` equality is ASSERTED, not assumed — a caller naming two
    stations that do not actually share a cell has violated D8's own
    premise and gets a typed failure, never a silently-computed number."""
    row_a = _elevation_row_for(elevation_rows, station_a)
    row_b = _elevation_row_for(elevation_rows, station_b)
    if row_a.shared_cell_id != row_b.shared_cell_id:
        raise StationSetMismatchError(
            f"{station_a!r} (cell {row_a.shared_cell_id!r}) and "
            f"{station_b!r} (cell {row_b.shared_cell_id!r}) do not share a "
            "cell — D8's within-cell pair premise does not hold for this pair"
        )
    series_a = gauge_population.by_station[station_a]
    series_b = gauge_population.by_station[station_b]
    joined = series_a.frame.rename({"value_mm": "value_a"}).join(
        series_b.frame.rename({"value_mm": "value_b"}), on="timestamp", how="inner"
    )
    n_common = joined.height
    sum_a = sum_b = accumulated_difference = mean_difference = None
    if n_common > 0:
        sum_a = float(joined["value_a"].sum())
        sum_b = float(joined["value_b"].sum())
        accumulated_difference = sum_a - sum_b
        # mean(a - b) == (sum(a) - sum(b)) / n exactly (both reduce the same
        # finite population); derived arithmetically rather than via a
        # second, independently-ordered polars reduction so the two
        # reported numbers can never disagree by summation order.
        mean_difference = accumulated_difference / n_common
    return WithinCellPairResult(
        station_a=station_a,
        station_b=station_b,
        shared_cell_id=row_a.shared_cell_id,
        n_common_retained=n_common,
        n_a_gauge_retained=series_a.frame.height,
        n_b_gauge_retained=series_b.frame.height,
        mean_difference_mm_per_h=mean_difference,
        accumulated_difference_mm=accumulated_difference,
        sum_a_mm=sum_a,
        sum_b_mm=sum_b,
    )
