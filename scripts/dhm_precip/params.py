"""D2 — one frozen parameter object. Every threshold, quantile grid, season and
sentinel value used anywhere in this package lives here; no magic numbers in
statistic functions (scripts/dhm_precip/stats_*.py).

Defaults match the vision's analysis (`docs/design/dhm-precipitation-vision.md`)
where it states a method; where it does not, the default is this plan's own
declared choice (D8b) and is recorded verbatim in `expectations.toml`'s
`method` tables so a mismatch is traceable to a specific field here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

QuantileDefinition = Literal["linear"]
ZeroPolicy = Literal["exclude_zero", "include_zero"]
WetThresholdSide = Literal[">", ">="]
PairwiseMissingPolicy = Literal["pairwise_complete"]
BucketAlignment = Literal["hour_start"]
YearAttribution = Literal["december_belongs_to_following_djf"]
RunPredicate = Literal["stuck_high", "zero"]
OrderingBasis = Literal["timestamp_ascending_per_station"]
AdjacencyRule = Literal["consecutive_calendar_hour"]
GapTreatment = Literal["gap_breaks_run"]
MissingValueBridging = Literal["none_null_breaks_run"]
SeasonBoundary = Literal["jjas_calendar_months_no_cross_year_merge"]


@dataclass(frozen=True, kw_only=True, slots=True)
class DhmPrecipParams:
    """D2 — the one parameter object every statistic function takes."""

    # --- axis / views (D6) ---
    on_grid_minute: int = 0
    """`ON_GRID` = `RAW` restricted to `minute == on_grid_minute` (D6)."""

    # --- sentinels (Findings: Lukla -9999999) ---
    sentinel_value: float = -9999999.0
    sentinel_tolerance: float = 1e-6

    # --- wet threshold (vision D5, method D8b: wet_threshold_side) ---
    wet_threshold_mm_per_h: float = 0.2
    wet_threshold_side: WetThresholdSide = ">="

    # --- quantiles (method D8b: quantile_definition, zero_policy) ---
    quantile_definition: QuantileDefinition = "linear"
    zero_policy: ZeroPolicy = "exclude_zero"
    quantile_grid: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999)
    shape_ratio_numerator_quantile: float = 0.99
    shape_ratio_denominator_quantile: float = 0.5

    # --- modal / sub-threshold mass (method D8b: modal_binning) ---
    modal_bin_width_mm: float = 0.01
    modal_binning: str = (
        "0.01 mm fixed-width bins over non-zero JJAS values, "
        "no wet-threshold restriction (vision quotes a modal NON-ZERO "
        "value, 0.03-0.06 mm/h, below the 0.2 mm/h wet floor)"
    )

    # --- reporting-resolution inference ---
    resolution_epsilon_mm: float = 1e-9
    """A station's non-null cells must all be integer multiples of its inferred
    resolution (0.01 or 0.2 mm) within this tolerance to be classified."""
    group_a_resolution_mm: float = 0.01
    group_b_resolution_mm: float = 0.2

    # --- seasons (vision: JJAS, DJF) ---
    jjas_months: tuple[int, ...] = (6, 7, 8, 9)
    djf_months: tuple[int, ...] = (12, 1, 2)
    year_attribution: YearAttribution = "december_belongs_to_following_djf"
    """A DJF season is named by its January/February calendar year — so
    December 2024 is part of "DJF 2025", the same season as Jan/Feb 2025."""

    # --- pairwise correlation / coherence (method D8b) ---
    pairwise_missing_policy: PairwiseMissingPolicy = "pairwise_complete"
    min_paired_samples: int = 100
    """Threshold for the JJAS-long hourly/3h/daily frequency series."""
    min_diurnal_paired_hours: int = 20
    """A SEPARATE, much lower threshold for the 24-point-per-station diurnal
    profile — reusing `min_paired_samples` (100) here would reject every
    pair (24 < 100) and silently produce no diurnal correlations at all."""
    coherence_frequencies: tuple[str, ...] = ("1h", "3h", "1d")
    distance_bin_edges_km: tuple[float, ...] = (25.0, 100.0, 200.0)

    # --- leave-one-out tail transferability ---
    tail_prediction_tolerance_fraction: float = 0.25
    loo_min_stations: int = 5

    # --- aggregation validity (D8b: bucket_alignment, daily_completeness,
    # period_completeness, denominator) ---
    bucket_alignment: BucketAlignment = "hour_start"
    daily_completeness_min_hours: int = 20
    """Of 24 possible ON_GRID hours; below this a daily total is not formed."""
    three_hourly_completeness_min_hours: int = 3
    """Of 3 possible ON_GRID hours per bucket; below this (i.e. any missing
    hour) a 3-hourly bucket total is not formed — a partial bucket must never
    silently enter a coherence correlation as if it were complete."""
    period_completeness_min_fraction: float = 0.85
    """Minimum retained-hour fraction to form a monthly/annual aggregate. No
    rescaling of incomplete totals (D8b: "no rescaling")."""
    denominator: str = (
        "counts and fractions are always over the stated population "
        "(usable stations, ON_GRID rows, or non_null_observations, per "
        "the expectation's declared grain) — never over the raw 37-column "
        "or 55,379-row totals unless the grain says so"
    )

    # --- candidate runs (D8b: mandatory extra fields; M-A1 inventories only) ---
    minimum_run_duration_hours: int = 12
    run_predicate_stuck_high: RunPredicate = "stuck_high"
    run_predicate_zero: RunPredicate = "zero"
    zero_run_tolerance_mm: float = 1e-9
    """Zero-run adjacency: exact equality to 0.0 (a genuine zero, not "near zero")."""
    stuck_high_tolerance_mm: float = 0.5
    """Stuck-high adjacency: room for a saturated sensor's ADC noise around a
    pinned value (vision: Sindhuli Madhi reports 72.0/72.2/72.4 mm, not one
    bit-exact repeated value) — deliberately looser than `zero_run_tolerance_mm`."""
    stuck_high_min_value_mm: float = 5.0
    """A magnitude floor, not just repetition — without it a long dry-season
    near-zero noise sequence would also chain under `stuck_high_tolerance_mm`
    and wrongly qualify as "stuck-high"."""
    ordering_basis: OrderingBasis = "timestamp_ascending_per_station"
    adjacency_rule: AdjacencyRule = "consecutive_calendar_hour"
    gap_treatment: GapTreatment = "gap_breaks_run"
    missing_value_bridging: MissingValueBridging = "none_null_breaks_run"
    season_boundary: SeasonBoundary = "jjas_calendar_months_no_cross_year_merge"
    merge_distance_hours: int = 0
    """Candidate runs separated by any gap are never merged (0 = no merge)."""
    zero_run_scope: Literal["jjas_only"] = "jjas_only"
    """Vision Findings state zero-run durations "during monsoon" — candidate
    zero runs are detected within JJAS windows only, never across a
    season boundary (season_boundary)."""

    def __post_init__(self) -> None:
        if not (self.wet_threshold_mm_per_h > 0.0):
            raise ValueError(
                f"wet_threshold_mm_per_h must be positive, "
                f"got {self.wet_threshold_mm_per_h}"
            )
        if self.on_grid_minute not in range(60):
            raise ValueError(f"on_grid_minute must be 0-59, got {self.on_grid_minute}")
        if not all(0.0 < q < 1.0 for q in self.quantile_grid):
            raise ValueError(
                f"quantile_grid entries must be in (0, 1): {self.quantile_grid}"
            )
        if self.minimum_run_duration_hours < 1:
            raise ValueError("minimum_run_duration_hours must be >= 1")
        if not (0.0 < self.period_completeness_min_fraction <= 1.0):
            raise ValueError("period_completeness_min_fraction must be in (0, 1]")


DEFAULT_PARAMS = DhmPrecipParams()
