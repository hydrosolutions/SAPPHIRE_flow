#!/usr/bin/env python3
"""Plan 184 (M-A6) task T2 — the D14 lapse correction and its Pyramid `AT`
check.

Two things, per the task spec:

1. **The D14 lapse correction** (`lapse_correct_to_station_degc`) — the
   standard 6.5 degC/km rate, applied from MODEL OROGRAPHY down to STATION
   elevation. Model orography sits ABOVE most of our high-altitude stations
   (Syangboche 3,700 m vs cell 4,447 m; Humde 3,401 m vs cell 4,700 m), so
   the correction usually WARMS — this is NOT universal: AWS4 Kala Patthar
   sits ~19 m ABOVE its own cell's orography, so its correction COOLS (see
   `lapse_correct_to_station_degc`'s docstring). Never fitted, never tuned
   (D14: "a rate derived from Lukla and Namche could not then be validated
   at Lukla and Namche").

2. **The transect check** — Pyramid `AT` at AWS3 (2,660 m), AWS5 (3,570 m),
   AWS2 (4,260 m), AWS1 (5,035 m), AWS4 (5,600 m), 2020-2023 (the Pyramid
   record ends 2023), hour-of-day exposure equalised, compared against the
   lapse-corrected ERA5-Land grid cell nearest each station. Reports a
   QUANTIFIED DISCREPANCY per station — never a pass/fail verdict (D14: "if
   the check fails, widen the reported uncertainty; do not refit").

**D6 — reuse, never re-derive.** Two DIFFERENT things are reused here, and
neither is re-fetched from CDS (this module never touches `cdsapi`):

- For a DHM gauge station's own model-orography elevation, this module
  reads M-A5's published `station_grid_elevation.csv` (`_discover_precip_
  bundle`, the same highest-valid-`NNNN`-with-checksum-reconciled
  convention `ma6_pairs.discover_precip_bundle` and `extract_era5_t2m.
  _discover_precip_bundle` already apply, to a THIRD payload —
  `station_grid_elevation.csv` — never re-derived here).
- For a PYRAMID station (Namche, Lukla, Pheriche, Kala Patthar, "Pyramid"
  itself) — none of which is a DHM gauge station, so none appears in that
  CSV — this module samples the SAME already-materialised, already-
  verified M-A5 orography raster (Plan 174 task 1b) at the Pyramid
  station's own coordinate, via the exact nearest-cell lookup
  `era5_extract.build_station_grid_elevation_table` already uses for DHM
  stations (`np.argmin` against the raster's lat/lon axes), reusing
  `era5_orography.verify_orography_materialisation` to re-verify the
  raster on every run rather than trusting a file of the right name. This
  is reuse of the MATERIALISED grid and its established lookup pattern,
  never a re-fetch and never a re-run of the acquisition/aggregation
  pipeline.

Pyramid station coordinates and elevations are the network's own published
metadata (`data/dhm_precip/pyramid/README_.txt`), not derived from
anything this track computes.

**Timestamp convention (D14 amendment, 2026-08-21; corrected 2026-08-21,
Finding A of the Plan 184 T2 round-2 review).** Pyramid's README does not
declare period-beginning vs period-ending; this module ASSUMES
period-ending (WMO/AWS-logger convention) and does not resolve it further,
because the hour-of-day-equalised MEAN, taken alone, is INVARIANT to a
whole-hour relabelling applied uniformly to an already-fixed population
(the same 24 hourly means are averaged, only their labels change). That
invariance does NOT extend to `pair_pyramid_and_era5`'s join itself:
`pyramid_loader.load_pyramid_lvl1_at_csv` returns Pyramid timestamps as NPT
wall-clock, UNCONVERTED, while the ERA5-Land grid series is UTC — joining
those two label sets directly would pair each Pyramid hour to the WRONG
ERA5 hour (a ~5h45m physical offset), silently selecting a different,
uncorrelated population rather than measuring a clean lapse residual (the
same D2 "masking one side measures selection, not weather" failure mode
Finding 1 of the first review round fixed on the OTHER axis). `pair_pyramid_
and_era5` therefore reconciles the clocks BEFORE joining: it shifts the
ERA5 (UTC) timestamps forward by `params.coloc_dhm_utc_to_npt_hour_offset`
whole hours (the SAME rounded 5h45m->6h convention `stats_coloc.
dhm_utc_to_npt` already applies to DHM-vs-Pyramid pairing — reused here
verbatim, never re-derived), landing them on NPT-labelled hours before the
join. This reconciliation is ROUNDED, so a residual sub-hour misalignment
remains — the same declared +-1.75h family M-A10 documents
(`params.coloc_alignment_uncertainty_hours`) — this module does not
re-derive that uncertainty, only inherits it. Neither this rounding nor the
still-unresolved period-ending assumption is fully resolved by this check;
both are bounded, not eliminated.

Usage:
    uv run python scripts/dhm_precip/ma6_lapse_check.py --out <dir>

Environment:
    DHM_PRECIP_ERA5_ROOT  root under which `era5_land/` (precipitation +
                          orography) lives (default `data/dhm_precip`).
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: era5_extract.py:12 — xarray ships partial type stubs; the same
# three rules are relaxed repo-wide for every module that touches it.
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
import structlog  # noqa: E402
import xarray as xr  # noqa: E402

from scripts.dhm_precip.domain_types import Station, StationCoordinate  # noqa: E402
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_extract import (  # noqa: E402
    ExtractedSeries,
    assert_station_within_grid,
    extract_nearest_series,
)
from scripts.dhm_precip.era5_extract_manifest import (  # noqa: E402
    assert_payload_checksum_matches,
    manifest_filename,
    points_root,
    read_extraction_manifest,
)
from scripts.dhm_precip.era5_manifest import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    product_artifact_path,
)
from scripts.dhm_precip.era5_orography import (  # noqa: E402
    orography_raster_path,
    orography_route_identity,
    orography_source_record_path,
    read_orography_source_record,
    verify_orography_materialisation,
)
from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC  # noqa: E402
from scripts.dhm_precip.era5_request import (  # noqa: E402
    DEFAULT_REQUEST_SPEC,
    expected_grid_shape,
)
from scripts.dhm_precip.numeric import as_float  # noqa: E402
from scripts.dhm_precip.params import DEFAULT_PARAMS  # noqa: E402
from scripts.dhm_precip.pyramid_loader import (  # noqa: E402
    PyramidLoaderError,
    load_pyramid_lvl1_at_csv,
)

if TYPE_CHECKING:
    from scripts.dhm_precip.era5_extract_manifest import ExtractionManifest

log = structlog.get_logger(__name__)

# D14 — the standard rate. NEVER fitted, NEVER tuned (D14's own rule): the
# transect check exists precisely so this constant is validated, and a rate
# derived from the validation data could then not be validated by it.
STANDARD_LAPSE_RATE_DEGC_PER_KM: Final = 6.5

DEFAULT_PRECIP_DATA_ROOT: Path = _REPO_ROOT / "data" / "dhm_precip"
DEFAULT_T2M_DATA_ROOT: Path = _REPO_ROOT / "data" / "dhm_precip" / "era5_land_t2m"
DEFAULT_PYRAMID_DIR: Path = _REPO_ROOT / "data" / "dhm_precip" / "pyramid"

_STATION_ELEVATION_CSV = "station_grid_elevation.csv"

# T2's own transect (5 stations, 2,940 m span) — coordinates and elevations
# are the Pyramid network's own published metadata (README_.txt), never
# derived. AWS0/AWSSC/CNG_SNP are Non-goals (no DHM overlap, or outside the
# transect the plan names).
TRANSECT_START_YEAR: Final = 2020
TRANSECT_END_YEAR: Final = 2023
"""D14 amendment — the Pyramid record ends in 2023; the transect is
validated on 4 of the 6 comparison years."""


@dataclass(frozen=True, kw_only=True, slots=True)
class TransectStation:
    pyramid_station: Station
    csv_filename: str
    lat: float
    lon: float
    elevation_m: float


TRANSECT_STATIONS: tuple[TransectStation, ...] = (
    TransectStation(
        pyramid_station=Station("AWS3 Lukla"),
        csv_filename="AWS3_Z2660_Lvl1.csv",
        lat=27.70,
        lon=86.72,
        elevation_m=2660.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS5 Namche"),
        csv_filename="AWS5_Z3570_Lvl1.csv",
        lat=27.80,
        lon=86.71,
        elevation_m=3570.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS2 Pheriche"),
        csv_filename="AWS2_Z4260_Lvl1.csv",
        lat=27.90,
        lon=86.82,
        elevation_m=4260.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS1 Pyramid"),
        csv_filename="AWS1_Z5035_Lvl1.csv",
        lat=27.96,
        lon=86.81,
        elevation_m=5035.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS4 Kala Patthar"),
        csv_filename="AWS4_Z5600_Lvl1.csv",
        lat=27.99,
        lon=86.83,
        elevation_m=5600.0,
    ),
)


def lapse_correct_to_station_degc(
    grid_t2m_degc: np.ndarray | float,
    *,
    orography_elev_m: float,
    station_elev_m: float,
    rate_degc_per_km: float = STANDARD_LAPSE_RATE_DEGC_PER_KM,
) -> np.ndarray | float:
    """D14 — correct FROM model orography DOWN TO station elevation.

    Sign: model orography sits ABOVE the station for most high-altitude
    cases this track cares about, so the correction usually WARMS —
    verified against the real elevation table (D14 amendment,
    2026-08-20): Humde's 1,299.18 m diff -> +8.44 degC at 6.5 degC/km;
    Olangchunggola +6.73; Lukla +6.52; Syangboche +4.86 (all reproduced
    exactly by `tests/unit/scripts/test_pyramid_at.py::
    TestLapseCorrectionSign`). This is NOT universal within the track:
    AWS4 Kala Patthar (Finding 2, Plan 184 T2 review) sits ~19 m ABOVE its
    own cell's orography (station 5,600 m vs orography 5,581.1 m), so its
    correction is small and COOLS (-0.123 degC at 6.5 degC/km), not warms.

    `correction = rate * (orography_elev_m - station_elev_m) / 1000`,
    ADDED to the raw grid value: positive when orography is above the
    station (the standard-atmosphere lapse rate says lower elevation is
    warmer), negative when the station happens to sit above its grid
    cell's orography."""
    correction = rate_degc_per_km * (orography_elev_m - station_elev_m) / 1000.0
    return grid_t2m_degc + correction


def hour_of_day_equalised_mean(
    frame: pl.DataFrame, *, timestamp_col: str = "timestamp", value_col: str = "value"
) -> float:
    """D14 amendment — equal-weight mean across the 24 hour-of-day buckets,
    REGARDLESS of how many observations landed in each hour. Pyramid is
    irregularly sampled (AWS3 August 2023: 215 `AT` observations, 3-19 per
    hour), so a naive arithmetic mean over-weights whichever hours happened
    to be sampled more densely — measured as a 1.00 degC shift on that
    month alone. This function removes that bias; it does NOT touch the
    NPT-vs-UTC clock question (module docstring)."""
    if frame.height == 0:
        raise ValueError(
            "cannot compute an hour-of-day equalised mean over an empty frame"
        )
    hourly = frame.group_by(pl.col(timestamp_col).dt.hour().alias("hour")).agg(
        pl.col(value_col).mean().alias("hour_mean")
    )
    return as_float(hourly["hour_mean"].mean())


@dataclass(frozen=True, kw_only=True, slots=True)
class TransectStationResult:
    """One transect station's quantified discrepancy — never a pass/fail
    verdict (D14: "if the check fails, widen the reported uncertainty on
    the mass-fraction column; do not refit")."""

    pyramid_station: Station
    station_elevation_m: float
    orography_elev_m: float
    lapse_correction_degc: float
    n_era5_hours: int
    """Total ERA5-Land hours available in the transect window — context
    only, NOT the population the means below are computed over."""
    n_pyramid_retained: int
    """Total Pyramid `AT` hours Pyramid itself retained in the transect
    window — context only, NOT the population the means below are
    computed over."""
    n_paired: int
    """The COMMONLY-retained population (Finding 1, Plan 184 T2 review) —
    `pair_pyramid_and_era5`'s inner join of `n_pyramid_retained` against
    the finite ERA5 hours, mirroring `ma6_pairs.PairedRetainedSubset.
    n_common_retained`'s discipline exactly. All three hour-of-day-
    equalised means below (`era5_raw_*`, `era5_corrected_*`,
    `pyramid_hour_equalised_degc`) are computed over THIS population,
    never independently — hour-of-day equalisation alone equalises hour
    exposure, not seasonal/date exposure."""
    era5_raw_hour_equalised_degc: float
    era5_corrected_hour_equalised_degc: float
    pyramid_hour_equalised_degc: float
    discrepancy_degc: float
    """`era5_corrected_hour_equalised_degc - pyramid_hour_equalised_degc` —
    the quantified check result. Positive: the lapse-corrected grid reads
    WARMER than Pyramid. Negative: colder."""


def pair_pyramid_and_era5(
    *,
    era5_grid: pl.DataFrame,
    pyramid_at: pl.DataFrame,
    utc_to_npt_hour_offset: int = DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset,
) -> pl.DataFrame:
    """D2 (Finding 1, Plan 184 T2 round-1 review; clock fix Finding A,
    round-2 review) — inner-join Pyramid's own retained `AT` population
    against the finite ERA5-Land grid series on exact timestamp, BEFORE any
    averaging, mirroring `ma6_pairs.pair_with_era5`'s established
    discipline exactly: an hour survives only when BOTH sides already kept
    it. Hour-of-day equalisation alone only equalises HOUR exposure; it
    does not equalise SEASONAL or DATE exposure, so comparing an all-season
    ERA5 mean against whatever subset Pyramid happened to record measures
    observation availability, not clean lapse residuals (D2: "masking one
    side measures selection, not weather").

    `era5_grid` (UTC) and `pyramid_at` (NPT wall-clock, unconverted —
    `pyramid_loader` module docstring) are NOT on the same clock, so a join
    on raw labels would pair each side's DIFFERENT physical hour — the same
    D2 failure mode, relocated to the clock axis rather than resolved. This
    function therefore reconciles the clocks FIRST: `era5_grid`'s UTC
    timestamps are shifted forward by `utc_to_npt_hour_offset` whole hours
    (default `params.coloc_dhm_utc_to_npt_hour_offset`, the SAME rounded
    5h45m->6h convention `stats_coloc.dhm_utc_to_npt` already applies to
    DHM-vs-Pyramid pairing — reused verbatim, never re-derived, never a
    hand-rolled offset) onto NPT-labelled hours, THEN joined against
    Pyramid's own NPT labels. The reconciliation is ROUNDED: a residual
    sub-hour misalignment remains, the same declared +-1.75h family M-A10
    documents (`params.coloc_alignment_uncertainty_hours`) — not
    re-derived here, only inherited.

    `era5_grid` is `(timestamp, value)`; `pyramid_at` is `(timestamp,
    value)`. Returns `(timestamp, pyramid_value_degc, era5_value_degc)`,
    sorted ascending, on the NPT-labelled join key."""
    era5_npt = era5_grid.rename({"value": "era5_value_degc"}).with_columns(
        (pl.col("timestamp") + pl.duration(hours=utc_to_npt_hour_offset))
        .cast(pyramid_at.schema["timestamp"])
        .alias("timestamp")
    )
    pyramid_renamed = pyramid_at.rename({"value": "pyramid_value_degc"})
    return pyramid_renamed.join(era5_npt, on="timestamp", how="inner").sort("timestamp")


@dataclass(frozen=True, kw_only=True, slots=True)
class GaugeToGaugeDiagnostic:
    """Finding B, Plan 184 T2 round-2 review — the AWS1/AWS4 shared-cell
    gauge comparison, computed on the timestamps BOTH gauges retained
    (never on each gauge's own, independently-sized, ERA5-paired
    population — that was the round-2 finding: 35,019 vs 24,369 hours,
    the same D2 population error relocated from the gauge-vs-ERA5 axis to
    the gauge-vs-gauge axis)."""

    n_common_retained: int
    mean_a_degc: float
    mean_b_degc: float


def paired_gauge_diagnostic(
    frame_a: pl.DataFrame, frame_b: pl.DataFrame
) -> GaugeToGaugeDiagnostic | None:
    """D2/D3 (Finding B, Plan 184 T2 round-2 review) — inner-join two
    Pyramid gauges' own retained `AT` populations on exact timestamp
    BEFORE any averaging, mirroring `pair_pyramid_and_era5`'s discipline
    gauge-to-gauge instead of gauge-to-ERA5. `frame_a`/`frame_b` are each
    `(timestamp, value)` (a station's own windowed, retained population).
    Returns `None` when the two gauges share zero common-retained
    timestamps — the diagnostic is optional (D14: never a candidate
    replacement rate), never worth a hard failure."""
    a = frame_a.select(pl.col("timestamp"), pl.col("value").alias("_a"))
    b = frame_b.select(
        pl.col("timestamp").cast(a.schema["timestamp"]), pl.col("value").alias("_b")
    )
    paired = a.join(b, on="timestamp", how="inner")
    if paired.height == 0:
        return None
    return GaugeToGaugeDiagnostic(
        n_common_retained=paired.height,
        mean_a_degc=hour_of_day_equalised_mean(paired, value_col="_a"),
        mean_b_degc=hour_of_day_equalised_mean(paired, value_col="_b"),
    )


def compute_transect_station_result(
    *,
    station: TransectStation,
    era5_grid: pl.DataFrame,
    orography_elev_m: float,
    pyramid_at: pl.DataFrame,
    rate_degc_per_km: float = STANDARD_LAPSE_RATE_DEGC_PER_KM,
) -> TransectStationResult:
    """The pure, testable core — no I/O. `era5_grid` is `(timestamp,
    value)` (UTC, the nearest-cell raw ERA5-Land t2m series already
    restricted to the transect window); `pyramid_at` is `(timestamp,
    value)` (NPT, `load_pyramid_lvl1_at_csv`'s retained population already
    restricted to the transect window). Both sides are paired via
    `pair_pyramid_and_era5` (Finding 1, Plan 184 T2 review) BEFORE any
    hour-of-day equalisation — the three reported means are always
    computed over the SAME commonly-retained population, never
    independently."""
    lapse_correction = float(
        rate_degc_per_km * (orography_elev_m - station.elevation_m) / 1000.0
    )
    paired = pair_pyramid_and_era5(era5_grid=era5_grid, pyramid_at=pyramid_at)
    corrected = paired.with_columns(
        (pl.col("era5_value_degc") + lapse_correction).alias("corrected")
    )
    return TransectStationResult(
        pyramid_station=station.pyramid_station,
        station_elevation_m=station.elevation_m,
        orography_elev_m=orography_elev_m,
        lapse_correction_degc=lapse_correction,
        n_era5_hours=era5_grid.height,
        n_pyramid_retained=pyramid_at.height,
        n_paired=paired.height,
        era5_raw_hour_equalised_degc=hour_of_day_equalised_mean(
            paired, value_col="era5_value_degc"
        ),
        era5_corrected_hour_equalised_degc=(
            corrected_mean := hour_of_day_equalised_mean(
                corrected, value_col="corrected"
            )
        ),
        pyramid_hour_equalised_degc=(
            pyramid_mean := hour_of_day_equalised_mean(
                paired, value_col="pyramid_value_degc"
            )
        ),
        discrepancy_degc=corrected_mean - pyramid_mean,
    )


# --- D6 reuse: discover the published precipitation bundle, read-only ---


def _discover_precip_bundle(precip_data_root: Path) -> tuple[Path, ExtractionManifest]:
    """The SAME highest-valid-`NNNN`-with-checksum-reconciled convention
    `ma6_pairs.discover_precip_bundle` and `extract_era5_t2m._discover_
    precip_bundle` already apply, here reconciling a THIRD payload
    (`station_grid_elevation.csv`) — read-only, never re-derived, never
    globbed by identity (P3)."""
    root = points_root(precip_data_root)
    if not root.exists():
        raise ExtractionInputAbsentError(
            f"no precipitation extraction points root at {root} — T2 needs "
            "a published precipitation bundle's station_grid_elevation.csv"
        )
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name != ".staging"),
        key=lambda p: p.name,
    )
    for candidate in reversed(candidates):
        manifest = read_extraction_manifest(candidate / manifest_filename())
        if manifest is not None:
            assert_payload_checksum_matches(candidate, manifest, _STATION_ELEVATION_CSV)
            return candidate, manifest
    raise ExtractionInputAbsentError(
        "no published precipitation extraction bundle with a readable "
        f"manifest and a checksum-verified {_STATION_ELEVATION_CSV!r} found "
        f"under {root}"
    )


def load_station_grid_elevation_table(precip_bundle_dir: Path) -> pl.DataFrame:
    """Reads the already-published `station_grid_elevation.csv` verbatim —
    the DHM-gauge-station half of D14's lapse input (D6: reuse, never
    re-derive)."""
    return pl.read_csv(precip_bundle_dir / _STATION_ELEVATION_CSV)


def discover_and_load_station_grid_elevation_table(
    precip_data_root: Path,
) -> tuple[pl.DataFrame, str]:
    """D14 item 2's public entry point for the DHM-gauge-station half of
    the lapse input: discover M-A5's published bundle (`_discover_precip_
    bundle`, D6 — never re-derived) and read its `station_grid_elevation.
    csv` verbatim. Returns the table plus the bundle's `extraction_
    identity`, so a caller (this module's own report, or a future T5) can
    record which bundle it consumed."""
    bundle_dir, manifest = _discover_precip_bundle(precip_data_root)
    return load_station_grid_elevation_table(bundle_dir), manifest.extraction_identity


# --- D6 reuse: the M-A5 orography raster, read-only, re-verified ---


def load_verified_orography(precip_data_root: Path) -> xr.Dataset:
    """Reuses the ALREADY-MATERIALISED, ALREADY-VERIFIED M-A5 orography
    raster (Plan 174 task 1b) — never re-fetches from CDS, never re-runs
    the acquisition/aggregation pipeline. Re-verifies on every run via the
    same `verify_orography_materialisation` the production extraction CLI
    uses (`extract_era5.py`), rather than trusting a file of the right name
    (D7's own rule, reused verbatim, never re-derived)."""
    area = DEFAULT_REQUEST_SPEC.area
    lat_count, lon_count = expected_grid_shape(area)
    north, west, south, east = area
    expected_lat = np.round(np.linspace(south, north, lat_count), 10)
    expected_lon = np.round(np.linspace(west, east, lon_count), 10)

    record = read_orography_source_record(
        orography_source_record_path(precip_data_root)
    )
    if record is None or record.raster_path is None:
        raise ExtractionInputAbsentError(
            "no materialised orography raster found under "
            f"{precip_data_root / 'era5_land' / 'orography'} — T2 reuses "
            "M-A5's published raster and never fetches one"
        )
    current_route_identity = orography_route_identity(OBSERVED_OROGRAPHY_SPEC)
    if record.orography_route_identity != current_route_identity:
        raise ExtractionPostConditionError(
            f"orography source record's route {record.orography_route_identity!r} "
            f"does not match the current frozen spec's route "
            f"{current_route_identity!r} — re-materialise via extract_era5.py "
            "(T2 never re-fetches)"
        )
    verify_orography_materialisation(
        record,
        data_root=precip_data_root,
        spec=OBSERVED_OROGRAPHY_SPEC,
        expected_lat=expected_lat,
        expected_lon=expected_lon,
    )
    raster_path = orography_raster_path(
        precip_data_root, route_identity=record.orography_route_identity
    )
    with xr.open_dataset(raster_path, engine="h5netcdf") as opened:
        return opened.load()


def orography_elev_at_nearest_cell(
    orography_ds: xr.Dataset, series: ExtractedSeries
) -> float:
    """Same nearest-cell lookup `era5_extract.build_station_grid_elevation_
    table` already uses for DHM stations (`np.argmin` against the raster's
    own lat/lon axes) — applied here to a `series` extracted at a Pyramid
    coordinate rather than a DHM one. The lookup ALGORITHM is reused
    verbatim; only the input coordinate differs."""
    orography_lat = orography_ds["latitude"].values
    orography_lon = orography_ds["longitude"].values
    oro_i = int(np.argmin(np.abs(orography_lat - series.grid_lat)))
    oro_j = int(np.argmin(np.abs(orography_lon - series.grid_lon)))
    value = float(orography_ds["orography_elev_m"].values[oro_i, oro_j])
    if not np.isfinite(value):
        raise ExtractionPostConditionError(
            f"orography cell (grid_i={oro_i}, grid_j={oro_j}) nearest "
            f"{series.station!r} is non-finite"
        )
    return value


def _load_era5_t2m_nearest_series(
    t2m_data_root: Path, *, coord: StationCoordinate, years: range
) -> ExtractedSeries:
    """Reads the full-grid ERA5-Land t2m products already on disk
    (`era5_land_t2m/era5_land/degc/era5_land_t2m_degc_<year>.nc`, the SAME
    per-year grid files `extract_era5_t2m.py` reads for the 26 DHM
    stations) and samples the nearest cell at `coord` for each requested
    year, concatenating into one series — never a CDS fetch."""
    parts: list[ExtractedSeries] = []
    for year in years:
        product_path = product_artifact_path(
            year,
            t2m_data_root,
            variable_code="t2m",
            product_dir_name="degc",
            unit_label="degc",
        )
        if not product_path.exists():
            raise ExtractionInputAbsentError(
                f"acquired ERA5-Land t2m product for {year} is missing at "
                f"{product_path}"
            )
        with xr.open_dataset(product_path, engine="h5netcdf") as reopened:
            ds = reopened.load()
        assert_station_within_grid(
            coord, lat=ds["latitude"].values, lon=ds["longitude"].values
        )
        parts.append(extract_nearest_series(ds, coord, variable="temperature"))
    first = parts[0]
    valid_time = np.concatenate([p.valid_time for p in parts])
    values = np.concatenate([p.values for p in parts])
    n_nan = int(np.isnan(values).sum())
    n_inf = int(np.isinf(values).sum())
    return replace(
        first,
        valid_time=valid_time,
        values=values,
        n_finite=values.size - n_nan - n_inf,
        n_nan=n_nan,
        n_inf=n_inf,
    )


def build_transect_report(
    *,
    precip_data_root: Path,
    t2m_data_root: Path,
    pyramid_dir: Path,
) -> tuple[tuple[TransectStationResult, ...], GaugeToGaugeDiagnostic | None]:
    """Production wiring for the transect check — real files, real I/O.
    Not covered by unit tests (those exercise `compute_transect_station_
    result` directly); this is exercised by the real-data verify command.

    Returns the per-station results AND the AWS1/AWS4 gauge-to-gauge
    diagnostic (`paired_gauge_diagnostic`, Finding B, Plan 184 T2 round-2
    review), computed HERE — the only point in the call graph that still
    holds both stations' own windowed, retained Pyramid frames before each
    is independently paired to ERA5 inside `compute_transect_station_
    result` (which is why the diagnostic cannot be reconstructed from
    `results` alone: `TransectStationResult.pyramid_hour_equalised_degc`
    is each station's OWN ERA5-paired population, a different population
    per station — reusing it here is exactly the round-2 finding)."""
    orography_ds = load_verified_orography(precip_data_root)
    results: list[TransectStationResult] = []
    pyramid_windows: dict[str, pl.DataFrame] = {}
    for station in TRANSECT_STATIONS:
        coord = StationCoordinate(
            station=station.pyramid_station,
            excel_col=f"pyramid:{station.pyramid_station}",
            lat=station.lat,
            lon=station.lon,
            elev_m=station.elevation_m,
        )
        series = _load_era5_t2m_nearest_series(
            t2m_data_root,
            coord=coord,
            years=range(TRANSECT_START_YEAR, TRANSECT_END_YEAR + 1),
        )
        orography_elev_m = orography_elev_at_nearest_cell(orography_ds, series)
        era5_grid = pl.DataFrame(
            {"timestamp": series.valid_time, "value": series.values}
        ).filter(pl.col("value").is_finite())

        at_result = load_pyramid_lvl1_at_csv(
            pyramid_dir / station.csv_filename, station=station.pyramid_station
        )
        pyramid_window = at_result.retained.filter(
            pl.col("timestamp")
            .dt.year()
            .is_between(TRANSECT_START_YEAR, TRANSECT_END_YEAR)
        ).rename({"value_degc": "value"})
        pyramid_windows[str(station.pyramid_station)] = pyramid_window

        results.append(
            compute_transect_station_result(
                station=station,
                era5_grid=era5_grid,
                orography_elev_m=orography_elev_m,
                pyramid_at=pyramid_window,
            )
        )

    aws1_name = next((name for name in pyramid_windows if "AWS1" in name), None)
    aws4_name = next((name for name in pyramid_windows if "AWS4" in name), None)
    aws1_aws4_diagnostic = (
        paired_gauge_diagnostic(pyramid_windows[aws1_name], pyramid_windows[aws4_name])
        if aws1_name is not None and aws4_name is not None
        else None
    )
    return tuple(results), aws1_aws4_diagnostic


def _attribution_notes(
    results: tuple[TransectStationResult, ...],
    *,
    aws1_aws4_diagnostic: GaugeToGaugeDiagnostic | None = None,
) -> list[str]:
    """Finding 2 (Plan 184 T2 round-1 review) — states two things in the
    report ITSELF, never left for a reader to infer: AWS4's tiny
    correction cannot principally explain its residual (D7: no causal
    attribution; D14: diagnostic, never a refit), and the AWS1/AWS4
    shared-cell gauge comparison is a DIAGNOSTIC only, never a candidate
    replacement rate. Finding C (round-2 review) adds a general D5/D7
    attribution caveat that applies to every row, not just AWS4. Finding B
    (round-2 review) recomputes the AWS1/AWS4 rate on the timestamps BOTH
    gauges retained, via the `aws1_aws4_diagnostic` this function no
    longer derives from `results` itself (each result's `pyramid_hour_
    equalised_degc` is that station's OWN ERA5-paired population, a
    different population per station)."""
    lines: list[str] = ["", "## Notes (Finding 2, Plan 184 T2 review)"]
    lines.append(
        "- **General (D5/D7)**: every residual and discrepancy figure "
        "above is a MAGNITUDE only. One point gauge against one ERA5-Land "
        "cell cannot separate grid/representativeness bias from "
        "lapse-rate error (D5: 'representativeness is characterised, not "
        "decomposed'); nothing in this report attributes a discrepancy to "
        "a specific cause (D7: no causal attribution)."
    )

    aws4 = next((r for r in results if "AWS4" in str(r.pyramid_station)), None)
    if aws4 is not None:
        raw_residual = (
            aws4.era5_raw_hour_equalised_degc - aws4.pyramid_hour_equalised_degc
        )
        lines.append(
            f"- **{aws4.pyramid_station}**: the D14 correction here is only "
            f"{aws4.lapse_correction_degc:+.3f} degC (orography "
            f"{aws4.orography_elev_m:.1f} m vs station "
            f"{aws4.station_elevation_m:.0f} m — the station sits ABOVE its "
            "own cell's orography here, the exception to the module's "
            f"usual sign), while the RAW (uncorrected) residual is already "
            f"{raw_residual:+.3f} degC and the corrected discrepancy is "
            f"{aws4.discrepancy_degc:+.3f} degC. The discrepancy at this "
            "station therefore cannot principally be an error introduced "
            "by the lapse correction — it is ERA5-versus-Pyramid bias at "
            "that grid cell, NOT evidence that 6.5 degC/km is wrong (D7: "
            "no causal attribution; D14: diagnostic, never a refit)."
        )

    aws1 = next((r for r in results if "AWS1" in str(r.pyramid_station)), None)
    if (
        aws1 is not None
        and aws4 is not None
        and abs(aws1.orography_elev_m - aws4.orography_elev_m) < 1e-6
        and aws1_aws4_diagnostic is not None
    ):
        elev_diff_m = abs(aws4.station_elevation_m - aws1.station_elevation_m)
        mean_diff = aws1_aws4_diagnostic.mean_a_degc - aws1_aws4_diagnostic.mean_b_degc
        observed_rate = (
            mean_diff / (elev_diff_m / 1000.0) if elev_diff_m else float("nan")
        )
        lines.append(
            f"- **{aws1.pyramid_station}** and **{aws4.pyramid_station}** "
            "share exactly the same ERA5-Land grid cell and orography "
            f"({aws4.orography_elev_m:.1f} m). On the "
            f"{aws1_aws4_diagnostic.n_common_retained} timestamps BOTH "
            "gauges retained (Finding B, Plan 184 T2 round-2 review — "
            "never each gauge's own independently-sized ERA5-paired "
            f"population), their means differ by {mean_diff:+.3f} degC "
            f"over {elev_diff_m:.0f} m = {observed_rate:+.3f} degC/km — "
            "an OBSERVED rate from two gauges, with no reanalysis "
            "involved. This is a DIAGNOSTIC only, never a candidate "
            "replacement rate (D14 forbids fitting the rate to Pyramid)."
        )
    return lines


def _write_report(
    path: Path,
    results: tuple[TransectStationResult, ...],
    *,
    aws1_aws4_diagnostic: GaugeToGaugeDiagnostic | None = None,
) -> None:
    lines = [
        "# Plan 184 (M-A6) T2 — D14 lapse-rate transect check",
        "",
        f"Standard rate: {STANDARD_LAPSE_RATE_DEGC_PER_KM} degC/km, "
        "never fitted or tuned to this check (D14).",
        f"Window: {TRANSECT_START_YEAR}-{TRANSECT_END_YEAR} (the Pyramid "
        "record ends 2023).",
        "Timestamp convention: PERIOD-ENDING assumed for the whole-hour "
        "label (the hour-of-day-equalised means below are INVARIANT to "
        "that assumption); ERA5 (UTC) is additionally shifted onto "
        "NPT-labelled hours via `params.coloc_dhm_utc_to_npt_hour_offset` "
        "BEFORE pairing (Finding A, Plan 184 T2 round-2 review) — a "
        "rounded reconciliation, residual +-1.75h uncertainty per M-A10.",
        "ERA5 is paired to the timestamps Pyramid actually retained "
        "(Finding 1, Plan 184 T2 round-1 review) BEFORE any averaging — "
        "'ERA5 raw'/'ERA5 corrected'/'Pyramid' below are all computed "
        "over the SAME n(paired) population per station, never "
        "independently; n(ERA5)/n(Pyramid) are context only.",
        "",
        "| Station | Elev (m) | Orography (m) | Correction (degC) | "
        "ERA5 raw | ERA5 corrected | Pyramid | Discrepancy | n(ERA5) | "
        "n(Pyramid) | n(paired) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.pyramid_station} | {r.station_elevation_m:.0f} | "
            f"{r.orography_elev_m:.1f} | {r.lapse_correction_degc:+.2f} | "
            f"{r.era5_raw_hour_equalised_degc:.2f} | "
            f"{r.era5_corrected_hour_equalised_degc:.2f} | "
            f"{r.pyramid_hour_equalised_degc:.2f} | "
            f"{r.discrepancy_degc:+.2f} | {r.n_era5_hours} | "
            f"{r.n_pyramid_retained} | {r.n_paired} |"
        )
    lines.extend(_attribution_notes(results, aws1_aws4_diagnostic=aws1_aws4_diagnostic))
    path.write_text("\n".join(lines) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ma6_lapse_check", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--precip-data-root", type=Path, default=None)
    parser.add_argument("--t2m-data-root", type=Path, default=DEFAULT_T2M_DATA_ROOT)
    parser.add_argument("--pyramid-dir", type=Path, default=DEFAULT_PYRAMID_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    precip_data_root: Path = (
        args.precip_data_root
        if args.precip_data_root is not None
        else Path(os.environ.get("DHM_PRECIP_ERA5_ROOT", str(DEFAULT_DATA_ROOT)))
    )
    try:
        results, aws1_aws4_diagnostic = build_transect_report(
            precip_data_root=precip_data_root,
            t2m_data_root=args.t2m_data_root,
            pyramid_dir=args.pyramid_dir,
        )
    except (Era5AcquisitionError, PyramidLoaderError, OSError) as exc:
        log.error(
            "ma6_lapse_check.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "ma6_lapse_check.md"
    _write_report(report_path, results, aws1_aws4_diagnostic=aws1_aws4_diagnostic)
    json_path = args.out / "ma6_lapse_check.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "pyramid_station": str(r.pyramid_station),
                    "station_elevation_m": r.station_elevation_m,
                    "orography_elev_m": r.orography_elev_m,
                    "lapse_correction_degc": r.lapse_correction_degc,
                    "n_era5_hours": r.n_era5_hours,
                    "n_pyramid_retained": r.n_pyramid_retained,
                    "n_paired": r.n_paired,
                    "era5_raw_hour_equalised_degc": r.era5_raw_hour_equalised_degc,
                    "era5_corrected_hour_equalised_degc": (
                        r.era5_corrected_hour_equalised_degc
                    ),
                    "pyramid_hour_equalised_degc": r.pyramid_hour_equalised_degc,
                    "discrepancy_degc": r.discrepancy_degc,
                }
                for r in results
            ],
            indent=2,
        )
    )
    log.info("ma6_lapse_check.cli.complete", out=str(report_path))
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
