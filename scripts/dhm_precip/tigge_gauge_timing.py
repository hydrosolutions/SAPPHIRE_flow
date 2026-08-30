"""Plan 216 (M-A11) T2 — pair T1's TIGGE-IFS control-forecast series
against the gauges, stratified by lead band (D3), and estimate the same
diurnal PHASE lag M-A6/M-A9 measured for ERA5-Land — using the SAME phase
estimator (D5), now a TRACKED module (`diurnal_phase.py`) shared with
`era5_gauge_timing_figure.py` rather than a dynamic, gitignored-file
import (a fresh checkout — including CI — never has `data/`). D5 also
RETIRES that script's season-year bootstrap and 24-bin completeness test
for this screen (both are meaningless on 6-hourly, one-season data); this
module reports point estimates with their own `n` instead (D4's ±3 h
resolution bound dominates any interval a degenerate bootstrap could add).

D5 also pins the STATION-EQUAL band statistic: each station's own diurnal
cycle is normalised INDEPENDENTLY (so it sums to 1 regardless of how many
windows it contributed), its own lag computed from that; the reported band
figure is the MEDIAN across stations — never a pool of raw mass summed
across stations first, which would let a high-count station dominate the
estimate. This mirrors `era5_gauge_timing_figure.analyse`'s per-station
`pooled()` normalisation and its band-median reporting
(`run_sensitivities.medians()`'s `np.median`).

⛔ Phase only — no magnitude or bias claim (T2 `Out`).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
import polars as pl
import structlog

from sapphire_flow.exceptions import SapphireError
from scripts.dhm_precip.diurnal_phase import (
    BAND_NAMES,
    HOUR_OF_DAY_PERIOD,
    band_of,
    harmonic_phase_h,
    npt_label,
    principal_branch,
    same_day_branch,
)
from scripts.dhm_precip.domain_types import Station, StationCoordinateTable
from scripts.dhm_precip.loader import load_station_coordinates, resolve_coords_path
from scripts.dhm_precip.ma6_pairs import (
    GaugeMaskedPopulation,
    load_gauge_masked_population,
)
from scripts.dhm_precip.tigge_ifs import (
    LEAD_BANDS,
    TIGGE_MONTHS,
    TIGGE_YEAR,
    write_tigge_attribution,
)

log = structlog.get_logger(__name__)

# D7 — the two timezone readings to report side by side (never a gate).
# The value is a CIRCULAR SHIFT (in whole hours) applied to an already-built
# station cycle, NOT a re-pairing offset: D7 says an NPT reading "shifts every
# offset uniformly +6 h while leaving the between-band contrast invariant", and
# that is only true if BOTH readings are computed from the SAME contributing
# windows. Rebuilding the pairing at a different window alignment changes which
# windows survive (and their `n`), so the offsets would NOT shift uniformly —
# which is exactly the D7 contradiction an earlier revision shipped.
GAUGE_SHIFT_READINGS: dict[str, int] = {
    "as_labelled_utc": 0,
    "gauge_labels_are_npt": -6,
}

# The four 6-hourly clock positions every LEAD_BANDS band covers by
# construction (a band is a COMPLEMENTARY STEP SET, T2 `In`). A station whose
# surviving pairs do not occupy all four cannot support a diurnal fit at all,
# so it must never contribute to a band statistic.
REQUIRED_CLOCK_HOURS: tuple[int, ...] = (0, 6, 12, 18)


class PhaseStatus(Enum):
    """Why a (lead band x elevation band x reading) cell does or does not
    carry a lag. Reported explicitly for EVERY cell — a silently missing
    row cannot be told apart from a cell nobody computed."""

    OK = "ok"
    NO_PAIRED_WINDOWS = "no_paired_windows"
    INSUFFICIENT_CLOCK_COVERAGE = "insufficient_clock_coverage"


class TiggeTimingMatrixError(SapphireError):
    """Not one (lead band x elevation band x reading) cell produced a lag —
    the CLI must fail loudly rather than write an all-empty matrix and
    exit 0."""


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
    status: PhaseStatus
    n_windows: int
    n_station_days: int
    n_stations: int
    gauge_peak_hour_utc: int | None
    tigge_peak_hour_utc: int | None
    lag_h: float  # nan unless `status is PhaseStatus.OK`
    lag_principal_h: float


def build_paired_frame(
    tigge_series: pl.DataFrame,
    gauge_population: GaugeMaskedPopulation,
    *,
    lead_band: str,
    jjas_year: int = TIGGE_YEAR,
    jjas_months: tuple[int, ...] = TIGGE_MONTHS,
) -> pl.DataFrame:
    """T2 — one (station, valid_time) row per surviving pair: `tigge_mm`
    from the deduplicated band series, `gauge_mm` from the matching
    complete 6-hour gauge window ENDING at that valid time (T2 `In`).
    ⛔ There is exactly ONE pairing, shared by both D7 readings: the
    alternate "gauge labels are really NPT" reading is a circular shift of
    the resulting cycle (`GAUGE_SHIFT_READINGS`), never a second pairing at
    a different window alignment — which would change the surviving windows
    and their `n`, so the offsets would not shift uniformly as D7 requires.
    D2 — filtered on BOTH `jjas_year` and `jjas_months`
    (never month alone): a `tigge_series` that happened to carry a
    different year's data must be dropped here, never silently reported
    as "JJAS `TIGGE_YEAR`"."""
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
        # The parquet carries ns-precision timestamps while the hourly
        # index is built at polars' own default precision — cast to the
        # lookup's dtype so the join key matches whatever that is.
        window_end = (
            station_frame["valid_time_utc"]
            .cast(lookup.schema["timestamp"])
            .alias("timestamp")
        )
        joined = station_frame.with_columns(window_end).join(
            lookup, on="timestamp", how="left"
        )
        frames.append(joined.rename({"gauge_window_mm": "gauge_mm"}))

    paired = (
        pl.concat(frames)
        if frames
        else banded.with_columns(pl.lit(None, dtype=pl.Float64).alias("gauge_mm"))
    )
    return restrict_to_pinned_season(
        paired, jjas_year=jjas_year, jjas_months=jjas_months
    )


def restrict_to_pinned_season(
    paired: pl.DataFrame, *, jjas_year: int, jjas_months: tuple[int, ...]
) -> pl.DataFrame:
    """D2 — keep only rows with a complete, FINITE pairing on BOTH sides
    AND `valid_time` inside the ONE pinned season (`jjas_year`,
    `jjas_months`) — filtering on YEAR as well as month closes the gap
    where a `tigge_series` that happened to carry a different year's JJAS
    data would pass a month-only filter and be silently reported as "JJAS
    `jjas_year`".

    This is the PAIRED-STATISTIC BOUNDARY. T1 carries gaps rather than
    filling them, so a masked raw grid point reaches here as a NaN
    `tigge_mm` — which is neither "materially negative" nor removed by
    `np.clip`, and would otherwise survive normalisation, the phase, the
    median AND the station-day count, poisoning a whole cell while every
    `n` still looked healthy. A non-finite value on EITHER side is not an
    observation, so it is dropped here and excluded from every `n`."""
    return paired.filter(
        pl.col("gauge_mm").is_not_null()
        & pl.col("gauge_mm").is_finite()
        & pl.col("tigge_mm").is_not_null()
        & pl.col("tigge_mm").is_finite()
        & (pl.col("valid_time_utc").dt.year() == jjas_year)
        & pl.col("valid_time_utc").dt.month().is_in(list(jjas_months))
    )


def _hour_of_day_share(frame: pl.DataFrame, *, value_col: str) -> np.ndarray:
    """Build a 24-length hour-of-day share vector using M-A6's OWN
    normalisation (D5, "same normalisation"): aggregate the SUM and the
    OBSERVATION COUNT per clock hour, form the HOURLY MEAN `sum / count`,
    and normalise THAT — exactly `era5_gauge_timing_figure.analyse`'s
    `hourly_means()` (`sum`, `sum`, `len`) followed by
    `pooled()`'s `g / np.maximum(n, 1)` and `g_share = 100 * g_mean /
    g_mean.sum()`.

    ⛔ Normalising RAW TOTALS instead is a DIFFERENT ESTIMATOR, not a
    scale factor: with gappy coverage the clock positions that retained
    more windows get more weight purely because they retained more
    windows, which moves the phase. (The overall SCALE does cancel in
    `harmonic_phase_h`'s angle — summing to 1 vs 100 is the same
    estimator — but the per-bin count does not.)

    Mass only at the (<=4) clock positions this band's `valid_time` hours
    actually occupy — zero elsewhere. `harmonic_phase_h`'s first-harmonic
    transform is a weighted sum over `arange(24)`, so a zero-weight bin
    contributes nothing: this is mathematically the SAME estimator as
    running it on the <=4 active points directly, just shaped to fit the
    reused, 24-length function UNMODIFIED (D5). Call ONCE PER STATION —
    never on a frame pooling multiple stations, which would let a
    high-count station dominate the shared 24-bin total."""
    agg = frame.group_by(frame["valid_time_utc"].dt.hour().alias("h")).agg(
        pl.col(value_col).sum().alias("total"), pl.len().alias("n")
    )
    totals = np.zeros(HOUR_OF_DAY_PERIOD)
    counts = np.zeros(HOUR_OF_DAY_PERIOD)
    for row in agg.iter_rows(named=True):
        totals[int(row["h"])] = float(row["total"])
        counts[int(row["h"])] = float(row["n"])
    means = totals / np.maximum(counts, 1.0)
    total = means.sum()
    return means / total if total > 0 else means


def _clock_hours_covered(frame: pl.DataFrame) -> set[int]:
    return {int(h) for h in frame["valid_time_utc"].dt.hour().unique().to_list()}


@dataclass(frozen=True, kw_only=True, slots=True)
class StationPhase:
    """One station's OWN normalised diurnal cycle and lag (D5) — the unit
    every band statistic is built from, never a pooled cross-station sum."""

    station: Station
    n_windows: int
    gauge_share: np.ndarray
    tigge_share: np.ndarray
    gauge_peak_hour_utc: int
    tigge_peak_hour_utc: int
    lag_h: float
    lag_principal_h: float


def estimate_station_phase(
    station: Station, station_frame: pl.DataFrame, *, gauge_hour_shift: int = 0
) -> StationPhase | None:
    """T2 — one station's phase estimate, from its OWN normalised cycle
    only (D5's per-station normalisation). Returns `None` when the station
    cannot support a diurnal fit: its surviving pairs must occupy ALL of
    `REQUIRED_CLOCK_HOURS` (one to three of the four positions cannot fit a
    cycle, however much mass they carry), and both sides must carry mass.

    D7: `gauge_hour_shift` CIRCULARLY ROTATES this station's already-built
    gauge cycle — the "gauge labels are really NPT" reading, computed from
    the SAME windows as the as-labelled reading, so it shifts the lag
    uniformly by exactly -`gauge_hour_shift` hours and leaves every `n`
    identical."""
    if not set(REQUIRED_CLOCK_HOURS) <= _clock_hours_covered(station_frame):
        return None
    g_share = np.roll(
        _hour_of_day_share(station_frame, value_col="gauge_mm"), gauge_hour_shift
    )
    e_share = _hour_of_day_share(station_frame, value_col="tigge_mm")
    if g_share.sum() == 0 or e_share.sum() == 0:
        return None
    phase_g = harmonic_phase_h(g_share)
    phase_e = harmonic_phase_h(e_share)
    lag_raw = (phase_e - phase_g) % 24.0
    return StationPhase(
        station=station,
        n_windows=station_frame.height,
        gauge_share=g_share,
        tigge_share=e_share,
        gauge_peak_hour_utc=int(np.argmax(g_share)),
        tigge_peak_hour_utc=int(np.argmax(e_share)),
        lag_h=same_day_branch(lag_raw),
        lag_principal_h=principal_branch(lag_raw),
    )


def station_day_count(frame: pl.DataFrame) -> int:
    """T2 — the number of distinct (station, valid_date) pairs retained,
    the exposure M-A9's `n_common_hours`/`n_season_years` play the same
    role for: NOT the window count (a station can contribute more than
    one window per calendar day across lead bands or repeated valid
    hours), and not conflated with it."""
    if frame.height == 0:
        return 0
    return (
        frame.select("station", pl.col("valid_time_utc").dt.date().alias("date"))
        .unique()
        .height
    )


def estimate_phase(
    paired: pl.DataFrame,
    *,
    lead_band: str,
    elevation_band: str,
    gauge_shift_reading: str,
    gauge_hour_shift: int = 0,
) -> PhaseEstimate:
    """T2 `Out` — D5 station-equal aggregate for one (lead band, elevation
    band, timezone reading): each station in `paired` gets its OWN
    normalised cycle and lag (`estimate_station_phase`); the reported
    figure is the MEDIAN across those per-station lags — exactly M-A9's
    band statistic (`era5_gauge_timing_figure.medians()`), never a pooled
    sum of raw mass across stations (which would let whichever station
    contributed the most windows dominate).

    ALWAYS returns a cell. A combination no station can support is
    reported EXPLICITLY (`PhaseStatus`, `n_stations=0`, `lag = nan`) rather
    than dropped — a missing row and an uncomputed row are
    indistinguishable to a reader."""
    station_phases = [
        phase
        for station, station_frame in paired.group_by("station")
        if (
            phase := estimate_station_phase(
                Station(str(station[0])),
                station_frame,
                gauge_hour_shift=gauge_hour_shift,
            )
        )
        is not None
    ]
    if not station_phases:
        return PhaseEstimate(
            lead_band=lead_band,
            elevation_band=elevation_band,
            gauge_shift_reading=gauge_shift_reading,
            status=(
                PhaseStatus.NO_PAIRED_WINDOWS
                if paired.height == 0
                else PhaseStatus.INSUFFICIENT_CLOCK_COVERAGE
            ),
            n_windows=paired.height,
            n_station_days=station_day_count(paired),
            n_stations=0,
            gauge_peak_hour_utc=None,
            tigge_peak_hour_utc=None,
            lag_h=float("nan"),
            lag_principal_h=float("nan"),
        )

    band_gauge_share = np.median(
        np.vstack([sp.gauge_share for sp in station_phases]), axis=0
    )
    band_tigge_share = np.median(
        np.vstack([sp.tigge_share for sp in station_phases]), axis=0
    )
    # `n` counts only the windows that actually fed the estimate: a station
    # the coverage gate rejected contributed nothing, so its rows must not
    # inflate the exposure reported beside the lag.
    contributing_rows = paired.filter(
        pl.col("station").is_in([str(sp.station) for sp in station_phases])
    )
    return PhaseEstimate(
        lead_band=lead_band,
        elevation_band=elevation_band,
        gauge_shift_reading=gauge_shift_reading,
        status=PhaseStatus.OK,
        n_windows=contributing_rows.height,
        n_station_days=station_day_count(contributing_rows),
        n_stations=len(station_phases),
        gauge_peak_hour_utc=int(np.argmax(band_gauge_share)),
        tigge_peak_hour_utc=int(np.argmax(band_tigge_share)),
        lag_h=float(np.median([sp.lag_h for sp in station_phases])),
        lag_principal_h=float(np.median([sp.lag_principal_h for sp in station_phases])),
    )


def run_all_bands(
    tigge_series: pl.DataFrame,
    gauge_population: GaugeMaskedPopulation,
    coords: StationCoordinateTable,
) -> list[PhaseEstimate]:
    """T2 driver — the COMPLETE (lead band x elevation band x D7 reading)
    matrix, computed station-equal (D5) within each elevation band (M-A9's
    own banding, `BAND_EDGES`/`BAND_NAMES`, reused via `band_of`). Every
    combination yields exactly one row, including the ones no station can
    support — never a silently short matrix.

    The pairing is built ONCE per lead band and reused by both D7 readings
    (`GAUGE_SHIFT_READINGS` rotates the built cycle), so the two readings
    are computed from the identical windows."""
    elev_of = {s: c.elev_m for s, c in coords.by_station.items()}
    results: list[PhaseEstimate] = []
    for lead_band in LEAD_BANDS:
        paired = build_paired_frame(
            tigge_series, gauge_population, lead_band=lead_band
        ).with_columns(
            pl.col("station")
            .map_elements(
                lambda s: band_of(elev_of.get(Station(str(s)), float("nan"))),
                return_dtype=pl.Int64,
            )
            .alias("elev_band")
        )
        for reading_name, shift in GAUGE_SHIFT_READINGS.items():
            for band_idx, band_name in enumerate(BAND_NAMES):
                results.append(
                    estimate_phase(
                        paired.filter(pl.col("elev_band") == band_idx),
                        lead_band=lead_band,
                        elevation_band=band_name,
                        gauge_shift_reading=reading_name,
                        gauge_hour_shift=shift,
                    )
                )
    return results


def write_csv(results: list[PhaseEstimate], path: Path) -> None:
    pl.DataFrame(
        [
            {
                "lead_band": r.lead_band,
                "elevation_band": r.elevation_band,
                "gauge_timezone_reading": r.gauge_shift_reading,
                "status": r.status.value,
                "n_paired_windows": r.n_windows,
                "n_station_days": r.n_station_days,
                "n_stations": r.n_stations,
                "gauge_peak_hour_npt": (
                    npt_label(r.gauge_peak_hour_utc)
                    if r.gauge_peak_hour_utc is not None
                    else ""
                ),
                "tigge_peak_hour_npt": (
                    npt_label(r.tigge_peak_hour_utc)
                    if r.tigge_peak_hour_utc is not None
                    else ""
                ),
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
    if not any(r.status is PhaseStatus.OK for r in results):
        raise TiggeTimingMatrixError(
            f"none of the {len(results)} (lead band x elevation band x reading) "
            "cells produced a lag — refusing to write an all-empty matrix"
        )
    args.out.mkdir(parents=True, exist_ok=True)
    out_csv = args.out / "tigge_gauge_timing_offsets.csv"
    write_csv(results, out_csv)
    attribution_path = write_tigge_attribution(out_csv)
    log.info(
        "tigge_gauge_timing.cli.complete", out=str(out_csv), n_estimates=len(results)
    )
    print(
        f"wrote {out_csv}: {len(results)} (lead-band x elev-band x reading) estimates"
    )
    print(f"wrote {attribution_path}")
    for r in results:
        print(
            f"  {r.lead_band:4s} {r.elevation_band:22s} {r.gauge_shift_reading:20s} "
            f"{r.status.value:28s} n={r.n_windows:5d} windows, "
            f"{r.n_station_days:4d} station-days "
            f"({r.n_stations} stations) lag={r.lag_h:+6.2f} h "
            f"(±3 h resolution bound)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
