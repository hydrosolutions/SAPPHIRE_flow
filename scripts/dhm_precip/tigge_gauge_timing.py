"""Plan 216 (M-A11) T2 — pair T1's TIGGE-IFS control-forecast series
against the gauges, stratified by lead band (D3), and estimate the same
diurnal PHASE lag M-A6/M-A9 measured for ERA5-Land — using the SAME phase
estimator (D5), imported unmodified from
`data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py`. D5 also
RETIRES that script's season-year bootstrap and 24-bin completeness test
for this screen (both are meaningless on 6-hourly, one-season data); this
module reports point estimates with their own `n` instead (D4's ±3 h
resolution bound dominates any interval a degenerate bootstrap could add).

⛔ Phase only — no magnitude or bias claim (T2 `Out`).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import structlog

from scripts.dhm_precip.domain_types import Station, StationCoordinateTable
from scripts.dhm_precip.loader import load_station_coordinates, resolve_coords_path
from scripts.dhm_precip.ma6_pairs import (
    GaugeMaskedPopulation,
    load_gauge_masked_population,
)
from scripts.dhm_precip.tigge_ifs import LEAD_BANDS

if TYPE_CHECKING:
    from types import ModuleType

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ERA5_TIMING_MODULE_PATH = (
    _REPO_ROOT
    / "data"
    / "dhm_precip"
    / "figures"
    / "era5-timing"
    / "era5_gauge_timing_figure.py"
)
_HOUR_OF_DAY_PERIOD = 24


def _load_era5_timing_module() -> ModuleType:
    """D5 — import the phase estimator FROM THE EXACT FILE the plan names
    (`era5_gauge_timing_figure.py:244-290`), never a re-typed copy. That
    file is gitignored (an M-A6 output artefact, `data/`), so it cannot be
    a normal package import; loaded by path, mirroring how the file itself
    inserts the repo root into `sys.path` to reach `scripts.dhm_precip`."""
    if not _ERA5_TIMING_MODULE_PATH.exists():
        raise FileNotFoundError(
            f"the M-A6 timing figure is not on disk at {_ERA5_TIMING_MODULE_PATH} — "
            "T2 reuses its phase estimator (D5) and cannot proceed without it"
        )
    spec = importlib.util.spec_from_file_location(
        "era5_gauge_timing_figure_for_tigge", _ERA5_TIMING_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"could not build an import spec for {_ERA5_TIMING_MODULE_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# D3/T2 — the four elevation bands + the phase functions, reused unmodified.
_era5_timing = _load_era5_timing_module()
harmonic_phase_h = _era5_timing.harmonic_phase_h
same_day_branch = _era5_timing.same_day_branch
principal_branch = _era5_timing.principal_branch
band_of = _era5_timing.band_of
BAND_NAMES = _era5_timing.BAND_NAMES
npt_label = _era5_timing.npt_label

# D7 — the two timezone readings to report side by side (never a gate).
GAUGE_SHIFT_READINGS: dict[str, int] = {
    "as_labelled_utc": 0,
    "gauge_labels_are_npt": -6,
}


def dedup_most_recent_init(
    series: pl.DataFrame, *, band_steps: tuple[int, ...]
) -> pl.DataFrame:
    """T2 `In` — restrict to one band's exact steps, then keep only the
    MOST RECENT initialisation for every (station, valid_time) that two
    initialisations both reach within this band's own step range (⛔ never
    both, never an average)."""
    banded = series.filter(pl.col("ending_lead_hours").is_in(list(band_steps)))
    return (
        banded.sort(["station", "valid_time_utc", "init_time_utc"])
        .group_by(["station", "valid_time_utc"], maintain_order=True)
        .last()
    )


def _gauge_window_lookup(
    gauge_frame: pl.DataFrame, *, full_index: pl.Series
) -> pl.DataFrame:
    """Every hourly timestamp in `full_index` mapped to the 6-hour,
    period-ending window sum ENDING at that timestamp — `null` whenever any
    of the 6 antecedent hours is absent from `gauge_frame` (M-A3-excluded
    or simply missing). ⛔ never a partial sum (T2 `In`)."""
    idx = pl.DataFrame({"timestamp": full_index})
    gauge_ms = gauge_frame.select(
        pl.col("timestamp").cast(idx.schema["timestamp"]), "value_mm"
    )
    hourly = idx.join(gauge_ms, on="timestamp", how="left").sort("timestamp")
    present = hourly["value_mm"].is_not_null().cast(pl.Int8)
    filled = hourly["value_mm"].fill_null(0.0)
    window_sum = filled.rolling_sum(window_size=6, min_samples=6)
    complete = present.rolling_min(window_size=6, min_samples=6) == 1
    return hourly.with_columns(
        pl.when(complete).then(window_sum).otherwise(None).alias("gauge_window_mm")
    ).select("timestamp", "gauge_window_mm")


@dataclass(frozen=True, kw_only=True, slots=True)
class PhaseEstimate:
    lead_band: str
    elevation_band: str
    gauge_shift_reading: str
    n_windows: int
    n_stations: int
    gauge_peak_hour_utc: int
    tigge_peak_hour_utc: int
    lag_h: float
    lag_principal_h: float


def build_paired_frame(
    tigge_series: pl.DataFrame,
    gauge_population: GaugeMaskedPopulation,
    *,
    lead_band: str,
    gauge_shift_hours: int,
    jjas_months: tuple[int, ...] = (6, 7, 8, 9),
) -> pl.DataFrame:
    """T2 — one (station, valid_time) row per surviving pair: `tigge_mm`
    from the deduplicated band series, `gauge_mm` from the matching
    complete 6-hour gauge window (T2 `In`). D7: `gauge_shift_hours=-6`
    tests "gauge labels are really NPT" — mirrors
    `era5_gauge_timing_figure.analyse`'s `gauge_hour_shift` convention
    exactly: gauge(t) pairs with the model value at valid time (t+shift),
    so for a given TIGGE valid time V the gauge window looked up ends at
    (V - shift)."""
    banded = dedup_most_recent_init(tigge_series, band_steps=LEAD_BANDS[lead_band])
    if banded.height == 0:
        return banded.with_columns(pl.lit(None, dtype=pl.Float64).alias("gauge_mm"))

    full_index = pl.datetime_range(
        banded["valid_time_utc"].min() - pl.duration(hours=12),
        banded["valid_time_utc"].max() + pl.duration(hours=12),
        interval="1h",
        eager=True,
    ).alias("timestamp")

    frames: list[pl.DataFrame] = []
    for station, station_frame in banded.group_by("station"):
        (station_name,) = station
        gauge_frame = gauge_population.by_station.get(Station(str(station_name)))
        if gauge_frame is None:
            continue
        lookup = _gauge_window_lookup(gauge_frame.frame, full_index=full_index)
        window_end = (
            station_frame["valid_time_utc"] - pl.duration(hours=gauge_shift_hours)
        ).alias("timestamp")
        joined = station_frame.with_columns(window_end).join(
            lookup, on="timestamp", how="left"
        )
        frames.append(joined.rename({"gauge_window_mm": "gauge_mm"}))

    paired = (
        pl.concat(frames)
        if frames
        else banded.with_columns(pl.lit(None, dtype=pl.Float64).alias("gauge_mm"))
    )
    return paired.filter(
        pl.col("gauge_mm").is_not_null()
        & pl.col("valid_time_utc").dt.month().is_in(list(jjas_months))
    )


def _hour_of_day_share(paired: pl.DataFrame, *, value_col: str) -> np.ndarray:
    """Build a 24-length hour-of-day share vector with mass only at the
    (<=4) clock positions this band's `valid_time` hours actually occupy —
    zero elsewhere. `harmonic_phase_h`'s first-harmonic transform is a
    weighted sum over `arange(24)`, so a zero-weight bin contributes
    nothing: this is mathematically the SAME estimator as running it on
    the 4 active points directly, just shaped to fit the reused, 24-length
    function UNMODIFIED (D5)."""
    totals = paired.group_by(paired["valid_time_utc"].dt.hour().alias("h")).agg(
        pl.col(value_col).sum().alias("total")
    )
    weights = np.zeros(_HOUR_OF_DAY_PERIOD)
    for row in totals.iter_rows(named=True):
        weights[int(row["h"])] = float(row["total"])
    total = weights.sum()
    return weights / total if total > 0 else weights


def estimate_phase(
    paired: pl.DataFrame,
    *,
    lead_band: str,
    elevation_band: str,
    gauge_shift_reading: str,
    n_stations: int,
) -> PhaseEstimate | None:
    """T2 `Out` — one point estimate (no bootstrap, D5) per (lead band,
    elevation band, timezone reading)."""
    if paired.height == 0:
        return None
    g_share = _hour_of_day_share(paired, value_col="gauge_mm")
    e_share = _hour_of_day_share(paired, value_col="tigge_mm")
    if g_share.sum() == 0 or e_share.sum() == 0:
        return None
    phase_g = harmonic_phase_h(g_share)
    phase_e = harmonic_phase_h(e_share)
    lag_raw = (phase_e - phase_g) % 24.0
    return PhaseEstimate(
        lead_band=lead_band,
        elevation_band=elevation_band,
        gauge_shift_reading=gauge_shift_reading,
        n_windows=paired.height,
        n_stations=n_stations,
        gauge_peak_hour_utc=int(np.argmax(g_share)),
        tigge_peak_hour_utc=int(np.argmax(e_share)),
        lag_h=same_day_branch(lag_raw),
        lag_principal_h=principal_branch(lag_raw),
    )


def run_all_bands(
    tigge_series: pl.DataFrame,
    gauge_population: GaugeMaskedPopulation,
    coords: StationCoordinateTable,
) -> list[PhaseEstimate]:
    """T2 driver — every (lead band x elevation band x D7 reading)
    combination, pooling all stations within each elevation band (M-A9's
    own banding, `BAND_EDGES`/`BAND_NAMES`, reused via `band_of`)."""
    elev_of = {s: c.elev_m for s, c in coords.by_station.items()}
    results: list[PhaseEstimate] = []
    for lead_band in LEAD_BANDS:
        for reading_name, shift in GAUGE_SHIFT_READINGS.items():
            paired = build_paired_frame(
                tigge_series,
                gauge_population,
                lead_band=lead_band,
                gauge_shift_hours=shift,
            )
            if paired.height == 0:
                continue
            paired = paired.with_columns(
                pl.col("station")
                .map_elements(
                    lambda s: band_of(elev_of.get(Station(str(s)), float("nan"))),
                    return_dtype=pl.Int64,
                )
                .alias("elev_band")
            )
            for band_idx, band_name in enumerate(BAND_NAMES):
                sub = paired.filter(pl.col("elev_band") == band_idx)
                if sub.height == 0:
                    continue
                n_stations = sub["station"].n_unique()
                est = estimate_phase(
                    sub,
                    lead_band=lead_band,
                    elevation_band=band_name,
                    gauge_shift_reading=reading_name,
                    n_stations=n_stations,
                )
                if est is not None:
                    results.append(est)
    return results


def write_csv(results: list[PhaseEstimate], path: Path) -> None:
    pl.DataFrame(
        [
            {
                "lead_band": r.lead_band,
                "elevation_band": r.elevation_band,
                "gauge_timezone_reading": r.gauge_shift_reading,
                "n_paired_windows": r.n_windows,
                "n_stations": r.n_stations,
                "gauge_peak_hour_npt": npt_label(r.gauge_peak_hour_utc),
                "tigge_peak_hour_npt": npt_label(r.tigge_peak_hour_utc),
                "lag_hours_same_day_branch": round(r.lag_h, 2),
                "lag_hours_shortest_arc": round(r.lag_principal_h, 2),
                "resolution_bound_h": 3.0,
            }
            for r in results
        ]
    ).write_csv(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tigge-points",
        type=Path,
        default=Path(
            "data/dhm_precip/tigge/points/tigge_station_series_jjas2025.parquet"
        ),
    )
    ap.add_argument("--out", type=Path, default=Path("data/dhm_precip/tigge/points"))
    args = ap.parse_args()

    tigge_series = pl.read_parquet(args.tigge_points)
    gauge_population = load_gauge_masked_population()
    coords_path = resolve_coords_path()
    all_stations = frozenset(gauge_population.by_station.keys())
    coords = load_station_coordinates(coords_path, expected_stations=all_stations)

    results = run_all_bands(tigge_series, gauge_population, coords)
    args.out.mkdir(parents=True, exist_ok=True)
    out_csv = args.out / "tigge_gauge_timing_offsets.csv"
    write_csv(results, out_csv)
    log.info(
        "tigge_gauge_timing.cli.complete", out=str(out_csv), n_estimates=len(results)
    )
    print(
        f"wrote {out_csv}: {len(results)} (lead-band x elev-band x reading) estimates"
    )
    for r in results:
        print(
            f"  {r.lead_band:4s} {r.elevation_band:22s} {r.gauge_shift_reading:20s} "
            f"n={r.n_windows:5d} ({r.n_stations} stations) lag={r.lag_h:+6.2f} h "
            f"(±3 h resolution bound)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
