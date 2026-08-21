"""Plan 184 (M-A6) task T3 — the estimands.

D1's three named estimands (matched-hour mean difference, conditional
accumulated difference, wet-hour conditional intensity bias) plus D12's
categorical scores, computed ONLY from T1's typed subsets
(`ma6_pairs.PairedRetainedSubset`) — never from a bare `pl.DataFrame` and
never with an `n` supplied independently of that subset.

**Why every result type stores the `PairedRetainedSubset` object itself,
not a bare `n: int`.** Phase 1 of this track found the same defect four
times: two means formed over populations that were not the same (D2's
"identical masking on both sides" rule broken four separate ways). The
structural answer used throughout `ma6_pairs.py` is that `n` is never a
field a caller can set independently — it is always read off the frame
that actually produced the statistic. This module extends that discipline:
`MatchedHourMeanDifference`, `WetHourConditionalIntensityBias` and
`ConditionalAccumulatedDifference` all store their `PairedRetainedSubset`
(or, for the latter, the per-period partition of one), and `__post_init__`
rejects anything that is not that type. There is no constructor path that
accepts a bare integer as "the n".

**Scope decision, stated explicitly (not implied by the plan):** D1's
"matched-hour mean difference" and "wet-hour conditional intensity bias"
are inherently hourly-differences statistics — aggregating them to a
DAILY or MONTHLY bucket would produce large numbers of small-`n` figures
of exactly the kind D3 warns against ("dominated by representativeness
error at that scale"). They are therefore reported at **JJAS and DJF
scale only** — large, well-populated aggregates, and the two scales this
track already treats as canonical (Rule 1: "exposure counts by season...
reported alongside every result"). "Conditional accumulated difference"
is naturally a sum over any period length, so it is reported at **all
four D3 scales** (JJAS, DJF, DAILY, MONTHLY) — the DAILY/MONTHLY buckets
are also what D12's categorical scores are built from, so no separate
bucketing machinery is needed for the two. Categorical scores are
computed at DAILY/MONTHLY grain only (D12) — a JJAS/DJF-grain categorical
score is refused, not computed (`CategoricalGrainRefusedError`).

**Rule 3 ordering respected throughout:** every function here takes a
`PairedRetainedSubset` that is already T1's common-hourly-mask output
(D2); daily/monthly totals are formed by grouping THAT frame (aggregation
after masking); the 0.2 mm wet floor (`params.wet_threshold_mm_per_h`) is
applied to the AGGREGATED bucket sums in `categorical_scores`, never to
raw hourly values before bucketing; a contingency-table period exists
only if it has at least one jointly-retained hour (a groupby produces no
row for an empty bucket), so every period is jointly valid by
construction.

**Elevation bands (D4a) are Plan 193 D5a's** — `< 700 m` / `700-2,000 m` /
`2,000-3,000 m` / `>= 3,000 m` — and a BAND estimand is the UNWEIGHTED
MEAN of its member stations' own estimand VALUES, never a pool of raw
hours across stations (D5a's own reasoning: a high-retention station
would otherwise dominate the band figure). Each member's own `n` is
still carried so retention variation stays visible (D13: stratify by
retention, never filter on it).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.ma6_pairs import PairedRetainedSubset, subset
from scripts.dhm_precip.numeric import as_float, as_int

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.ma6_pairs import PairedSeries
    from scripts.dhm_precip.params import DhmPrecipParams


class EstimandSubsetTypeError(TypeError):
    """An estimand result was constructed with something other than a real
    `PairedRetainedSubset` (or, for `ConditionalAccumulatedDifference`, a
    `periods` partition that does not reconcile against one) — the
    structural guard against a statistic wearing an `n` that is not its
    own (⛔ this track's recurring failure mode, Plan 184 T1 review)."""


class EmptySubsetError(ValueError):
    """An estimand was asked for over a subset with zero commonly-retained
    rows — no comparison is possible; never silently reported as 0.0 or
    NaN."""


class ScaleNotSupportedError(ValueError):
    """`matched_hour_mean_difference`/`wet_hour_conditional_intensity_bias`
    are scoped to JJAS/DJF only (module docstring's scope decision) — a
    DAILY/MONTHLY request is a caller error, not a silently-honoured one."""


class CategoricalGrainRefusedError(ValueError):
    """D12 — at JJAS/DJF grain 'wet' means >=0.2 mm accumulated over a
    whole season, so both sides are wet in essentially every station-year:
    POD->1.0, CSI->1.0, FAR->0 BY CONSTRUCTION, while totals may differ by
    hundreds of mm. A well-defined number that reads as perfect agreement
    and is analytically vacuous must be refused, not computed."""


class AccumulatedDifferenceReconciliationError(ValueError):
    """`ConditionalAccumulatedDifference.periods` must partition its own
    `subset` exactly — every retained hour accounted for in exactly one
    period, none invented, none dropped. A mismatch means the periods were
    built from a DIFFERENT population than the subset the result claims to
    carry (the exact defect D2 exists to forbid, one level up)."""


class Scale(StrEnum):
    """The D3 scales this module reports at. `DAILY`/`MONTHLY` are D12's
    categorical grain; `JJAS`/`DJF` are the two seasons this track already
    treats as canonical (Rule 1)."""

    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    JJAS = "JJAS"
    DJF = "DJF"


_MAGNITUDE_SCALES = (Scale.JJAS, Scale.DJF)
_GRAIN_SCALES = (Scale.DAILY, Scale.MONTHLY)


class ElevationBand(StrEnum):
    """D4a — Plan 193 D5a's bands, named here (not re-derived): edges are
    declared a priori from the literature D5a cites, never fitted."""

    BELOW_700M = "< 700 m"
    B700_2000M = "700-2,000 m"
    B2000_3000M = "2,000-3,000 m"
    ABOVE_3000M = ">= 3,000 m"


def assign_elevation_band(elev_m: float) -> ElevationBand:
    """D4a edges: <700 / [700,2000) / [2000,3000) / >=3000."""
    if elev_m < 700.0:
        return ElevationBand.BELOW_700M
    if elev_m < 2000.0:
        return ElevationBand.B700_2000M
    if elev_m < 3000.0:
        return ElevationBand.B2000_3000M
    return ElevationBand.ABOVE_3000M


def elevation_bands_by_station(
    coords: Mapping[Station, float],
) -> dict[Station, ElevationBand]:
    """`coords` maps station -> its own elevation in metres (D4a bands the
    STATION's real-world elevation, not the ERA5-Land grid-cell orography
    D7 already treats separately)."""
    return {
        station: assign_elevation_band(elev_m) for station, elev_m in coords.items()
    }


def season_membership_predicate(scale: Scale, params: DhmPrecipParams) -> pl.Expr:
    """JJAS/DJF -> month-membership filter (Rule 1's season exposure
    grouping); DAILY/MONTHLY -> no season restriction, since D12's grain
    is reported over the whole record, then bucketed within
    `conditional_accumulated_difference`."""
    if scale is Scale.JJAS:
        return pl.col("timestamp").dt.month().is_in(params.jjas_months)
    if scale is Scale.DJF:
        return pl.col("timestamp").dt.month().is_in(params.djf_months)
    return pl.lit(True)  # noqa: FBT003 — polars sentinel literal, not a domain bool


def wet_predicate(column: str, params: DhmPrecipParams) -> pl.Expr:
    """D8b's `wet_threshold_side` applied to `column` — reused for both the
    hourly gauge-wet conditioning and the aggregated-bucket wet/dry
    classification (Rule 3: the SAME 0.2 mm floor, applied post-
    aggregation for the latter)."""
    if params.wet_threshold_side == ">=":
        return pl.col(column) >= params.wet_threshold_mm_per_h
    return pl.col(column) > params.wet_threshold_mm_per_h


def scale_subset(
    paired: PairedSeries, *, scale: Scale, params: DhmPrecipParams
) -> PairedRetainedSubset:
    """The one way this module takes a scale slice of a station's
    `PairedSeries` — always through T1's own `subset()`, so the result's
    `n_common_retained` is always T1's, never re-derived here."""
    return subset(paired, season_membership_predicate(scale, params))


def wet_scale_subset(
    paired: PairedSeries, *, scale: Scale, params: DhmPrecipParams
) -> PairedRetainedSubset:
    """As `scale_subset`, additionally restricted to GAUGE-wet hours (the
    conditioning `wet_hour_conditional_intensity_bias` needs) — again via
    T1's `subset()`, so this is its OWN subset, distinct from
    `scale_subset`'s unconditioned one."""
    predicate = season_membership_predicate(scale, params) & wet_predicate(
        "gauge_value_mm", params
    )
    return subset(paired, predicate)


@dataclass(frozen=True, kw_only=True, slots=True)
class MatchedHourMeanDifference:
    """D1's first estimand: mean(gauge - ERA5) over every commonly-
    retained hour in `subset`, wet and dry alike. `n` is `subset`'s own —
    never a series-level or gauge-only count."""

    station: Station
    scale: Scale
    subset: PairedRetainedSubset
    mean_difference_mm_per_h: float

    def __post_init__(self) -> None:
        # Dataclasses do not enforce field types at runtime — this guard
        # is exactly the defence-in-depth the plan requires (a caller
        # cannot smuggle a mismatched or bare-int "n" through by
        # bypassing static typing). pyright sees the field as always
        # PairedRetainedSubset given its declared type, hence the ignore.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.subset, PairedRetainedSubset
        ):
            raise EstimandSubsetTypeError(
                "MatchedHourMeanDifference.subset must be a real "
                f"PairedRetainedSubset, got {type(self.subset)}"
            )
        if self.scale not in _MAGNITUDE_SCALES:
            raise ScaleNotSupportedError(
                f"matched-hour mean difference is scoped to {_MAGNITUDE_SCALES}, "
                f"got {self.scale}"
            )

    @property
    def n(self) -> int:
        return self.subset.n_common_retained


def matched_hour_mean_difference(
    paired_subset: PairedRetainedSubset, *, station: Station, scale: Scale
) -> MatchedHourMeanDifference:
    if paired_subset.n_common_retained == 0:
        raise EmptySubsetError(
            f"{station!r} at {scale}: zero commonly-retained hours — no "
            "matched-hour mean difference is computable"
        )
    diff = as_float(
        paired_subset.frame.select(
            (pl.col("gauge_value_mm") - pl.col("era5_nearest_mm_per_h")).mean()
        ).item()
    )
    return MatchedHourMeanDifference(
        station=station,
        scale=scale,
        subset=paired_subset,
        mean_difference_mm_per_h=diff,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class WetHourConditionalIntensityBias:
    """D1's third estimand: mean(gauge - ERA5) restricted to hours the
    GAUGE recorded as wet (`params.wet_threshold_mm_per_h`) — Rule 1's
    'well-defined under the mask' row, distinct from the unconditioned
    `MatchedHourMeanDifference`. `subset` here is the WET-conditioned
    subset (`wet_scale_subset`'s output), never the unconditioned one."""

    station: Station
    scale: Scale
    subset: PairedRetainedSubset
    mean_intensity_bias_mm_per_h: float

    def __post_init__(self) -> None:
        # Dataclasses do not enforce field types at runtime — this guard
        # is exactly the defence-in-depth the plan requires (a caller
        # cannot smuggle a mismatched or bare-int "n" through by
        # bypassing static typing). pyright sees the field as always
        # PairedRetainedSubset given its declared type, hence the ignore.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.subset, PairedRetainedSubset
        ):
            raise EstimandSubsetTypeError(
                "WetHourConditionalIntensityBias.subset must be a real "
                f"PairedRetainedSubset, got {type(self.subset)}"
            )
        if self.scale not in _MAGNITUDE_SCALES:
            raise ScaleNotSupportedError(
                f"wet-hour conditional intensity bias is scoped to "
                f"{_MAGNITUDE_SCALES}, got {self.scale}"
            )

    @property
    def n(self) -> int:
        return self.subset.n_common_retained


def wet_hour_conditional_intensity_bias(
    wet_paired_subset: PairedRetainedSubset, *, station: Station, scale: Scale
) -> WetHourConditionalIntensityBias:
    if wet_paired_subset.n_common_retained == 0:
        raise EmptySubsetError(
            f"{station!r} at {scale}: zero gauge-wet commonly-retained hours "
            "— no wet-hour conditional intensity bias is computable"
        )
    diff = as_float(
        wet_paired_subset.frame.select(
            (pl.col("gauge_value_mm") - pl.col("era5_nearest_mm_per_h")).mean()
        ).item()
    )
    return WetHourConditionalIntensityBias(
        station=station,
        scale=scale,
        subset=wet_paired_subset,
        mean_intensity_bias_mm_per_h=diff,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class PeriodAccumulation:
    """One DAILY/MONTHLY bucket's (or, for JJAS/DJF, the whole season's)
    accumulated sums — formed by grouping an ALREADY commonly-retained
    frame (Rule 3: aggregation after masking), never a raw hourly frame."""

    period_label: str
    n_hours: int
    gauge_sum_mm: float
    era5_sum_mm: float

    @property
    def difference_mm(self) -> float:
        return self.gauge_sum_mm - self.era5_sum_mm


def _bucket_label_expr(scale: Scale) -> pl.Expr:
    if scale is Scale.DAILY:
        return pl.col("timestamp").dt.strftime("%Y-%m-%d")
    if scale is Scale.MONTHLY:
        return pl.col("timestamp").dt.strftime("%Y-%m")
    raise ScaleNotSupportedError(
        f"{scale} has no DAILY/MONTHLY bucket — JJAS/DJF form a single "
        "whole-season bucket instead"
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ConditionalAccumulatedDifference:
    """D1's second estimand. `periods` must EXACTLY partition `subset`'s
    own retained hours — `__post_init__` reconciles `sum(n_hours)` against
    `subset.n_common_retained` and raises rather than accept a `periods`
    tuple built from a different population than `subset` claims."""

    station: Station
    scale: Scale
    subset: PairedRetainedSubset
    periods: tuple[PeriodAccumulation, ...]

    def __post_init__(self) -> None:
        # Dataclasses do not enforce field types at runtime — this guard
        # is exactly the defence-in-depth the plan requires (a caller
        # cannot smuggle a mismatched or bare-int "n" through by
        # bypassing static typing). pyright sees the field as always
        # PairedRetainedSubset given its declared type, hence the ignore.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.subset, PairedRetainedSubset
        ):
            raise EstimandSubsetTypeError(
                "ConditionalAccumulatedDifference.subset must be a real "
                f"PairedRetainedSubset, got {type(self.subset)}"
            )
        total_period_hours = sum(period.n_hours for period in self.periods)
        if total_period_hours != self.subset.n_common_retained:
            raise AccumulatedDifferenceReconciliationError(
                f"{self.station!r} at {self.scale}: periods sum to "
                f"{total_period_hours} hours, but subset carries "
                f"{self.subset.n_common_retained} — periods do not "
                "partition this subset's own retained hours"
            )

    @property
    def n(self) -> int:
        return self.subset.n_common_retained

    @property
    def n_periods(self) -> int:
        return len(self.periods)

    @property
    def total_difference_mm(self) -> float:
        return sum(period.difference_mm for period in self.periods)

    @property
    def mean_period_difference_mm(self) -> float:
        if self.n_periods == 0:
            raise EmptySubsetError(
                f"{self.station!r} at {self.scale}: zero periods — no mean "
                "period difference is computable"
            )
        return self.total_difference_mm / self.n_periods


def conditional_accumulated_difference(
    paired_subset: PairedRetainedSubset, *, station: Station, scale: Scale
) -> ConditionalAccumulatedDifference:
    if paired_subset.n_common_retained == 0:
        raise EmptySubsetError(
            f"{station!r} at {scale}: zero commonly-retained hours — no "
            "conditional accumulated difference is computable"
        )
    if scale in _GRAIN_SCALES:
        grouped = (
            paired_subset.frame.with_columns(
                _bucket_label_expr(scale).alias("_period_label")
            )
            .group_by("_period_label")
            .agg(
                pl.len().alias("n_hours"),
                pl.col("gauge_value_mm").sum().alias("gauge_sum_mm"),
                pl.col("era5_nearest_mm_per_h").sum().alias("era5_sum_mm"),
            )
            .sort("_period_label")
        )
        periods = tuple(
            PeriodAccumulation(
                period_label=str(row["_period_label"]),
                n_hours=as_int(row["n_hours"]),
                gauge_sum_mm=as_float(row["gauge_sum_mm"]),
                era5_sum_mm=as_float(row["era5_sum_mm"]),
            )
            for row in grouped.iter_rows(named=True)
        )
    else:
        row = paired_subset.frame.select(
            pl.len().alias("n_hours"),
            pl.col("gauge_value_mm").sum().alias("gauge_sum_mm"),
            pl.col("era5_nearest_mm_per_h").sum().alias("era5_sum_mm"),
        ).row(0, named=True)
        periods = (
            PeriodAccumulation(
                period_label=str(scale),
                n_hours=as_int(row["n_hours"]),
                gauge_sum_mm=as_float(row["gauge_sum_mm"]),
                era5_sum_mm=as_float(row["era5_sum_mm"]),
            ),
        )
    return ConditionalAccumulatedDifference(
        station=station, scale=scale, subset=paired_subset, periods=periods
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class CategoricalScores:
    """D12 — DAILY/MONTHLY grain only. `n_periods` is the number of
    jointly-valid periods the contingency table rests on (every period in
    `accumulated.periods` has >=1 jointly-retained hour by construction —
    a groupby never emits an empty bucket); `n_hours` is the underlying
    subset's own retained-hour count."""

    station: Station
    scale: Scale
    n_periods: int
    n_hours: int
    pod: float
    far: float
    csi: float


def categorical_scores(
    accumulated: ConditionalAccumulatedDifference, *, params: DhmPrecipParams
) -> CategoricalScores:
    if accumulated.scale not in _GRAIN_SCALES:
        raise CategoricalGrainRefusedError(
            f"{accumulated.station!r}: categorical scores at "
            f"{accumulated.scale} grain are analytically vacuous by "
            "construction (D12) — refused, not computed. Use DAILY or "
            "MONTHLY grain."
        )
    if accumulated.n_periods == 0:
        raise EmptySubsetError(
            f"{accumulated.station!r} at {accumulated.scale}: zero periods "
            "— no categorical score is computable"
        )
    wet_threshold = params.wet_threshold_mm_per_h
    hits = misses = false_alarms = correct_negatives = 0
    for period in accumulated.periods:
        gauge_wet = period.gauge_sum_mm >= wet_threshold
        era5_wet = period.era5_sum_mm >= wet_threshold
        if gauge_wet and era5_wet:
            hits += 1
        elif gauge_wet and not era5_wet:
            misses += 1
        elif not gauge_wet and era5_wet:
            false_alarms += 1
        else:
            correct_negatives += 1
    pod = hits / (hits + misses) if (hits + misses) > 0 else float("nan")
    far = (
        false_alarms / (hits + false_alarms)
        if (hits + false_alarms) > 0
        else float("nan")
    )
    csi = (
        hits / (hits + misses + false_alarms)
        if (hits + misses + false_alarms) > 0
        else float("nan")
    )
    return CategoricalScores(
        station=accumulated.station,
        scale=accumulated.scale,
        n_periods=accumulated.n_periods,
        n_hours=accumulated.n,
        pod=pod,
        far=far,
        csi=csi,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ElevationBandEstimand:
    """D4a — the band value is the UNWEIGHTED MEAN of its member stations'
    own estimand values (D5a's discipline). `member_ns` carries each
    member's own `n` so retention variation across the band's stations
    stays visible (D13) rather than being hidden behind one pooled count."""

    band: ElevationBand
    scale: Scale
    station_count: int
    member_ns: tuple[int, ...]
    mean_value: float

    def __post_init__(self) -> None:
        if self.station_count != len(self.member_ns):
            raise ValueError(
                f"{self.band}: station_count={self.station_count} does not "
                f"match len(member_ns)={len(self.member_ns)}"
            )
        if self.station_count == 0:
            raise EmptySubsetError(
                f"{self.band} at {self.scale}: zero member stations — no "
                "band estimand is computable"
            )


def _band_estimand(
    band: ElevationBand, scale: Scale, values_and_ns: tuple[tuple[float, int], ...]
) -> ElevationBandEstimand:
    values = tuple(v for v, _n in values_and_ns)
    ns = tuple(n for _v, n in values_and_ns)
    return ElevationBandEstimand(
        band=band,
        scale=scale,
        station_count=len(values_and_ns),
        member_ns=ns,
        mean_value=sum(values) / len(values),
    )


def band_matched_hour_mean_difference(
    band: ElevationBand, results: Mapping[Station, MatchedHourMeanDifference]
) -> ElevationBandEstimand:
    scales = {r.scale for r in results.values()}
    if len(scales) > 1:
        raise ScaleNotSupportedError(
            f"{band}: member results span more than one scale: {scales}"
        )
    scale = next(iter(scales))
    return _band_estimand(
        band, scale, tuple((r.mean_difference_mm_per_h, r.n) for r in results.values())
    )


def band_wet_hour_conditional_intensity_bias(
    band: ElevationBand, results: Mapping[Station, WetHourConditionalIntensityBias]
) -> ElevationBandEstimand:
    scales = {r.scale for r in results.values()}
    if len(scales) > 1:
        raise ScaleNotSupportedError(
            f"{band}: member results span more than one scale: {scales}"
        )
    scale = next(iter(scales))
    return _band_estimand(
        band,
        scale,
        tuple((r.mean_intensity_bias_mm_per_h, r.n) for r in results.values()),
    )


def band_conditional_accumulated_difference(
    band: ElevationBand, results: Mapping[Station, ConditionalAccumulatedDifference]
) -> ElevationBandEstimand:
    scales = {r.scale for r in results.values()}
    if len(scales) > 1:
        raise ScaleNotSupportedError(
            f"{band}: member results span more than one scale: {scales}"
        )
    scale = next(iter(scales))
    return _band_estimand(
        band,
        scale,
        tuple((r.mean_period_difference_mm, r.n) for r in results.values()),
    )
