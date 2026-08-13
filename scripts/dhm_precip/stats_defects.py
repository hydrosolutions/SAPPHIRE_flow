"""Task 2d — candidate defect inventory. Inventory only — nothing here
consumes a run/sentinel as a filter (D6: M-A1 builds no exclusion mask).

Run detection follows D8b's declared contract exactly: `ordering_basis`
(timestamp ascending per station), `adjacency_rule` (consecutive calendar
hour), `gap_treatment` (a gap breaks the run), `missing_value_bridging`
(a null breaks the run), `merge_distance` (0 — no merge across gaps).
Candidate-run rows additionally carry `pre_normalisation_candidate=True` (D7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.domain_types import AxisStatus, View

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams


def _tag(frame: pl.DataFrame, *, run: bool = False) -> pl.DataFrame:
    tagged = frame.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )
    if run:
        tagged = tagged.with_columns(pl.lit(True).alias("pre_normalisation_candidate"))
    return tagged


def sentinel_counts(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """Per-station count of cells within `sentinel_tolerance` of `sentinel_value`."""
    hits = on_grid.filter(
        (pl.col("value_mm") - params.sentinel_value).abs() <= params.sentinel_tolerance
    )
    per_station = hits.group_by("station").agg(pl.len().alias("sentinel_count"))
    return _tag(per_station)


def _identical_value_runs(frame: pl.DataFrame, *, tolerance: float) -> pl.DataFrame:
    """Maximal per-station runs of an adjacent-chained near-constant value at
    contiguous ON_GRID calendar hours. `run_length_hours` is the run's
    row count (adjacency_rule + gap_treatment together make this equal to
    elapsed hours, since a gap would have broken the run already).

    `tolerance` is the caller's own choice — zero-run detection wants exact
    equality (0.0 is 0.0), stuck-high detection wants room for a saturated
    sensor's small ADC noise around a pinned value (vision: Sindhuli Madhi
    reports 72.0/72.2/72.4, not one bit-exact repeated value).
    """
    df = frame.sort(["station", "timestamp"])
    contiguous = (
        (pl.col("timestamp").diff().over("station").dt.total_seconds() == 3600)
        .fill_null(False)
        .alias("_contiguous")
    )
    prev_value = pl.col("value_mm").shift(1).over("station")
    same_as_prev = (
        pl.col("value_mm").is_not_null()
        & prev_value.is_not_null()
        & ((pl.col("value_mm") - prev_value).abs() <= tolerance)
    )
    df = df.with_columns(contiguous)
    continues_run = pl.col("_contiguous") & same_as_prev
    df = df.with_columns((~continues_run).alias("_break"))
    df = df.with_columns(pl.col("_break").cum_sum().over("station").alias("_run_id"))
    present = df.filter(pl.col("value_mm").is_not_null())
    runs = present.group_by(["station", "_run_id"]).agg(
        pl.col("timestamp").min().alias("run_start"),
        pl.col("timestamp").max().alias("run_end"),
        pl.len().alias("run_length_hours"),
        pl.col("value_mm").first().alias("run_value_mm"),
        pl.col("value_mm").min().alias("run_min_mm"),
        pl.col("value_mm").max().alias("run_max_mm"),
        pl.col("value_mm").sum().alias("run_total_mm"),
    )
    return runs.drop("_run_id")


def stuck_high_candidate_runs(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Candidate stuck-high blocks: a near-constant value pinned well above
    typical rainfall, no season scope (method: `season_boundary =
    "not season-restricted for stuck-high"`).

    `stuck_high_min_value_mm` is a magnitude floor, not just a
    repeated-value test — without it, a long dry-season near-zero noise
    sequence (0.2/0.4/0.6 mm chained by the same adjacency tolerance) would
    also qualify as "stuck", which is not the defect signature (vision:
    Sindhuli Madhi's block sits at ~72 mm/h, nowhere near typical noise).
    """
    runs = _identical_value_runs(on_grid, tolerance=params.stuck_high_tolerance_mm)
    candidates = runs.filter(
        (pl.col("run_value_mm") >= params.stuck_high_min_value_mm)
        & (pl.col("run_length_hours") >= params.minimum_run_duration_hours)
    )
    return _tag(candidates, run=True)


def candidate_zero_runs(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """Candidate false-zero runs, JJAS-scoped (`zero_run_scope`), duration in
    days. Exact-zero equality (`zero_run_tolerance_mm`) — a genuine zero,
    not "near zero"."""
    jjas = on_grid.filter(pl.col("timestamp").dt.month().is_in(params.jjas_months))
    runs = _identical_value_runs(jjas, tolerance=params.zero_run_tolerance_mm)
    candidates = runs.filter(
        (pl.col("run_value_mm") == 0.0)
        & (pl.col("run_length_hours") >= params.minimum_run_duration_hours)
    ).with_columns((pl.col("run_length_hours") / 24.0).alias("run_length_days"))
    return _tag(candidates, run=True)


def daily_totals(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """Per-station-per-day total, formed only when at least
    `daily_completeness_min_hours` ON_GRID hours are present that day."""
    with_day = on_grid.with_columns(pl.col("timestamp").dt.date().alias("day"))
    per_day = with_day.group_by(["station", "day"]).agg(
        pl.col("value_mm").sum().alias("daily_total_mm"),
        pl.col("value_mm").is_not_null().sum().alias("hours_present"),
    )
    complete = per_day.filter(
        pl.col("hours_present") >= params.daily_completeness_min_hours
    )
    return _tag(complete)


def annual_totals(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """Per-station-per-year total (Findings figures — no minimum applied)."""
    with_year = on_grid.with_columns(pl.col("timestamp").dt.year().alias("year"))
    per_year = with_year.group_by(["station", "year"]).agg(
        pl.col("value_mm").sum().alias("annual_total_mm"),
        pl.col("value_mm").is_not_null().sum().alias("hours_present"),
    )
    return _tag(per_year)
