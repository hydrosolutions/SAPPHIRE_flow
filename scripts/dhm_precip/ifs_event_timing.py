"""M-A11c — per-EVENT timing of the IFS control forecast against the DHM
gauges, scored against a no-skill null, plus the climatological diurnal-phase
offset measured on the SAME pairing.

Everything else in the M-A11 screen measures the season-long MEAN diurnal
cycle. Runoff responds to individual storms, so this module asks a different
question: when a gauge records a large 6-hourly total, does IFS put its own
largest nearby total in the same window?

⛔ THE NULL IS THE POINT. A raw "44 % within ±6 h" is meaningless without a
baseline, because a forecast with the right climatology and zero event skill
already scores well above zero. The null circularly shifts each station-season
IFS series by WHOLE DAYS, WITHIN THAT SEASON: the clock hour of every value is
preserved (4 windows/day on a complete grid), IFS's own diurnal climatology is
preserved exactly, and only the day-to-day correspondence is destroyed. The
reported skill increment is observed − null.

This module replaces three untracked scratch scripts whose numbers M-A11c was
first published from. An independent review (2026-09-03) found four defects in
them; each is fixed HERE by reusing the tracked implementation rather than by
re-deriving it:

1. Gauge windows are `(h−6, h]` — an observation stamped 06Z belongs to the
   window ENDING 06Z. Built by `tigge_gauge_timing._gauge_window_lookup`, the
   tracked implementation of that convention (never a partial sum).
2. Phase uses the tracked `diurnal_phase.harmonic_phase_h` (through
   `tigge_gauge_timing.estimate_station_phase`), so the sign cannot disagree
   with the published M-A6/M-A9/M-A11 estimator. That path also carries M-A6's
   OWN normalisation (each clock hour's total over ITS OWN observation count,
   never raw totals) and the R ≥ 0.05 first-harmonic identifiability gate.
3. ⛔ The lead set here is NOT "D+1". `CONTINUOUS_DAY_LEADS` is the first 24 h
   of ONE 00Z run — four consecutive windows, one initialisation, no
   deduplication. The published D+1 is `LEAD_BANDS["D+1"] == (24, 30, 36, 42)`
   with most-recent-initialisation deduplication
   (`tigge_ifs.dedup_most_recent_init`). The continuous construction is the
   right one for event timing (an unbroken series at a single, short lead),
   but it is a DIFFERENT quantity and must never be quoted as D+1.
4. The event search runs over a FULL, INDEPENDENT IFS series on a dense
   6-hourly grid — never over an inner join to the gap-bearing gauge series,
   which would both drop otherwise-available IFS candidates and break the
   null's clock alignment (a whole-day index roll is only a whole-day CLOCK
   shift on a complete grid).

⚠️ Two reported quantities are convention-dependent, by construction:
the offset IQR tracks the search window (it is censored at ±window), and the
missed fraction depends on the search width and on `--miss-fraction`, an
arbitrary gauge-scale threshold applied to IFS. Only the skill increment is
stable across every window tested. See M-A11c.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable

from sapphire_flow.exceptions import SapphireError
from scripts.dhm_precip.diurnal_phase import BAND_NAMES, band_of
from scripts.dhm_precip.domain_types import Station, StationCoordinate
from scripts.dhm_precip.loader import (
    compute_sha256,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.ma6_pairs import (
    MaskedGaugeSeries,
    load_gauge_masked_population,
)

# `_gauge_window_lookup` and `_hour_of_day_share` are module-private only in
# the sense that no OTHER subsystem may depend on them; the M-A6/M-A11 figure
# family already imports them by name for exactly this reason. Reusing them is
# the whole point — a second implementation of the `(h−6, h]` convention or of
# M-A6's normalisation is how defects 1 and 2 were introduced.
from scripts.dhm_precip.tigge_gauge_timing import (
    MIN_HARMONIC_AMPLITUDE,
    REQUIRED_CLOCK_HOURS,
    StationPhase,
    _gauge_window_lookup,  # pyright: ignore[reportPrivateUsage] — see above
    estimate_station_phase,
)
from scripts.dhm_precip.tigge_ifs import (
    DEFAULT_TIGGE_ROOT,
    LEAD_BANDS,
    TIGGE_MONTHS,
    points_filename,
)

DEFAULT_SEASONS: tuple[int, ...] = (2020, 2021, 2022, 2023, 2024, 2025)

# ⛔ NOT a lead BAND — see defect 3 in the module docstring.
CONTINUOUS_DAY_LEADS: tuple[int, ...] = (6, 12, 18, 24)
CONTINUOUS_DAY_LABEL = "first 24 h of one 00Z run (⛔ not the published D+1)"
DEFAULT_INIT_HOUR = 0

WINDOWS_PER_DAY = 24 // 6

# The scratch scripts' values, kept as defaults so a bare run reproduces the
# published M-A11c table rather than a new quantity.
DEFAULT_SEARCH_WINDOWS_H: tuple[int, ...] = (12, 24, 36, 48)
DEFAULT_EVENT_QUANTILE = 0.90
DEFAULT_DECLUSTER_H = 24
DEFAULT_MISS_FRACTION = 0.5
DEFAULT_MIN_WET_WINDOWS = 30
DEFAULT_MIN_CANDIDATES = 3
# Coprime-ish spread of whole-day shifts, both signs, all well inside a
# 122-day JJAS season.
DEFAULT_NULL_SHIFT_DAYS: tuple[int, ...] = (7, 11, 13, 17, 19, 23, 29, 31, -9, -15)


class EventTimingInputError(SapphireError):
    """No station-season cell survived — the CLI must fail loudly rather than
    print an all-empty table and exit 0."""


@dataclass(frozen=True, kw_only=True, slots=True)
class EventTimingParams:
    search_window_h: int
    event_quantile: float = DEFAULT_EVENT_QUANTILE
    decluster_h: int = DEFAULT_DECLUSTER_H
    miss_fraction: float = DEFAULT_MISS_FRACTION
    min_wet_windows: int = DEFAULT_MIN_WET_WINDOWS
    min_candidates: int = DEFAULT_MIN_CANDIDATES

    def __post_init__(self) -> None:
        if self.search_window_h < 6 or self.search_window_h % 6:
            raise ValueError(
                f"search_window_h {self.search_window_h} must be a positive "
                "multiple of 6 h — the series has no finer resolution"
            )
        if not 0.0 < self.event_quantile < 1.0:
            raise ValueError(f"event_quantile {self.event_quantile} not in (0, 1)")
        if self.decluster_h < 6:
            raise ValueError(f"decluster_h {self.decluster_h} must be ≥ 6 h")
        if not 0.0 <= self.miss_fraction <= 1.0:
            raise ValueError(f"miss_fraction {self.miss_fraction} not in [0, 1]")


@dataclass(frozen=True, kw_only=True, slots=True)
class StationSeasonCell:
    """One station's one JJAS season: its gauge event candidates and its own
    FULL IFS series on a complete 6-hourly grid (the two are never joined)."""

    station: Station
    year: int
    gauge_hour: np.ndarray  # epoch hours of each complete 6-h window's END
    gauge_mm: np.ndarray
    ifs_hour: np.ndarray  # complete, regular 6-h grid; length % 4 == 0
    ifs_mm: np.ndarray  # nan where a window is absent
    event_threshold_mm: float = float("nan")


@dataclass(frozen=True, kw_only=True, slots=True)
class EventTimingResult:
    """⛔ Two denominators. `exact_window` and `within_6h` are fractions of the
    MATCHED events; `missed_fraction` is a fraction of ALL events searched."""

    n_events: int
    n_missed: int
    offsets_h: np.ndarray

    @property
    def n_matched(self) -> int:
        return int(self.offsets_h.size)

    @property
    def exact_window(self) -> float:
        return float((self.offsets_h == 0).mean()) if self.n_matched else float("nan")

    @property
    def within_6h(self) -> float:
        return (
            float((np.abs(self.offsets_h) <= 6).mean())
            if self.n_matched
            else float("nan")
        )

    @property
    def missed_fraction(self) -> float:
        return self.n_missed / self.n_events if self.n_events else float("nan")

    @property
    def median_offset_h(self) -> float:
        return float(np.median(self.offsets_h)) if self.n_matched else float("nan")

    @property
    def iqr_h(self) -> float:
        if not self.n_matched:
            return float("nan")
        q75, q25 = np.percentile(self.offsets_h, [75, 25])
        return float(q75 - q25)


@dataclass(frozen=True, kw_only=True, slots=True)
class FrozenConvention:
    """D3 — every knob that governs which stations, seasons and events are
    retained, serialized so the report proves what produced it rather than
    asserting it in prose. Changing any field here is a reviewed commit
    (⛔ never a CLI habit), and T2's binding test fails if one drifts from
    what § M-A11c states."""

    seasons: tuple[int, ...]
    leads: tuple[int, ...]
    lead_label: str
    init_hour: int
    search_windows_h: tuple[int, ...]
    event_quantile: float
    decluster_h: int
    miss_fraction: float
    min_wet_windows: int
    min_candidates: int
    null_shift_days: tuple[int, ...]
    min_amplitude: float


@dataclass(frozen=True, kw_only=True, slots=True)
class StatisticRow:
    """D1 — observed, null and increment together, never the increment
    alone and never an absolute rate alone. ⛔ Two denominators, carried on
    every row rather than assumed by position: `n_events` is the SEARCHED
    count (the `missed` denominator), `n_matched` the MATCHED count (the
    `exact window` / `within ±6 h` denominator) — see `EventTimingResult`."""

    window_h: int
    statistic: str
    n_events: int
    n_matched: int
    observed: float
    null_mean: float
    null_min: float
    null_max: float

    @property
    def increment(self) -> float:
        return self.observed - self.null_mean


@dataclass(frozen=True, kw_only=True, slots=True)
class EventTimingReport:
    """The complete, machine-checkable output D2 binds § M-A11c to: the
    frozen convention, the SHA-256 of every consumed input (D7), and one row
    per (window, statistic) with observed + null + increment together (D1).
    ⛔ No uncertainty interval field — D4 states none is available."""

    convention: FrozenConvention
    input_digests: dict[str, str]
    rows: tuple[StatisticRow, ...]


def season_bounds(
    year: int, *, months: tuple[int, ...] = TIGGE_MONTHS
) -> tuple[datetime, datetime]:
    """First and last 6-hourly window END of one JJAS season. The first window
    ends 6 h into the first month (nothing ends at 00Z on day 1) and the last
    ends at 00Z on the first of the following month, so the grid is complete
    and its length is a whole number of days — which is what makes the null's
    whole-day roll a pure clock-preserving shift."""
    first, last = min(months), max(months)
    end = (
        datetime(year + 1, 1, 1) if last == 12 else datetime(year, last + 1, 1)  # noqa: DTZ001 — naive, matching the parquet's naive UTC axis
    )
    return datetime(year, first, 1, 6), end  # noqa: DTZ001 — see above


def gauge_season_windows(series: MaskedGaugeSeries, *, year: int) -> pl.DataFrame:
    """Complete 6-h period-ending gauge totals at the four IFS clock positions
    (defect 1). ⛔ Never a partial sum: `_gauge_window_lookup` nulls a window
    that is missing any of its six antecedent hours."""
    start, end = season_bounds(year)
    # The index reaches 6 h BEFORE the first window end, or that window could
    # never accumulate its six antecedent hours and the season would silently
    # lose its first 06Z window for every station.
    index = pl.datetime_range(
        pl.lit(start) - pl.duration(hours=6), pl.lit(end), interval="1h", eager=True
    ).alias("timestamp")
    return (
        _gauge_window_lookup(series.frame, full_index=index)
        .filter(
            pl.col("gauge_window_mm").is_not_null()
            & (pl.col("timestamp") >= pl.lit(start))
            & pl.col("timestamp").dt.hour().is_in(list(REQUIRED_CLOCK_HOURS))
        )
        .rename({"timestamp": "valid_time_utc", "gauge_window_mm": "gauge_mm"})
        .sort("valid_time_utc")
    )


def tigge_series_paths(
    *, tigge_root: Path, seasons: tuple[int, ...]
) -> tuple[Path, ...]:
    """One parquet per season, in season order — the SAME path list
    `load_ifs_series` reads and `consumed_input_digests` hashes (D7). A
    second derivation of this list is how the digests could silently stop
    matching what was actually read."""
    return tuple(tigge_root / "points" / points_filename(year) for year in seasons)


def load_ifs_series(
    *,
    tigge_root: Path,
    seasons: tuple[int, ...],
    leads: tuple[int, ...],
    init_hour: int,
) -> pl.DataFrame:
    """The station series at one initialisation hour and one lead set. ⛔ No
    deduplication: a single init hour and a strictly increasing lead set
    already yield one value per valid time."""
    paths = tigge_series_paths(tigge_root=tigge_root, seasons=seasons)
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise EventTimingInputError(
            "TIGGE station series missing — run `tigge_ifs --year <y>` first: "
            + ", ".join(missing)
        )
    return (
        pl.concat([pl.read_parquet(path) for path in paths])
        .filter(
            pl.col("ending_lead_hours").is_in(list(leads))
            & (pl.col("init_time_utc").dt.hour() == init_hour)
        )
        .select(
            "station",
            pl.col("valid_time_utc").cast(pl.Datetime("us")),
            pl.col("tigge_mm"),
        )
        .sort("station", "valid_time_utc")
    )


def consumed_input_digests(
    *, tigge_root: Path, seasons: tuple[int, ...]
) -> dict[str, str]:
    """D7 — the SHA-256 of every file this run actually reads: the gauge
    workbook (`DHM_PRECIP_XLSX`, already separately pinned to
    `PRODUCTION_SOURCE_SHA256` by `load_long_frame`) plus the six TIGGE
    station-series parquets. Keyed by the path exactly as resolved, so the
    report shows what was addressed, not an assumption about it."""
    workbook = resolve_source_path()
    paths = (workbook, *tigge_series_paths(tigge_root=tigge_root, seasons=seasons))
    return {str(path): compute_sha256(path) for path in paths}


def _epoch_hours(times: pl.Series) -> np.ndarray:
    return (times.dt.epoch("s").to_numpy() // 3600).astype(np.int64)


def ifs_season_grid(station_series: pl.DataFrame, *, year: int) -> pl.DataFrame:
    """The station's IFS series reindexed onto the season's COMPLETE 6-hourly
    grid, `null` where a window is absent (defect 4). Reindexing is what makes
    an index roll a clock-preserving time shift."""
    start, end = season_bounds(year)
    grid = pl.datetime_range(
        pl.lit(start), pl.lit(end), interval="6h", eager=True
    ).alias("valid_time_utc")
    return (
        pl.DataFrame({"valid_time_utc": grid})
        .join(
            station_series.select("valid_time_utc", "tigge_mm"),
            on="valid_time_utc",
            how="left",
        )
        .sort("valid_time_utc")
    )


def whole_day_shift(values: np.ndarray, *, shift_days: int) -> np.ndarray:
    """The null: a circular whole-DAY shift within one season. Only exact on a
    complete grid whose length is a whole number of days — otherwise the
    wrapped tail lands on a different clock hour, which is precisely the
    defect this replaces."""
    if values.size % WINDOWS_PER_DAY:
        raise EventTimingInputError(
            f"IFS season grid of {values.size} windows is not a whole number "
            "of days — a whole-day roll would not preserve the clock"
        )
    return np.roll(values, shift_days * WINDOWS_PER_DAY) if shift_days else values


def build_cells(
    *,
    tigge_root: Path,
    seasons: tuple[int, ...],
    leads: tuple[int, ...],
    init_hour: int,
    params: EventTimingParams,
) -> list[StationSeasonCell]:
    """One cell per (station, season) that has both sides, with the event
    threshold taken from the station's WET gauge windows POOLED over seasons —
    a per-season quantile on ~120 wet windows is far noisier."""
    population = load_gauge_masked_population()
    ifs = load_ifs_series(
        tigge_root=tigge_root, seasons=seasons, leads=leads, init_hour=init_hour
    )
    by_station = dict(ifs.group_by("station"))

    cells: list[StationSeasonCell] = []
    for station, series in sorted(population.by_station.items()):
        station_ifs = by_station.get((str(station),))
        if station_ifs is None:
            continue
        for year in seasons:
            gauge = gauge_season_windows(series, year=year)
            grid = ifs_season_grid(station_ifs, year=year)
            if gauge.height == 0 or grid["tigge_mm"].is_not_null().sum() == 0:
                continue
            cells.append(
                StationSeasonCell(
                    station=station,
                    year=year,
                    gauge_hour=_epoch_hours(gauge["valid_time_utc"]),
                    gauge_mm=gauge["gauge_mm"].to_numpy().astype(float),
                    ifs_hour=_epoch_hours(grid["valid_time_utc"]),
                    ifs_mm=grid["tigge_mm"].to_numpy().astype(float),
                )
            )

    thresholds: dict[Station, float] = {}
    for station in {cell.station for cell in cells}:
        wet = np.concatenate(
            [
                cell.gauge_mm[cell.gauge_mm > 0]
                for cell in cells
                if cell.station == station
            ]
        )
        if wet.size >= params.min_wet_windows:
            thresholds[station] = float(np.quantile(wet, params.event_quantile))
    retained = [
        replace(cell, event_threshold_mm=thresholds[cell.station])
        for cell in cells
        if cell.station in thresholds
    ]
    if not retained:
        raise EventTimingInputError(
            "no (station, season) cell carried both a complete IFS grid and "
            f"≥ {params.min_wet_windows} wet gauge windows"
        )
    return retained


def select_events(cell: StationSeasonCell, *, params: EventTimingParams) -> list[int]:
    """Declustered gauge events: windows at or above the station threshold,
    taken strongest-first, each at least `decluster_h` from every already
    retained event."""
    order = np.argsort(-cell.gauge_mm, kind="stable")
    chosen: list[int] = []
    for index in order:
        if cell.gauge_mm[index] < cell.event_threshold_mm:
            break
        hour = cell.gauge_hour[index]
        if all(
            abs(hour - cell.gauge_hour[other]) >= params.decluster_h for other in chosen
        ):
            chosen.append(int(index))
    return chosen


def measure_events(
    cells: list[StationSeasonCell], *, params: EventTimingParams, shift_days: int = 0
) -> EventTimingResult:
    """`shift_days == 0` is the observed measurement; anything else is one
    draw from the null."""
    offsets: list[float] = []
    n_events = 0
    n_missed = 0
    for cell in cells:
        values = whole_day_shift(cell.ifs_mm, shift_days=shift_days)
        available = np.isfinite(values)
        floor = cell.event_threshold_mm * params.miss_fraction
        for index in select_events(cell, params=params):
            delta = cell.ifs_hour - cell.gauge_hour[index]
            near = (np.abs(delta) <= params.search_window_h) & available
            if int(near.sum()) < params.min_candidates:
                continue
            n_events += 1
            candidates = values[near]
            peak = int(np.argmax(candidates))
            if candidates[peak] < floor:
                n_missed += 1
                continue
            offsets.append(float(delta[near][peak]))
    return EventTimingResult(
        n_events=n_events, n_missed=n_missed, offsets_h=np.asarray(offsets, dtype=float)
    )


def station_phases(
    cells: list[StationSeasonCell], *, min_amplitude: float
) -> dict[Station, StationPhase]:
    """The CLIMATOLOGICAL comparison, on the same pairing, through the tracked
    estimator: M-A6's own normalisation, the tracked first-harmonic phase, and
    the R ≥ `min_amplitude` identifiability gate. Stations that fail a gate are
    absent, never silently folded in at an arbitrary angle."""
    frames: dict[Station, list[pl.DataFrame]] = {}
    for cell in cells:
        paired = (
            pl.DataFrame(
                {
                    "hour": cell.gauge_hour,
                    "gauge_mm": cell.gauge_mm,
                }
            )
            .join(
                pl.DataFrame({"hour": cell.ifs_hour, "tigge_mm": cell.ifs_mm}),
                on="hour",
                how="inner",
            )
            .filter(pl.col("tigge_mm").is_not_null() & pl.col("tigge_mm").is_finite())
            .with_columns(
                pl.from_epoch(pl.col("hour") * 3600, time_unit="s").alias(
                    "valid_time_utc"
                )
            )
        )
        if paired.height:
            frames.setdefault(cell.station, []).append(paired)
    phases: dict[Station, StationPhase] = {}
    for station, parts in frames.items():
        outcome = estimate_station_phase(
            station, pl.concat(parts), min_amplitude=min_amplitude
        )
        if isinstance(outcome, StationPhase):
            phases[station] = outcome
    return phases


def load_elevations() -> dict[Station, float]:
    """Validated through the tracked `StationCoordinate` (range checks, finite
    elevation) rather than read as bare floats."""
    frame = pl.read_csv(resolve_coords_path())
    coords = [
        StationCoordinate(
            station=Station(str(row["station"])),
            excel_col=str(row["excel_col"]),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            elev_m=float(row["elev"]),
        )
        for row in frame.iter_rows(named=True)
    ]
    return {coord.station: coord.elev_m for coord in coords}


def _emit(line: str = "") -> None:
    print(line, flush=True)  # noqa: T201 — the report IS this tool's output


# The three headline rows of M-A11c's table, in its order. The first row also
# carries the window's event counts.
REPORTED_STATISTICS: tuple[tuple[str, Callable[[EventTimingResult], float]], ...] = (
    ("exact window", lambda result: result.exact_window),
    ("within ±6 h", lambda result: result.within_6h),
    ("missed", lambda result: result.missed_fraction),
)


def build_report(
    cells: list[StationSeasonCell],
    *,
    windows_h: tuple[int, ...],
    base: EventTimingParams,
    leads: tuple[int, ...],
    lead_label: str,
    init_hour: int,
    seasons: tuple[int, ...],
    null_shift_days: tuple[int, ...],
    min_amplitude: float,
    input_digests: dict[str, str],
) -> EventTimingReport:
    """Pure — no I/O, no new statistic. Reorganises exactly what
    `measure_events` already returns into the D1/D3/D7 schema: one
    `StatisticRow` per (window, statistic) carrying observed + null +
    increment + both counts, plus the frozen convention and the digests the
    caller resolved (D2 binds both)."""
    convention = FrozenConvention(
        seasons=seasons,
        leads=leads,
        lead_label=lead_label,
        init_hour=init_hour,
        search_windows_h=windows_h,
        event_quantile=base.event_quantile,
        decluster_h=base.decluster_h,
        miss_fraction=base.miss_fraction,
        min_wet_windows=base.min_wet_windows,
        min_candidates=base.min_candidates,
        null_shift_days=null_shift_days,
        min_amplitude=min_amplitude,
    )
    rows: list[StatisticRow] = []
    for window_h in windows_h:
        params = replace(base, search_window_h=window_h)
        observed = measure_events(cells, params=params)
        nulls = [
            measure_events(cells, params=params, shift_days=shift)
            for shift in null_shift_days
        ]
        for label, statistic in REPORTED_STATISTICS:
            draws = [statistic(null) for null in nulls]
            rows.append(
                StatisticRow(
                    window_h=window_h,
                    statistic=label,
                    n_events=observed.n_events,
                    n_matched=observed.n_matched,
                    observed=statistic(observed),
                    null_mean=float(np.mean(draws)),
                    null_min=float(np.min(draws)),
                    null_max=float(np.max(draws)),
                )
            )
    return EventTimingReport(
        convention=convention, input_digests=input_digests, rows=tuple(rows)
    )


def format_report(result: EventTimingReport, *, n_stations: int, n_cells: int) -> str:
    """The printed form of `EventTimingReport` — table plus the convention
    and digest blocks T2's binding test checks against § M-A11c."""
    convention = result.convention
    lines = [
        f"lead set {convention.leads} from {convention.init_hour:02d}Z — "
        f"{convention.lead_label}",
        f"published D+1 for comparison: {LEAD_BANDS['D+1']} + most-recent-init dedup",
        f"{n_stations} stations, {n_cells} station-seasons, "
        f"event = gauge 6-h total ≥ q{convention.event_quantile:.2f} of its "
        f"station's wet windows, declustered {convention.decluster_h} h",
        f"null = whole-day circular shift within season, "
        f"{len(convention.null_shift_days)} draws {convention.null_shift_days}",
        "",
        "frozen convention (D3):",
        f"  seasons={convention.seasons} min_wet_windows={convention.min_wet_windows} "
        f"min_candidates={convention.min_candidates} "
        f"miss_fraction={convention.miss_fraction:g} "
        f"min_amplitude={convention.min_amplitude:g}",
        "",
        "consumed input digests, sha256 (D7):",
    ]
    lines += [
        f"  {path}  {digest}" for path, digest in sorted(result.input_digests.items())
    ]
    lines += [
        "",
        "uncertainty: n = 6 seasons — no reliable inferential interval is "
        "available (D4). The ±window spread below is parameter sensitivity, "
        "not uncertainty.",
        "",
        "  ⛔ 'exact' and '±6 h' are fractions of MATCHED events; 'missed' is a "
        "fraction of ALL events searched.",
        "  ⚠️ IQR is censored at the search window and 'missed' depends on "
        "--miss-fraction; neither is window-independent.",
        "",
        f"{'window':>8s}{'events':>8s}{'matched':>9s}{'statistic':>14s}"
        f"{'observed':>11s}{'null mean (min–max)':>24s}{'increment':>11s}",
    ]
    for window_h in result.convention.search_windows_h:
        window_rows = [row for row in result.rows if row.window_h == window_h]
        for position, row in enumerate(window_rows):
            lead = (
                f"{'±' + str(window_h) + ' h':>8s}{row.n_events:>8d}{row.n_matched:>9d}"
                if position == 0
                else " " * 25
            )
            null_spread = f"{row.null_mean:.3f} ({row.null_min:.3f}–{row.null_max:.3f})"
            lines.append(
                f"{lead}{row.statistic:>14s}{row.observed:>11.3f}{null_spread:>24s}"
                f"{row.increment:>+11.3f}"
            )
    return "\n".join(lines)


def report(
    cells: list[StationSeasonCell],
    *,
    windows_h: tuple[int, ...],
    base: EventTimingParams,
    leads: tuple[int, ...],
    init_hour: int,
    seasons: tuple[int, ...],
    null_shift_days: tuple[int, ...],
    min_amplitude: float,
    input_digests: dict[str, str],
) -> None:
    stations = sorted({str(cell.station) for cell in cells})
    result = build_report(
        cells,
        windows_h=windows_h,
        base=base,
        leads=leads,
        lead_label=CONTINUOUS_DAY_LABEL,
        init_hour=init_hour,
        seasons=seasons,
        null_shift_days=null_shift_days,
        min_amplitude=min_amplitude,
        input_digests=input_digests,
    )
    _emit(format_report(result, n_stations=len(stations), n_cells=len(cells)))
    _emit()

    phases = station_phases(cells, min_amplitude=min_amplitude)
    elevations = load_elevations()
    _emit(
        f"climatological diurnal phase on the SAME pairing "
        f"(M-A6 normalisation, R ≥ {min_amplitude:g} gate): "
        f"{len(phases)}/{len(stations)} stations identified"
    )
    _emit(
        "  ⛔ UNSIGNED displacement only — the reported branches are not "
        "rotation-equivariant, so no physical sign follows (M-A11c)."
    )
    for band, name in enumerate(BAND_NAMES):
        magnitudes = [
            abs(phase.lag_principal_h)
            for station, phase in phases.items()
            if station in elevations and band_of(elevations[station]) == band
        ]
        if not magnitudes:
            _emit(f"  {name:22s} n=0")
            continue
        _emit(
            f"  {name:22s} n={len(magnitudes):2d}  "
            f"median |offset| = {float(np.median(magnitudes)):.2f} h"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M-A11c — per-event IFS timing against DHM gauges, scored "
            "against a whole-day-shift null"
        )
    )
    parser.add_argument("--seasons", type=int, nargs="+", default=list(DEFAULT_SEASONS))
    parser.add_argument(
        "--search-window-h",
        type=int,
        nargs="+",
        default=list(DEFAULT_SEARCH_WINDOWS_H),
        help="± search half-width(s), hours; one report block each",
    )
    parser.add_argument("--event-quantile", type=float, default=DEFAULT_EVENT_QUANTILE)
    parser.add_argument("--decluster-h", type=int, default=DEFAULT_DECLUSTER_H)
    parser.add_argument(
        "--miss-fraction",
        type=float,
        default=DEFAULT_MISS_FRACTION,
        help="IFS peak below this fraction of the event threshold counts as missed",
    )
    parser.add_argument(
        "--leads",
        type=int,
        nargs="+",
        default=list(CONTINUOUS_DAY_LEADS),
        help="ending lead hours; ⛔ a continuous set, NOT a published lead band",
    )
    parser.add_argument("--init-hour", type=int, default=DEFAULT_INIT_HOUR)
    parser.add_argument(
        "--null-shift-days", type=int, nargs="+", default=list(DEFAULT_NULL_SHIFT_DAYS)
    )
    parser.add_argument("--min-amplitude", type=float, default=MIN_HARMONIC_AMPLITUDE)
    parser.add_argument("--tigge-root", type=Path, default=DEFAULT_TIGGE_ROOT)
    args = parser.parse_args()

    windows = tuple(sorted(set(args.search_window_h)))
    base = EventTimingParams(
        search_window_h=windows[0],
        event_quantile=args.event_quantile,
        decluster_h=args.decluster_h,
        miss_fraction=args.miss_fraction,
    )
    seasons = tuple(sorted(set(args.seasons)))
    cells = build_cells(
        tigge_root=args.tigge_root,
        seasons=seasons,
        leads=tuple(args.leads),
        init_hour=args.init_hour,
        params=base,
    )
    input_digests = consumed_input_digests(tigge_root=args.tigge_root, seasons=seasons)
    report(
        cells,
        windows_h=windows,
        base=base,
        leads=tuple(args.leads),
        init_hour=args.init_hour,
        seasons=seasons,
        null_shift_days=tuple(args.null_shift_days),
        min_amplitude=args.min_amplitude,
        input_digests=input_digests,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
