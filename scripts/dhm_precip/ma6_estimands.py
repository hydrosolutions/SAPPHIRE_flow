"""Plan 184 (M-A6) task T3 — the estimands.

D1's three named estimands (matched-hour mean difference, conditional
accumulated difference, wet-hour conditional intensity bias) plus D12's
categorical scores, computed ONLY from T1's typed subsets
(`ma6_pairs.PairedRetainedSubset`) — never from a bare `pl.DataFrame` and
never with an `n` supplied independently of that subset.

**Why every result type stores ONLY the `PairedRetainedSubset` object
itself — never the reported value, never `n`, never `periods`, as
independently-suppliable constructor fields.** Phase 1 of this track found
the same defect four times: two means formed over populations that were
not the same (D2's "identical masking on both sides" rule broken four
separate ways). Phase 2's T3 independent review (2026-08-21) found it a
FIFTH time, inside this very module: `mean_difference_mm_per_h`,
`mean_intensity_bias_mm_per_h` and `periods` were plain constructor
fields, so a value (or bucket partition) computed from one subset could be
attached to a DIFFERENT, equal-sized subset and pass every existing check
(type, scale, row-count reconciliation) undetected — the reconciliation
check only ever compared *counts*, never *identity*. The structural fix —
the same move `ma6_pairs.py` already uses for `n_common_retained` — is
that none of these are stored fields at all: `MatchedHourMeanDifference.
mean_difference_mm_per_h`, `WetHourConditionalIntensityBias.
mean_intensity_bias_mm_per_h` and `ConditionalAccumulatedDifference.
periods` are all `@property`s computed live from `self.subset` (and, for
`periods`, `self.scale`) via the SAME construction path every time. There
is no constructor argument through which a caller could attach a value or
partition computed elsewhere — not "checked and rejected", genuinely
absent from the signature. `n` was already such a property (T1's own
discipline); this extends it to the statistic itself.
`__post_init__` still rejects any `subset` that is not a real
`PairedRetainedSubset`.

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

**Owner decision, Plan 184 T3 follow-up: BOTH wet-hour conditionings are
reported, never gauge-alone only.** D1 never defined what "wet-hour"
conditions on. `WetHourConditionalIntensityBias` (unchanged, still
gauge-alone: hours the GAUGE calls wet, ERA5 wet or dry) answers "when
the gauge says it rained, how far off is ERA5's intensity?" but mixes in
ERA5's own detection failure. `JointWetHourConditionalIntensityBias`
(new) restricts to hours BOTH sides call wet, at the cost of
conditioning on ERA5's own wet/dry behaviour too (doubly conditional;
see `JointWetConditionality`).

**The honest framing (Plan 184 phase 2 independent review, 2026-08-24,
Finding 4 — this paragraph replaces an earlier, self-contradictory
draft):** `joint` and `gauge_alone` are two DIFFERENT, NESTED
populations — `joint` a subset of `gauge_alone`'s retained hours,
EQUALITY PERMITTED (a jointly-wet hour is always a gauge-wet hour, but
every gauge-wet hour could turn out ERA5-wet too), never the reverse —
NOT orthogonal components of one statistic. Nothing is "isolated": a
nested population is not an independent one, so `joint`'s intensity mean
still carries whatever selection ERA5's own wet/dry call imposes.
`WetHourConditionalIntensityBiasComparison` reports both means, each
with its OWN `n` (never a shared or inherited count), plus
`detection_ratio` (`joint.n / gauge_alone.n` — the fraction of gauge-wet
hours ERA5 also calls wet, itself a detection statistic, not part of the
bias estimate) and `mean_shift_mm_per_h` — how much the estimate moves
when ERA5-dry hours are excluded from the gauge-alone population. This
*is* the comparison the module computes
(`joint.mean_intensity_bias_mm_per_h -
gauge_alone.mean_intensity_bias_mm_per_h`); an earlier draft of this
docstring said the opposite of the code a few lines below it — that
exactly this comparison was "what D2 forbids". D2 forbids comparing
means computed from subsets that do NOT share a common parent population
(the mismatched-population failure mode this milestone keeps
reproducing); it does not forbid a sensitivity comparison between two
NESTED subsets of the SAME parent, which is what `mean_shift_mm_per_h`
is. A nested sensitivity is a legitimate result — which is why the owner
asked for both means to begin with.

**Structural fix for the nesting itself (Finding 1, Plan 184 phase 2
independent review, 2026-08-24):** `WetHourConditionalIntensityBias
Comparison` no longer takes `gauge_alone`/`joint` as independently-
suppliable constructor fields. A prior version did, guarded only by an
anti-join on `timestamp` between the two supplied subsets — two subsets
built from DIFFERENT `PairedSeries` that happened to share timestamps
but carried unrelated gauge/ERA5 values passed that guard undetected (an
anti-join on timestamp is blind to values). The fix is structural, not a
stronger check: `WetHourConditionalIntensityBiasComparison` now stores
exactly ONE `paired: PairedSeries` (plus `station`, `scale`, `params`)
and derives BOTH `gauge_alone` and `joint` from THAT SAME `paired`,
inside its own `@property`s, via `wet_scale_subset`/
`joint_wet_scale_subset` — the same functions, applied to the same
input, whose predicates are supersets of one another by construction
(`joint_wet_scale_subset`'s predicate is `wet_scale_subset`'s plus an
ERA5-wet term). Nesting therefore follows from polars filter semantics
on one shared frame, not from a runtime check comparing two
independently-supplied objects — there is no longer any constructor
argument through which two unrelated subsets could be supplied in the
first place.

**Structural fix, closing review (Finding 1, Plan 184 phase 2 CLOSING
independent review, 2026-08-24 — the SEVENTH instance of this milestone's
signature defect, one level up from every prior fix):** the round above
fixed which POPULATIONS `gauge_alone`/`joint` are computed from (both
derived from ONE `paired`) but left `station` itself as an
independently-suppliable constructor field, never reconciled against
`paired.station` — a correctly-nested pair of values could still be
emitted under the WRONG station label. `station` is now a `@property`
computed live from `self.paired.station` — there is no constructor
argument through which a caller could attach a station that disagrees
with the data it was built from. The same move retires
`wet_hour_conditional_intensity_bias_comparison`'s own `station`
parameter, for the same reason: it is derived from `paired.station`
inside the factory, not accepted as an independent argument.

At the aggregation boundary, `band_wet_hour_conditional_intensity_bias`
and `band_joint_wet_hour_conditional_intensity_bias` used to combine
gauge-alone and joint results through two SEPARATE, independently-
suppliable `Mapping[Station, ...]` arguments — a caller could align them
by hand from different station sets with nothing catching it (no
station-set check, no key/result identity check, no band-membership
check, no shared-parent check: neither function even looked at its own
mapping's keys, only at `.values()`). Both functions are replaced by
`ElevationBandWetHourConditionalIntensityBiasComparison` (built via
`band_wet_hour_conditional_intensity_bias_comparison`), which takes ONE
`comparisons: tuple[WetHourConditionalIntensityBiasComparison, ...]` —
`gauge_alone` and `joint` band estimands are `@property`s both derived
from THAT SAME tuple, using each comparison's own `.station`, so they can
never be built from different station sets: there is no argument through
which gauge-alone-only or joint-only comparisons could be supplied
separately. `__post_init__` also refuses a `comparisons` tuple carrying a
repeated station (`DuplicateBandMemberError`) or a mixed scale
(`ScaleNotSupportedError`) — the band-membership and station-set checks
the old two-mapping design had no way to make.

**Finding 2 (wet-threshold side), closing review, 2026-08-24:**
`_confusion_counts` used to hardcode `>=` for the aggregated-bucket
wet/dry call, ignoring a configured `wet_threshold_side=">"` even though
`wet_predicate`'s own docstring claimed reuse "for both the hourly
gauge-wet conditioning and the aggregated-bucket wet/dry classification".
`_confusion_counts` now builds a small frame from `accumulated.periods`
and calls `wet_predicate` itself on it — the SAME function, not a
re-implementation of its branch — so the aggregated-bucket call and the
hourly call are provably the same predicate, not merely claimed to be.

**Root-cause structural fix, closing the defect CLASS (Plan 184 phase 2,
2026-08-24):** every fix above closed a surface a review happened to look
at; all of them trace one layer below to the same hole — `ma6_pairs.subset()`
used to throw the station away and never carried a scale at all, so every
estimand HERE had to accept `station`/`scale` as independently-suppliable
constructor fields just to re-attach identity its own `subset` already had.
`GaugeRetainedSubset`/`PairedRetainedSubset` now carry `station: Station`
and `scale: Scale` themselves (`ma6_pairs.py`'s own module docstring) —
`MatchedHourMeanDifference`, `WetHourConditionalIntensityBias`,
`JointWetHourConditionalIntensityBias` and `ConditionalAccumulatedDifference`
no longer take `station`/`scale` as constructor fields at all; both are
`@property`s read off `self.subset.station`/`self.subset.scale`. A
station-A/JJAS subset can no longer be reported as station-B/DJF — there is
no argument through which a caller could even attempt it; the attempt is a
`TypeError` (no such keyword), not a value that happens to be checked and
rejected.

This also closes the wet/joint factories' own hole (`wet_hour_conditional_
intensity_bias`, `joint_wet_hour_conditional_intensity_bias`): they used to
accept ANY `PairedRetainedSubset`, including an unconditioned `scale_subset()`
output that was never wet-filtered at all. Both now verify, against the
subset's own `frame` and through `wet_predicate` itself (never a
re-implementation), that every retained hour actually satisfies the
conditioning being claimed — `UnconditionedSubsetError` otherwise.

And it closes the band-aggregation boundary: `band_matched_hour_mean_
difference`/`band_conditional_accumulated_difference` used to combine
member results through a caller-aligned `Mapping[Station, ...]` whose KEYS
were never checked against its VALUES' own (now-intrinsic) `.station` — a
repeated station under two different keys would silently double-count.
Both now take a plain `tuple[...]` of results and read station identity off
each result itself, rejecting a repeated station
(`DuplicateBandMemberError`). `ElevationBandWetHourConditionalIntensityBias
Comparison` gains a `station_elev_m` field and verifies every member's
D4a band membership against `assign_elevation_band` at construction time
(`BandMembershipError`) — `band` was previously independent of
`comparisons` entirely; the same tuple could be labelled under any band
with nothing checking it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

import polars as pl

from scripts.dhm_precip.ma6_pairs import (
    PairedRetainedSubset,
    PairedSeries,
    Scale,
    subset,
)
from scripts.dhm_precip.numeric import as_float, as_int

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.params import DhmPrecipParams


class EstimandSubsetTypeError(TypeError):
    """An estimand result was constructed with something other than the
    real domain object its construction path requires — a
    `PairedRetainedSubset` (or, for `ConditionalAccumulatedDifference`, a
    `periods` partition that does not reconcile against one), a
    `PairedSeries` (`WetHourConditionalIntensityBiasComparison.paired`),
    or a `ConditionalAccumulatedDifference` (`CategoricalScores.
    accumulated`) — the structural guard against a statistic wearing an
    `n` that is not its own (⛔ this track's recurring failure mode, Plan
    184 T1 review)."""


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
    """Internal defence-in-depth only (Finding 1 follow-up, Plan 184 T3
    independent review, 2026-08-21): `ConditionalAccumulatedDifference.
    periods` is now a `@property` computed by `_compute_periods` from
    `subset` itself (see the module docstring), so no caller-facing path
    can attach a mismatched `periods` any more — the partition is
    structurally guaranteed to sum to `subset.n_common_retained`. This
    error guards `_compute_periods`'s own bucketing arithmetic, not a
    caller input; it is not expected to ever fire in practice."""


class DuplicateBandMemberError(ValueError):
    """A band-aggregation `results`/`comparisons` collection carried the
    same station more than once — a band estimand is the UNWEIGHTED MEAN
    across its DISTINCT member stations (D4a/D5a); a repeated station would
    silently double-count that station's own value, exactly the
    band-membership check a caller-aligned `Mapping[Station, ...]` had no
    way to make (Finding 1, Plan 184 phase 2 CLOSING independent review,
    2026-08-24)."""


class UnconditionedSubsetError(ValueError):
    """`wet_hour_conditional_intensity_bias`/`joint_wet_hour_conditional_
    intensity_bias` require a subset already restricted to gauge-wet (resp.
    jointly-wet) hours. Verified structurally against the subset's own
    `frame`, through the SAME `wet_predicate` `wet_scale_subset`/
    `joint_wet_scale_subset` themselves use to build a conditioned subset in
    the first place — not merely assumed from which function happened to
    produce it. A subset carrying even one dry (or, for the joint
    estimand, ERA5-dry) hour is refused HERE, at the one place both
    estimands are actually built, rather than trusted from its caller
    (root-cause structural fix, Plan 184 phase 2, 2026-08-24 — the wet/joint
    factories used to accept ANY `PairedRetainedSubset`, including an
    unconditioned `scale_subset()` output)."""


class BandMembershipError(ValueError):
    """`ElevationBandWetHourConditionalIntensityBiasComparison` was given a
    `comparisons` tuple containing a station whose OWN elevation places it
    outside `band`'s D4a edges (or a station missing from `station_elev_m`
    entirely). `band` used to be independent of `comparisons` — the SAME
    tuple could be labelled under ANY band with nothing checking it
    (root-cause structural fix, Plan 184 phase 2, 2026-08-24). Verified
    against each comparison's now-intrinsic `.station`, re-derived through
    `assign_elevation_band` (D4a's own edges, never a separately-suppliable
    band label that could itself drift out of sync)."""


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
    return subset(paired, season_membership_predicate(scale, params), scale=scale)


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
    return subset(paired, predicate, scale=scale)


def joint_wet_scale_subset(
    paired: PairedSeries, *, scale: Scale, params: DhmPrecipParams
) -> PairedRetainedSubset:
    """As `wet_scale_subset`, additionally restricted to ERA5-wet hours too
    — the JOINT conditioning (both sides wet) `JointWetHourConditional
    IntensityBias` needs. By construction a restriction of
    `wet_scale_subset`'s own predicate (same season and gauge-wet term,
    plus the ERA5-wet term), so the result is always a subset of
    `wet_scale_subset`'s rows for the same `paired`/`scale`/`params` — this
    is its OWN subset via T1's `subset()`, distinct from both
    `scale_subset`'s and `wet_scale_subset`'s."""
    predicate = (
        season_membership_predicate(scale, params)
        & wet_predicate("gauge_value_mm", params)
        & wet_predicate("era5_nearest_mm_per_h", params)
    )
    return subset(paired, predicate, scale=scale)


@dataclass(frozen=True, kw_only=True, slots=True)
class MatchedHourMeanDifference:
    """D1's first estimand: mean(gauge - ERA5) over every commonly-
    retained hour in `subset`, wet and dry alike. `n` is `subset`'s own —
    never a series-level or gauge-only count.

    `mean_difference_mm_per_h` is a `@property` computed live from
    `self.subset` — NOT a constructor field (Finding 1, Plan 184 T3
    independent review, 2026-08-21) — so there is no argument through
    which a caller could attach a mean computed from a different subset,
    even one of the same size.

    `station` and `scale` are likewise `@property`s read off `self.subset.
    station`/`self.subset.scale` — NOT constructor fields (root-cause
    structural fix, module docstring) — so a subset taken for station A at
    JJAS can never be reported as station B or DJF: there is no `station=`/
    `scale=` argument through which a caller could attach a label the
    subset does not itself carry."""

    subset: PairedRetainedSubset

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
    def station(self) -> Station:
        return self.subset.station

    @property
    def scale(self) -> Scale:
        return self.subset.scale

    @property
    def n(self) -> int:
        return self.subset.n_common_retained

    @property
    def mean_difference_mm_per_h(self) -> float:
        return as_float(
            self.subset.frame.select(
                (pl.col("gauge_value_mm") - pl.col("era5_nearest_mm_per_h")).mean()
            ).item()
        )


def matched_hour_mean_difference(
    paired_subset: PairedRetainedSubset,
) -> MatchedHourMeanDifference:
    if paired_subset.n_common_retained == 0:
        # No `.station` here: an empty subset's frame carries zero rows, so
        # its station column carries zero values too — genuinely unknowable
        # (`ma6_pairs.StationIdentityError`), not merely unchecked. `.scale`
        # remains available (a plain field, not frame-derived).
        raise EmptySubsetError(
            f"at {paired_subset.scale}: zero commonly-retained hours — no "
            "matched-hour mean difference is computable"
        )
    return MatchedHourMeanDifference(subset=paired_subset)


@dataclass(frozen=True, kw_only=True, slots=True)
class WetHourConditionalIntensityBias:
    """D1's third estimand: mean(gauge - ERA5) restricted to hours the
    GAUGE recorded as wet (`params.wet_threshold_mm_per_h`) — Rule 1's
    'well-defined under the mask' row, distinct from the unconditioned
    `MatchedHourMeanDifference`. `subset` here is the WET-conditioned
    subset (`wet_scale_subset`'s output), never the unconditioned one —
    `wet_hour_conditional_intensity_bias` (the only recommended
    construction path) verifies this structurally.

    `station` and `scale` are `@property`s read off `self.subset.station`/
    `self.subset.scale` — NOT constructor fields (root-cause structural
    fix, module docstring)."""

    subset: PairedRetainedSubset

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
    def station(self) -> Station:
        return self.subset.station

    @property
    def scale(self) -> Scale:
        return self.subset.scale

    @property
    def n(self) -> int:
        return self.subset.n_common_retained

    @property
    def mean_intensity_bias_mm_per_h(self) -> float:
        return as_float(
            self.subset.frame.select(
                (pl.col("gauge_value_mm") - pl.col("era5_nearest_mm_per_h")).mean()
            ).item()
        )


def wet_hour_conditional_intensity_bias(
    wet_paired_subset: PairedRetainedSubset, *, params: DhmPrecipParams
) -> WetHourConditionalIntensityBias:
    """Requires `wet_paired_subset` to ALREADY be gauge-wet-conditioned
    (`wet_scale_subset`'s output) — verified structurally, against the
    subset's own `frame`, through the SAME `wet_predicate` `wet_scale_
    subset` itself uses to build one in the first place, not merely
    assumed from which function produced it. An unconditioned subset (for
    instance `scale_subset`'s output, carrying dry hours too) is refused
    here — `UnconditionedSubsetError`, not silently accepted."""
    if wet_paired_subset.n_common_retained == 0:
        # No `.station` here — see matched_hour_mean_difference's own empty
        # branch: an empty subset's station is genuinely unknowable.
        raise EmptySubsetError(
            f"at {wet_paired_subset.scale}: zero gauge-wet commonly-retained "
            "hours — no wet-hour conditional intensity bias is computable"
        )
    all_gauge_wet = wet_paired_subset.frame.select(
        wet_predicate("gauge_value_mm", params).all()
    ).item()
    if not all_gauge_wet:
        raise UnconditionedSubsetError(
            f"{wet_paired_subset.station!r} at {wet_paired_subset.scale}: "
            "subset contains at least one gauge-dry hour — "
            "wet_hour_conditional_intensity_bias requires a subset already "
            "restricted to gauge-wet hours (wet_scale_subset's output)"
        )
    return WetHourConditionalIntensityBias(subset=wet_paired_subset)


class JointWetConditionality(StrEnum):
    """Rule 1 / D13: `JointWetHourConditionalIntensityBias` is DOUBLY
    conditional — on retention (D2, like every subset in this module) AND
    on ERA5's own wet/dry classification, since its population is
    restricted to hours ERA5 itself calls wet, not merely the gauge. The
    label carried on every instance must express BOTH conditions, not
    only the ERA5 one (Finding 3, Plan 184 phase 2 independent review,
    2026-08-24 — an earlier version of this member named only the ERA5
    half). `JointWetHourConditionalIntensityBias.joint_conditionality`
    carries this label as a `ClassVar` — the same mechanism
    `CategoricalScores.retention_conditionality` uses — so it travels
    with every instance and cannot be dropped by a downstream renderer.
    `WetHourConditionalIntensityBias` (gauge-alone) is only singly
    conditional (retention only) and Rule 1 already treats it as
    well-defined under the mask — giving it this extra label would
    over-caveat an already-well-defined result, so it does NOT carry this
    marker (owner decision, Plan 184 T3 follow-up)."""

    CONDITIONAL_ON_RETENTION_AND_ERA5_WET_CLASSIFICATION = (
        "CONDITIONAL_ON_RETENTION_AND_ERA5_WET_CLASSIFICATION"
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class JointWetHourConditionalIntensityBias:
    """D1's third estimand, JOINT-conditioned: mean(gauge - ERA5) restricted
    to hours BOTH the gauge AND ERA5 call wet (`joint_wet_scale_subset`'s
    output), at the cost of conditioning on ERA5's own wet/dry behaviour
    too (doubly conditional, see `JointWetConditionality`). `subset` here
    is the JOINT-conditioned subset, never the gauge-alone one — a
    different, NESTED population from `WetHourConditionalIntensityBias`'s
    (a subset of its retained hours, equality permitted), never a
    component of it.

    `n` and `mean_intensity_bias_mm_per_h` are `@property`s computed live
    from `self.subset`, exactly as `WetHourConditionalIntensityBias`'s are
    — there is no constructor argument through which a caller could
    attach a value computed from a different (e.g. gauge-alone) subset.

    `station` and `scale` are likewise `@property`s read off `self.subset.
    station`/`self.subset.scale` — NOT constructor fields (root-cause
    structural fix, module docstring)."""

    subset: PairedRetainedSubset

    joint_conditionality: ClassVar[JointWetConditionality] = (
        JointWetConditionality.CONDITIONAL_ON_RETENTION_AND_ERA5_WET_CLASSIFICATION
    )

    def __post_init__(self) -> None:
        # See MatchedHourMeanDifference.__post_init__ for why this
        # isinstance check exists despite the declared type.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.subset, PairedRetainedSubset
        ):
            raise EstimandSubsetTypeError(
                "JointWetHourConditionalIntensityBias.subset must be a real "
                f"PairedRetainedSubset, got {type(self.subset)}"
            )
        if self.scale not in _MAGNITUDE_SCALES:
            raise ScaleNotSupportedError(
                f"joint wet-hour conditional intensity bias is scoped to "
                f"{_MAGNITUDE_SCALES}, got {self.scale}"
            )

    @property
    def station(self) -> Station:
        return self.subset.station

    @property
    def scale(self) -> Scale:
        return self.subset.scale

    @property
    def n(self) -> int:
        return self.subset.n_common_retained

    @property
    def mean_intensity_bias_mm_per_h(self) -> float:
        return as_float(
            self.subset.frame.select(
                (pl.col("gauge_value_mm") - pl.col("era5_nearest_mm_per_h")).mean()
            ).item()
        )


def joint_wet_hour_conditional_intensity_bias(
    joint_wet_paired_subset: PairedRetainedSubset, *, params: DhmPrecipParams
) -> JointWetHourConditionalIntensityBias:
    """Requires `joint_wet_paired_subset` to ALREADY be jointly-wet-
    conditioned (`joint_wet_scale_subset`'s output) — verified
    structurally, against the subset's own `frame`, through the SAME
    `wet_predicate` `joint_wet_scale_subset` itself uses (applied to BOTH
    `gauge_value_mm` and `era5_nearest_mm_per_h`), not merely assumed from
    which function produced it. An unconditioned (or only gauge-wet-
    conditioned) subset is refused here — `UnconditionedSubsetError`, not
    silently accepted."""
    if joint_wet_paired_subset.n_common_retained == 0:
        # No `.station` here — see matched_hour_mean_difference's own empty
        # branch: an empty subset's station is genuinely unknowable.
        raise EmptySubsetError(
            f"at {joint_wet_paired_subset.scale}: zero jointly-wet "
            "commonly-retained hours — no joint wet-hour conditional "
            "intensity bias is computable"
        )
    all_jointly_wet = joint_wet_paired_subset.frame.select(
        (
            wet_predicate("gauge_value_mm", params)
            & wet_predicate("era5_nearest_mm_per_h", params)
        ).all()
    ).item()
    if not all_jointly_wet:
        raise UnconditionedSubsetError(
            f"{joint_wet_paired_subset.station!r} at "
            f"{joint_wet_paired_subset.scale}: subset contains at least "
            "one hour that is not jointly wet — "
            "joint_wet_hour_conditional_intensity_bias requires a subset "
            "already restricted to jointly-wet hours "
            "(joint_wet_scale_subset's output)"
        )
    return JointWetHourConditionalIntensityBias(subset=joint_wet_paired_subset)


@dataclass(frozen=True, kw_only=True, slots=True)
class WetHourConditionalIntensityBiasComparison:
    """Owner decision, Plan 184 T3 follow-up: report BOTH wet-hour
    conditionings, never gauge-alone only.

    **Structural fix (Finding 1, Plan 184 phase 2 independent review,
    2026-08-24):** this type does NOT take `gauge_alone`/`joint` as
    constructor fields. A prior version did, guarded only by an anti-join
    on `timestamp` — two subsets built from DIFFERENT `PairedSeries` that
    happened to share timestamps but carried unrelated gauge/ERA5 values
    passed that guard undetected. Instead, this type stores exactly ONE
    `paired: PairedSeries` (plus `station`, `scale`, `params`); `gauge_alone`
    and `joint` are `@property`s that derive BOTH conditionings from THAT
    SAME `paired`, via `wet_scale_subset`/`joint_wet_scale_subset` — the
    same functions applied to the same input, whose predicates are
    supersets of one another by construction (`joint_wet_scale_subset`'s
    predicate is `wet_scale_subset`'s plus an ERA5-wet term). Nesting is
    therefore guaranteed by polars filter semantics on one shared frame —
    there is no argument through which two unrelated subsets could ever be
    supplied.

    `gauge_alone` and `joint` are two DIFFERENT, NESTED populations —
    `joint` a subset of `gauge_alone`'s retained hours, equality
    permitted — NOT orthogonal components of one statistic, and this type
    is NOT a decomposition. `detection_ratio` (`joint.n / gauge_alone.n`)
    is itself a detection statistic (the fraction of gauge-wet hours ERA5
    also calls wet), not part of the bias estimate. `mean_shift_mm_per_h`
    is how much the mean moves when ERA5-dry hours are excluded from the
    gauge-alone population — a sensitivity between two NESTED population
    means, not a decomposition and not an orthogonal "detection
    contribution".

    Neither derived number is a constructor field — both are `@property`s
    computed live from `self.gauge_alone`/`self.joint`. There is no
    argument through which a caller could attach a `detection_ratio` or
    `mean_shift_mm_per_h` computed elsewhere.

    **`station` is likewise not an independently-suppliable constructor
    field (Finding 1, Plan 184 phase 2 CLOSING independent review,
    2026-08-24 — see module docstring).** A prior version accepted
    `station` alongside `paired`, with nothing reconciling the two — a
    correctly-nested pair of values could be emitted under the WRONG
    station label. `station` is now a `@property` computed live from
    `self.paired.station`, so a mislabelled result is unconstructible,
    not merely unchecked."""

    scale: Scale
    paired: PairedSeries
    params: DhmPrecipParams

    def __post_init__(self) -> None:
        # See MatchedHourMeanDifference.__post_init__ for why this
        # isinstance check exists despite the declared type.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.paired, PairedSeries
        ):
            raise EstimandSubsetTypeError(
                "WetHourConditionalIntensityBiasComparison.paired must be a "
                f"real PairedSeries, got {type(self.paired)}"
            )
        if self.scale not in _MAGNITUDE_SCALES:
            raise ScaleNotSupportedError(
                f"wet-hour conditional intensity bias comparison is scoped "
                f"to {_MAGNITUDE_SCALES}, got {self.scale}"
            )

    @property
    def station(self) -> Station:
        """Derived from `self.paired.station` — NOT a constructor field —
        so a `WetHourConditionalIntensityBiasComparison` can never carry a
        station label that disagrees with the data it was built from."""
        return self.paired.station

    @property
    def gauge_alone(self) -> WetHourConditionalIntensityBias:
        """Derived from `self.paired` via `wet_scale_subset` — the SAME
        `paired` `joint` is derived from (see class docstring)."""
        wet_subset = wet_scale_subset(self.paired, scale=self.scale, params=self.params)
        return wet_hour_conditional_intensity_bias(wet_subset, params=self.params)

    @property
    def joint(self) -> JointWetHourConditionalIntensityBias:
        """Derived from `self.paired` via `joint_wet_scale_subset` — the
        SAME `paired` `gauge_alone` is derived from (see class
        docstring)."""
        joint_subset = joint_wet_scale_subset(
            self.paired, scale=self.scale, params=self.params
        )
        return joint_wet_hour_conditional_intensity_bias(
            joint_subset, params=self.params
        )

    @property
    def detection_ratio(self) -> float:
        """`joint.n / gauge_alone.n` — the fraction of gauge-wet hours
        ERA5 also calls wet. A detection statistic, not part of the bias
        estimate."""
        return self.joint.n / self.gauge_alone.n

    @property
    def mean_shift_mm_per_h(self) -> float:
        """How much the mean intensity bias moves when ERA5-dry hours are
        excluded from the gauge-alone population — a sensitivity between
        two NESTED population means, NOT a decomposition or an orthogonal
        "detection contribution"."""
        return (
            self.joint.mean_intensity_bias_mm_per_h
            - self.gauge_alone.mean_intensity_bias_mm_per_h
        )


def wet_hour_conditional_intensity_bias_comparison(
    paired: PairedSeries,
    *,
    scale: Scale,
    params: DhmPrecipParams,
) -> WetHourConditionalIntensityBiasComparison:
    """Builds the comparison from ONE `PairedSeries` — `gauge_alone` and
    `joint` are derived INSIDE the comparison's own construction path from
    that SAME input (see `WetHourConditionalIntensityBiasComparison`'s
    docstring), so there is no argument through which two unrelated
    subsets could ever be supplied. `station` is likewise derived from
    `paired.station`, not accepted as an independent argument here (Finding
    1, Plan 184 phase 2 CLOSING independent review, 2026-08-24) — there is
    no way to ask for a comparison labelled with a station other than the
    one `paired` itself carries. `.gauge_alone`/`.joint` are accessed here
    so either component's own `EmptySubsetError` propagates from this
    call, not silently deferred to first property access."""
    comparison = WetHourConditionalIntensityBiasComparison(
        scale=scale, paired=paired, params=params
    )
    _ = comparison.gauge_alone
    _ = comparison.joint
    return comparison


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


def _compute_periods(
    subset: PairedRetainedSubset, scale: Scale
) -> tuple[PeriodAccumulation, ...]:
    """The ONLY place `ConditionalAccumulatedDifference.periods` is
    produced — always grouped from `subset`'s own frame, inside the same
    construction path every time (Finding 1, Plan 184 T3 independent
    review, 2026-08-21). `total_period_hours` is asserted against
    `subset.n_common_retained` as defence-in-depth against a bug in this
    function's own bucketing arithmetic — not against a caller, since a
    caller has no way to reach this function with anything but `subset`'s
    own frame."""
    if scale in _GRAIN_SCALES:
        grouped = (
            subset.frame.with_columns(_bucket_label_expr(scale).alias("_period_label"))
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
        row = subset.frame.select(
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
    total_period_hours = sum(period.n_hours for period in periods)
    if total_period_hours != subset.n_common_retained:
        raise AccumulatedDifferenceReconciliationError(
            f"at {scale}: periods sum to {total_period_hours} hours, but "
            f"subset carries {subset.n_common_retained} — this function's "
            "own bucketing arithmetic does not partition subset's retained "
            "hours (internal bug, not a caller error)"
        )
    return periods


@dataclass(frozen=True, kw_only=True, slots=True)
class ConditionalAccumulatedDifference:
    """D1's second estimand. `periods` is a `@property` computed live from
    `self.subset` and `self.scale` via `_compute_periods` — NOT a
    constructor field (Finding 1, Plan 184 T3 independent review,
    2026-08-21) — so there is no argument through which a caller could
    attach a periods tuple built from a different population, even one of
    the same total size. `periods` therefore always partitions `subset`'s
    own retained hours exactly, by construction.

    `station` and `scale` are likewise `@property`s read off `self.subset.
    station`/`self.subset.scale` — NOT constructor fields (root-cause
    structural fix, module docstring)."""

    subset: PairedRetainedSubset

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

    @property
    def station(self) -> Station:
        return self.subset.station

    @property
    def scale(self) -> Scale:
        return self.subset.scale

    @property
    def n(self) -> int:
        return self.subset.n_common_retained

    @property
    def periods(self) -> tuple[PeriodAccumulation, ...]:
        return _compute_periods(self.subset, self.scale)

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
    paired_subset: PairedRetainedSubset,
) -> ConditionalAccumulatedDifference:
    if paired_subset.n_common_retained == 0:
        # No `.station` here — see matched_hour_mean_difference's own empty
        # branch: an empty subset's station is genuinely unknowable.
        raise EmptySubsetError(
            f"at {paired_subset.scale}: zero commonly-retained hours — no "
            "conditional accumulated difference is computable"
        )
    return ConditionalAccumulatedDifference(subset=paired_subset)


class RetentionConditionality(StrEnum):
    """Rule 1 (`docs/design/dhm-precipitation-milestones.md`): POD/FAR/CSI
    (and the counts they are built from) are reportable ONLY as
    conditional-on-retention estimands — under the MNAR mask POD/CSI are
    inflated and FAR biased low. `CategoricalScores.retention_conditionality`
    carries this label as a `ClassVar` (Finding 2, Plan 184 T3 independent
    review, 2026-08-21) — not a per-instance field a caller could omit or
    swap, so it travels with every `CategoricalScores` value and cannot be
    dropped by a downstream renderer (T6). The two estimands Rule 1 says
    ARE well-defined under the mask (`MatchedHourMeanDifference`,
    `WetHourConditionalIntensityBias`) do NOT carry this marker at all —
    giving them the same label would over-caveat an already-well-defined
    result."""

    CONDITIONAL_ON_RETENTION = "CONDITIONAL_ON_RETENTION"


def _confusion_counts(
    accumulated: ConditionalAccumulatedDifference, params: DhmPrecipParams
) -> tuple[int, int, int, int]:
    """`(hits, misses, false_alarms, correct_negatives)` — the ONLY place
    `CategoricalScores`'s POD/FAR/CSI are derived from, always from
    `accumulated.periods` (mirrors `_compute_periods`'s role for
    `ConditionalAccumulatedDifference.periods`).

    The wet/dry call on each bucket goes through `wet_predicate` itself
    (Finding 2, Plan 184 phase 2 CLOSING independent review, 2026-08-24) —
    not a re-implementation of its `>=`/`>` branch — so `wet_threshold_side`
    genuinely governs the aggregated-bucket classification the same way it
    governs every hourly one; a previous version hardcoded `>=` here,
    silently ignoring a configured `">"` side."""
    periods_frame = pl.DataFrame(
        {
            "gauge_sum_mm": [period.gauge_sum_mm for period in accumulated.periods],
            "era5_sum_mm": [period.era5_sum_mm for period in accumulated.periods],
        }
    ).with_columns(
        wet_predicate("gauge_sum_mm", params).alias("gauge_wet"),
        wet_predicate("era5_sum_mm", params).alias("era5_wet"),
    )
    hits = periods_frame.filter(pl.col("gauge_wet") & pl.col("era5_wet")).height
    misses = periods_frame.filter(pl.col("gauge_wet") & ~pl.col("era5_wet")).height
    false_alarms = periods_frame.filter(
        ~pl.col("gauge_wet") & pl.col("era5_wet")
    ).height
    correct_negatives = periods_frame.filter(
        ~pl.col("gauge_wet") & ~pl.col("era5_wet")
    ).height
    return hits, misses, false_alarms, correct_negatives


@dataclass(frozen=True, kw_only=True, slots=True)
class CategoricalScores:
    """D12 — DAILY/MONTHLY grain only.

    **Structural fix (Finding 2, Plan 184 phase 2 independent review,
    2026-08-24):** `scale`, counts and scores are NOT independently-
    suppliable constructor fields any more. A prior version stored them
    as freely constructible fields and refused seasonal grain only in the
    `categorical_scores()` factory — direct construction accepted a
    JJAS/DJF-grain score with arbitrary `n_hours`/POD/FAR/CSI, bypassing
    both D12's vacuity refusal and this type's own-subset-`n`
    requirement. This type now stores exactly `accumulated:
    ConditionalAccumulatedDifference` and `params: DhmPrecipParams`;
    `station`, `scale`, `n_periods`, `n_hours`, `pod`, `far` and `csi` are
    all `@property`s derived live from `self.accumulated`/`self.params`
    (via `_confusion_counts`), and the seasonal-grain refusal
    (`CategoricalGrainRefusedError`) and the empty-periods refusal
    (`EmptySubsetError`) are both enforced in `__post_init__` — where the
    object is CREATED, not only in a factory. A JJAS/DJF-grain
    `CategoricalScores` is therefore unconstructible, full stop.

    `retention_conditionality` is Rule 1's conditional-on-retention marker
    (see `RetentionConditionality`) — always
    `CONDITIONAL_ON_RETENTION` for every instance of this type."""

    accumulated: ConditionalAccumulatedDifference
    params: DhmPrecipParams

    retention_conditionality: ClassVar[RetentionConditionality] = (
        RetentionConditionality.CONDITIONAL_ON_RETENTION
    )

    def __post_init__(self) -> None:
        # See MatchedHourMeanDifference.__post_init__ for why this
        # isinstance check exists despite the declared type.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.accumulated, ConditionalAccumulatedDifference
        ):
            raise EstimandSubsetTypeError(
                "CategoricalScores.accumulated must be a real "
                f"ConditionalAccumulatedDifference, got {type(self.accumulated)}"
            )
        if self.accumulated.scale not in _GRAIN_SCALES:
            raise CategoricalGrainRefusedError(
                f"{self.accumulated.station!r}: categorical scores at "
                f"{self.accumulated.scale} grain are analytically vacuous by "
                "construction (D12) — refused, not computed. Use DAILY or "
                "MONTHLY grain."
            )
        if self.accumulated.n_periods == 0:
            raise EmptySubsetError(
                f"{self.accumulated.station!r} at {self.accumulated.scale}: "
                "zero periods — no categorical score is computable"
            )

    @property
    def station(self) -> Station:
        return self.accumulated.station

    @property
    def scale(self) -> Scale:
        return self.accumulated.scale

    @property
    def n_periods(self) -> int:
        return self.accumulated.n_periods

    @property
    def n_hours(self) -> int:
        return self.accumulated.n

    @property
    def pod(self) -> float:
        hits, misses, _false_alarms, _correct_negatives = _confusion_counts(
            self.accumulated, self.params
        )
        return hits / (hits + misses) if (hits + misses) > 0 else float("nan")

    @property
    def far(self) -> float:
        hits, _misses, false_alarms, _correct_negatives = _confusion_counts(
            self.accumulated, self.params
        )
        return (
            false_alarms / (hits + false_alarms)
            if (hits + false_alarms) > 0
            else float("nan")
        )

    @property
    def csi(self) -> float:
        hits, misses, false_alarms, _correct_negatives = _confusion_counts(
            self.accumulated, self.params
        )
        return (
            hits / (hits + misses + false_alarms)
            if (hits + misses + false_alarms) > 0
            else float("nan")
        )


def categorical_scores(
    accumulated: ConditionalAccumulatedDifference, *, params: DhmPrecipParams
) -> CategoricalScores:
    """Thin wiring over `CategoricalScores`'s own construction path — the
    grain refusal, the empty-periods refusal and the score derivation all
    live in `CategoricalScores` itself now, so this function has nothing
    left to check that the class does not already enforce."""
    return CategoricalScores(accumulated=accumulated, params=params)


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


def _verify_band_membership(
    band: ElevationBand,
    stations: tuple[Station, ...],
    station_elev_m: Mapping[Station, float],
) -> None:
    """The ONE place D4a band membership is verified — re-derived through
    `assign_elevation_band` (D4a's own edges), never a separately-
    suppliable band label a caller could attach independently of the
    stations actually being aggregated. Shared by every band factory
    (`band_matched_hour_mean_difference`, `band_conditional_accumulated_
    difference`, `band_wet_hour_conditional_intensity_bias_comparison`),
    never re-implemented per factory (Plan 184 phase 2 round 2, change 4)."""
    for station in stations:
        if station not in station_elev_m:
            raise BandMembershipError(
                f"{band}: {station!r} has no known elevation in "
                "station_elev_m — cannot verify band membership"
            )
        elev_m = station_elev_m[station]
        actual_band = assign_elevation_band(elev_m)
        if actual_band is not band:
            raise BandMembershipError(
                f"{band}: {station!r} at {elev_m} m belongs to {actual_band} "
                f"(D4a edges), not the declared {band}"
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
    band: ElevationBand,
    results: tuple[MatchedHourMeanDifference, ...],
    *,
    station_elev_m: Mapping[Station, float],
) -> ElevationBandEstimand:
    """`results` is a plain tuple, not a `Mapping[Station, ...]` — station
    identity is read off each result's own (now-intrinsic) `.station`,
    never a caller-aligned dict key that could disagree with it or repeat a
    station under two different keys (Finding, Plan 184 phase 2 root-cause
    fix, 2026-08-24 — the old `Mapping` design aggregated `.values()` and
    never looked at its own keys).

    `station_elev_m` gets the SAME band-membership verification
    `band_wet_hour_conditional_intensity_bias_comparison` already applies
    (`_verify_band_membership`) — a member whose own elevation places it
    outside `band`'s D4a edges is refused, not silently averaged in (Plan
    184 phase 2 round 2, change 4)."""
    if not results:
        raise EmptySubsetError(
            f"{band}: zero member results — no band estimand is computable"
        )
    stations = tuple(r.station for r in results)
    if len(set(stations)) != len(stations):
        raise DuplicateBandMemberError(
            f"{band}: results carries a repeated station: {stations}"
        )
    _verify_band_membership(band, stations, station_elev_m)
    scales = {r.scale for r in results}
    if len(scales) > 1:
        raise ScaleNotSupportedError(
            f"{band}: member results span more than one scale: {scales}"
        )
    scale = next(iter(scales))
    return _band_estimand(
        band, scale, tuple((r.mean_difference_mm_per_h, r.n) for r in results)
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ElevationBandWetHourConditionalIntensityBiasComparison:
    """The band-level analog of `WetHourConditionalIntensityBiasComparison`
    (Finding 1, Plan 184 phase 2 CLOSING independent review, 2026-08-24).

    The prior design combined gauge-alone and joint band estimands through
    two SEPARATE `band_wet_hour_conditional_intensity_bias`/`band_joint_
    wet_hour_conditional_intensity_bias` calls, each taking its own
    independently-suppliable `Mapping[Station, ...]` — nothing checked that
    the two mappings shared a station set, that a mapping's keys agreed
    with its values' own `.station`, that a member actually belonged to
    `band`, or that gauge-alone and joint shared a common parent
    population. This type instead stores exactly ONE `comparisons:
    tuple[WetHourConditionalIntensityBiasComparison, ...]`; `gauge_alone`
    and `joint` are `@property`s BOTH derived from THAT SAME tuple, using
    each comparison's own `.station` (never a caller-supplied mapping key)
    — so gauge-alone and joint for one band can never be built from
    different station sets, and there is no argument through which either
    could be supplied independently of the other.

    `station_elev_m` (a station's own elevation in metres, D4a) is used to
    VERIFY every comparison's station actually belongs to `band` — re-
    derived through `assign_elevation_band` (D4a's own edges), never a
    separately-suppliable band label. `band` used to be entirely
    independent of `comparisons`, so the SAME comparisons tuple could be
    labelled under ANY band with nothing checking it (root-cause structural
    fix, Plan 184 phase 2, 2026-08-24)."""

    band: ElevationBand
    scale: Scale
    comparisons: tuple[WetHourConditionalIntensityBiasComparison, ...]
    station_elev_m: Mapping[Station, float]

    def __post_init__(self) -> None:
        if len(self.comparisons) == 0:
            raise EmptySubsetError(
                f"{self.band} at {self.scale}: zero member comparisons — no "
                "band estimand is computable"
            )
        stations = tuple(c.station for c in self.comparisons)
        if len(set(stations)) != len(stations):
            raise DuplicateBandMemberError(
                f"{self.band} at {self.scale}: comparisons carries a "
                f"repeated station: {stations}"
            )
        scales = {c.scale for c in self.comparisons}
        if scales != {self.scale}:
            raise ScaleNotSupportedError(
                f"{self.band}: member comparisons span {scales}, not the "
                f"declared scale {self.scale}"
            )
        _verify_band_membership(self.band, stations, self.station_elev_m)

    @property
    def gauge_alone(self) -> ElevationBandEstimand:
        return _band_estimand(
            self.band,
            self.scale,
            tuple(
                (c.gauge_alone.mean_intensity_bias_mm_per_h, c.gauge_alone.n)
                for c in self.comparisons
            ),
        )

    @property
    def joint(self) -> ElevationBandEstimand:
        return _band_estimand(
            self.band,
            self.scale,
            tuple(
                (c.joint.mean_intensity_bias_mm_per_h, c.joint.n)
                for c in self.comparisons
            ),
        )


def band_wet_hour_conditional_intensity_bias_comparison(
    band: ElevationBand,
    comparisons: tuple[WetHourConditionalIntensityBiasComparison, ...],
    *,
    station_elev_m: Mapping[Station, float],
) -> ElevationBandWetHourConditionalIntensityBiasComparison:
    """Builds the band-level comparison from ONE tuple of per-station
    `WetHourConditionalIntensityBiasComparison`s — `gauge_alone` and
    `joint` are derived INSIDE the result's own construction path from
    that SAME tuple (see `ElevationBandWetHourConditionalIntensityBias
    Comparison`'s docstring), so there is no argument through which the
    two could ever be assembled from different station sets. `station_elev_m`
    is forwarded straight through to `__post_init__`'s own band-membership
    verification — there is no path through this factory that skips it."""
    if not comparisons:
        raise EmptySubsetError(
            f"{band}: zero member comparisons — no band estimand is computable"
        )
    result = ElevationBandWetHourConditionalIntensityBiasComparison(
        band=band,
        scale=comparisons[0].scale,
        comparisons=comparisons,
        station_elev_m=station_elev_m,
    )
    _ = result.gauge_alone
    _ = result.joint
    return result


def band_conditional_accumulated_difference(
    band: ElevationBand,
    results: tuple[ConditionalAccumulatedDifference, ...],
    *,
    station_elev_m: Mapping[Station, float],
) -> ElevationBandEstimand:
    """`results` is a plain tuple, not a `Mapping[Station, ...]` — station
    identity is read off each result's own (now-intrinsic) `.station`,
    never a caller-aligned dict key that could disagree with it or repeat a
    station under two different keys (Finding, Plan 184 phase 2 root-cause
    fix, 2026-08-24 — the old `Mapping` design aggregated `.values()` and
    never looked at its own keys).

    `station_elev_m` gets the SAME band-membership verification
    `band_wet_hour_conditional_intensity_bias_comparison` already applies
    (`_verify_band_membership`) — a member whose own elevation places it
    outside `band`'s D4a edges is refused, not silently averaged in (Plan
    184 phase 2 round 2, change 4)."""
    if not results:
        raise EmptySubsetError(
            f"{band}: zero member results — no band estimand is computable"
        )
    stations = tuple(r.station for r in results)
    if len(set(stations)) != len(stations):
        raise DuplicateBandMemberError(
            f"{band}: results carries a repeated station: {stations}"
        )
    _verify_band_membership(band, stations, station_elev_m)
    scales = {r.scale for r in results}
    if len(scales) > 1:
        raise ScaleNotSupportedError(
            f"{band}: member results span more than one scale: {scales}"
        )
    scale = next(iter(scales))
    return _band_estimand(
        band,
        scale,
        tuple((r.mean_period_difference_mm, r.n) for r in results),
    )
