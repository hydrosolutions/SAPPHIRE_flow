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

    # --- fit-for-purpose QC mask (Plan 173, M-A3, D3/D4/D8) ---
    mam_months: tuple[int, ...] = (3, 4, 5)
    """D8 — pre-monsoon, the conventional Nepali four-season split's third
    named bucket (with `jjas_months`/`djf_months` already declared above and
    `on_months` below)."""
    on_months: tuple[int, ...] = (10, 11)
    """D8 — post-monsoon."""
    qc_mask_range_check_value_min_mm: float = 0.0
    """D4 — a physical-impossibility floor, rejecting the `-9999999` sentinel."""
    qc_mask_range_check_value_max_mm: float = 200.0
    """D4 — comfortably unreachable rather than discriminating (Adhikari et
    al. 2025: >40 mm/h is common in Nepal, not a ceiling); every value above
    this becomes QC_FAILED, so a tight bound would mask real extremes."""
    qc_mask_long_zero_run_min_consecutive_hours: int = 168
    """D3 — measured, not asserted: 7 days catches every documented defect
    (shortest is Biratnagar at 12.2 days) while costing the median station
    only 1.6% of JJAS. Distinct from `minimum_run_duration_hours`, which is
    M-A1's *inventory* threshold — right for reporting candidates,
    catastrophic for wholesale removal."""
    minimum_jjas_retained_fraction: float = 0.50
    """D8 — a station whose JJAS `retained_nonmissing / (retained_nonmissing
    + qc_removed)` falls STRICTLY below this is excluded from M-A6 (equality
    passes). Measured worst case at the 7-day threshold is 0.830 (Lete) — this
    is a safeguard against a pathological case, not a filter expected to fire."""

    # --- Plan 182 (M-A10) — co-located gauge-vs-gauge adjudication ---
    coloc_threshold_ladder_mm: tuple[float, ...] = (0.0, 0.1, 0.2)
    """D7 — the threshold ladder: 'all values' (0.0, a no-op zero-floor),
    then 0.1 mm, then 0.2 mm (matched to Pyramid's native resolution).
    Values are ZEROED, never dropped (D7.2) — a common scalar shift cannot
    move the peak on its own, so a moved peak implicates the specific
    zeroed hours, not the shift itself."""
    coloc_matched_resolution_threshold_mm: float = 0.2
    """D7.1 — the ONLY threshold at which DHM and Pyramid can represent the
    same events; Pyramid cannot record anything below this by construction
    (LSI-Lastem tipping buckets, verified empirically 2026-08-18: 0.00% of
    7,133+9,280 positive JJAS values fall below it)."""
    coloc_dhm_utc_to_npt_hour_offset: int = 6
    """D2 — Pyramid is NPT (UTC+5:45); DHM is UTC. Converting a DHM UTC hour
    bin to its NPT hour-of-day label rounds the 5h45m offset to the nearest
    whole hour (round-half-up: +6). This rounding (up to 15 min) plus the
    Pyramid README's unstated period convention (up to 1h) together make up
    D2's declared ±1.75h alignment uncertainty — well below the 4h/2h D9
    decision boundaries (see `coloc_threshold_coherence_holds`)."""
    coloc_alignment_uncertainty_hours: float = 1.75
    """D2 — the declared total alignment uncertainty (45 min NPT-rounding +
    up to 1h unstated Pyramid period convention). Never a claimed phase
    result finer than this."""
    coloc_min_season_years_for_adequacy: int = 5
    """D5 — fewer season-years cannot on its own establish adequacy for a
    phase verdict, regardless of how narrow the bootstrap spread looks
    (round 2: 3 seasons all peaking at the same hour would otherwise
    produce a false zero-spread 'adequate' reading)."""
    coloc_stationarity_max_circular_diff_hours: float = 4.0
    """D9 gate 0 — disjoint-period (pre-2020 vs 2020+) peak-hour circular
    difference above this fails the adequacy gate."""
    coloc_matched_resolution_max_circular_diff_hours: float = 4.0
    """D9 gate 1 — DHM@matched-resolution vs Pyramid peak-hour circular
    difference above this is INDETERMINATE (sub-threshold counts are not
    the explanation for the discrepancy)."""
    coloc_ablation_supported_min_hours: float = 4.0
    """D9 gate 2 — circular ablation movement at or above this supports H1
    (given gates 0-1 passed)."""
    coloc_ablation_refuted_max_hours: float = 2.0
    """D9 gate 2 — circular ablation movement strictly below this refutes
    H1. Between this and `coloc_ablation_supported_min_hours` is
    INDETERMINATE (ambiguous)."""
    coloc_bootstrap_resamples: int = 1000
    """D5 — number of season-year resamples for the circular bootstrap peak
    spread."""
    coloc_bootstrap_adequate_max_spread_hours: float = 2.0
    """D5 — if the circular bootstrap spread on the peak hour exceeds this
    on the overlap window, only the full record may support a verdict."""
    coloc_pyramid_stationarity_split_year: int = 2020
    """D12 — 'Stationarity is checked on PYRAMID, not DHM.' The disjoint-period
    (pre-2020 vs 2020+) split belongs to the Pyramid record (Lukla 2005-2023,
    Namche 2002-2023), which is the ONLY side that straddles 2020. This is the
    check that licenses D11's non-contemporaneous full-record comparison: a
    Pyramid phase shift across the split would invalidate it. DHM has no
    pre-2020 data at all, so applying the split to DHM made the check vacuous."""
    coloc_dhm_stationarity_split_year: int = 2023
    """D12 — the DHM-side disjoint-period split, reported as ADDITIONAL
    evidence only, NEVER as a substitute for the Pyramid check above.
    **Corrected from the plan's original 2020**
    (`docs/design/dhm-precipitation-vision.md:20`: the authoritative DHM
    source workbook spans 2020-01-01 -> 2025-12-31 in its entirety — there
    is no pre-2020 DHM data to split against, so a 2020 cutoff made `pre`
    always empty). 2023 splits the real 6-year span (2020-2025) into two
    non-empty, roughly balanced disjoint halves (2020-2022 vs 2023-2025).
    Even so, callers must treat a station whose OWN retained history
    doesn't span both sides of this split as
    `disjoint_period_data_sufficient=False` (`coloc_adjudication.py`),
    never let `peak_hour` raise on an empty partition (D9 gate 0 maps
    insufficiency to INDETERMINATE, never a crash)."""

    # --- Plan 174 (M-A5) D8/2d — station-set cardinality tripwire ---
    expected_station_count: int = 26
    """The number of usable DHM stations the ERA5-Land point extraction
    expects from its workbook-derived boundary inventory. Deliberately
    hard-coded and NOT derived: 2d admitted a single inventory source, so
    the loader's "extracted set equals the inventory" check is satisfied by
    an inventory of any size. This pins the count independently, as a
    tripwire on the boundary input. If a delivery legitimately changes size,
    this number is updated here, once, as a visible decision."""

    def __post_init__(self) -> None:
        if self.expected_station_count < 1:
            raise ValueError(
                f"expected_station_count must be >= 1, "
                f"got {self.expected_station_count}"
            )
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
        if (
            self.qc_mask_range_check_value_min_mm
            >= self.qc_mask_range_check_value_max_mm
        ):
            raise ValueError(
                "qc_mask_range_check_value_min_mm must be < "
                "qc_mask_range_check_value_max_mm, got "
                f"{self.qc_mask_range_check_value_min_mm} >= "
                f"{self.qc_mask_range_check_value_max_mm}"
            )
        if self.qc_mask_long_zero_run_min_consecutive_hours < 1:
            raise ValueError("qc_mask_long_zero_run_min_consecutive_hours must be >= 1")
        if not (0.0 <= self.minimum_jjas_retained_fraction <= 1.0):
            raise ValueError("minimum_jjas_retained_fraction must be in [0, 1]")
        # D8 — the four named seasons must exhaustively and disjointly cover
        # the twelve calendar months; a gap or overlap would let an axis row
        # land in zero or two season bins, breaking the reconciliation
        # invariant every downstream accounting table depends on.
        season_months = (
            self.mam_months + self.jjas_months + self.on_months + self.djf_months
        )
        if sorted(season_months) != list(range(1, 13)):
            raise ValueError(
                "mam_months + jjas_months + on_months + djf_months must "
                f"partition 1..12 exactly, got {sorted(season_months)}"
            )
        # --- Plan 182 (M-A10) ---
        if self.coloc_min_season_years_for_adequacy < 1:
            raise ValueError("coloc_min_season_years_for_adequacy must be >= 1")
        if self.coloc_bootstrap_resamples < 1:
            raise ValueError("coloc_bootstrap_resamples must be >= 1")
        ladder = self.coloc_threshold_ladder_mm
        if not ladder:
            raise ValueError("coloc_threshold_ladder_mm must have at least one rung")
        if any(a >= b for a, b in zip(ladder, ladder[1:], strict=False)):
            # `sorted(ladder) == list(ladder)` alone permits duplicate rungs
            # (non-decreasing, not strictly ascending) — checked pairwise
            # with a strict `<` instead so `(0.1, 0.1, 0.2)` is rejected too.
            raise ValueError(
                "coloc_threshold_ladder_mm must be strictly ascending "
                f"(no duplicate rungs), got {ladder}"
            )
        if ladder[0] != 0.0:
            # coloc_adjudication.py treats the ladder's first rung as
            # `dhm_peak_all_hour` — "all values" only means that when the
            # first rung is exactly the zero-floor no-op.
            raise ValueError(
                f"coloc_threshold_ladder_mm must start at 0.0 (the "
                f"all-values rung), got {ladder}"
            )
        if self.coloc_matched_resolution_threshold_mm not in ladder:
            raise ValueError(
                "coloc_matched_resolution_threshold_mm must be a member of "
                "coloc_threshold_ladder_mm"
            )
        if not (
            self.coloc_ablation_refuted_max_hours
            < self.coloc_ablation_supported_min_hours
        ):
            raise ValueError(
                "coloc_ablation_refuted_max_hours must be < "
                "coloc_ablation_supported_min_hours"
            )
        # D9 "Threshold coherence": D2's alignment uncertainty and D5's
        # bootstrap adequacy rule must both sit strictly below the 4h
        # decision boundaries, and the 2h refute-boundary must sit at or
        # above both — no gate decision may turn on a difference smaller
        # than the measurement uncertainty that produced it.
        if not (
            self.coloc_alignment_uncertainty_hours
            < self.coloc_ablation_supported_min_hours
        ):
            raise ValueError(
                "coloc_alignment_uncertainty_hours must be < "
                "coloc_ablation_supported_min_hours (D9 threshold coherence)"
            )
        if not (
            self.coloc_alignment_uncertainty_hours
            < self.coloc_stationarity_max_circular_diff_hours
        ):
            raise ValueError(
                "coloc_alignment_uncertainty_hours must be < "
                "coloc_stationarity_max_circular_diff_hours (D9 threshold coherence)"
            )
        if not (
            self.coloc_alignment_uncertainty_hours
            < self.coloc_matched_resolution_max_circular_diff_hours
        ):
            raise ValueError(
                "coloc_alignment_uncertainty_hours must be < "
                "coloc_matched_resolution_max_circular_diff_hours "
                "(D9 threshold coherence)"
            )
        if not (
            self.coloc_ablation_refuted_max_hours
            >= self.coloc_alignment_uncertainty_hours
        ):
            raise ValueError(
                "coloc_ablation_refuted_max_hours must be >= "
                "coloc_alignment_uncertainty_hours (D9 threshold coherence)"
            )
        if not (
            self.coloc_ablation_refuted_max_hours
            >= self.coloc_bootstrap_adequate_max_spread_hours
        ):
            raise ValueError(
                "coloc_ablation_refuted_max_hours must be >= "
                "coloc_bootstrap_adequate_max_spread_hours (D9 threshold coherence)"
            )


DEFAULT_PARAMS = DhmPrecipParams()
