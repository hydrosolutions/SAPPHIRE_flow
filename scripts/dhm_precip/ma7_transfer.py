"""Plan 193 (M-A7) task T3 — transferability and elevation/resolution
stratification.

D3 PINNED — the transferability headline is `stats_precision.
leave_one_out_tail_prediction_error`, UNCHANGED: predict each held-out
station's q99 as `median_i * pooled_ratio(excluding i)` (D5's q50/q99
body/tail split), computed on the MASKED series (D1) via T2's
`StationIntensityDistribution`. No second formulation is written here —
this module only groups T2's already-computed q50/q99 two different ways
and feeds each grouping through the SAME pinned function.

D6 — this module computes the SAME statistic over two different station
groupings — `ElevationBand` (D5a) and reporting-resolution group
(`resolution.infer_reporting_resolution`) — and DECLINES to attribute a
difference between them to either. `TransferabilityComparison` carries
`declined_attribution` as a DATA field (its value is fixed and checked in
`__post_init__`), not a comment, so the refusal survives being quoted out
of context.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from scripts.dhm_precip.ma7_intensity import (
    BandIntensityDistribution,
    DuplicateBandMemberError,
    MixedSeasonError,
    MixedSelectionParamsError,
    StationIntensityDistribution,
)
from scripts.dhm_precip.stats_precision import leave_one_out_tail_prediction_error

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.ma6_estimands import ElevationBand
    from scripts.dhm_precip.seasons import Season

ResolutionGroupLabel = Literal["A", "B"]
"""`resolution.infer_reporting_resolution`'s own two labels (D6/D7 in
`stats_precision.py`) — never re-derived here. This module receives a
station's group as already-classified data, the same discipline
`BandIntensityDistribution.station_elev_m` already follows for elevation
(D6's own reuse instruction: the classification runs once, externally, on
the ON_GRID population)."""

DECLINED_ATTRIBUTION = (
    "D6 — Group A is simultaneously the 0.01 mm reporting-resolution "
    "subset and the high-altitude subset. This comparison reports both "
    "cuts side by side and explicitly DECLINES to attribute any "
    "difference between them to elevation or to reporting resolution. "
    "Bounding that confound is M-A8's exit, not this plan's."
)
"""D6's refusal, fixed and checked by `TransferabilityComparison.
__post_init__` — a caller cannot construct a comparison carrying a
different or missing attribution statement."""


class ResolutionGroupMembershipError(ValueError):
    """A `ResolutionGroupIntensityDistribution` member's own resolution
    group (looked up in `station_resolution_group`) does not match the
    declared `group`, or the member has no known group at all — the
    resolution-group analogue of `ma7_intensity.BandMembershipError`."""


class DuplicateGroupError(ValueError):
    """`compare_transferability` was given more than one distribution for
    the same `ElevationBand` (in `by_band`) or the same resolution group
    (in `by_resolution_group`)."""


class MismatchedStationPopulationError(ValueError):
    """The elevation-band cut and the reporting-resolution cut passed to
    `compare_transferability` do not cover the SAME set of stations. D6
    compares the two cuts side by side; a population mismatch would let an
    apparent difference come from which stations were included rather than
    from the grouping itself, which is exactly the confound D6 forbids
    pre-empting a conclusion about."""


def _validate_group_members(
    members: tuple[StationIntensityDistribution, ...], *, label: str
) -> None:
    if not members:
        raise ValueError(f"{label}: zero member stations")
    stations = tuple(m.station for m in members)
    if len(set(stations)) != len(stations):
        raise DuplicateBandMemberError(
            f"{label}: members carries a repeated station: {stations}"
        )
    seasons = {m.season for m in members}
    if len(seasons) > 1:
        raise MixedSeasonError(f"{label}: members span more than one season: {seasons}")
    selection_params = {m.params for m in members}
    if len(selection_params) > 1:
        raise MixedSelectionParamsError(
            f"{label}: members were selected under different DhmPrecipParams"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolutionGroupIntensityDistribution:
    """D6's reporting-resolution cut — the SAME validated-aggregate
    mechanism as `ma7_intensity.BandIntensityDistribution`, over a
    DIFFERENT grouping (inferred reporting resolution, never re-derived
    here). `members`/`group`/`station_resolution_group` are the only
    stored fields; `station_count` is a `@property` derived from them —
    this track's own structural convention."""

    group: ResolutionGroupLabel
    members: tuple[StationIntensityDistribution, ...]
    station_resolution_group: Mapping[Station, ResolutionGroupLabel]

    def __post_init__(self) -> None:
        _validate_group_members(self.members, label=f"resolution group {self.group}")
        for member in self.members:
            if member.station not in self.station_resolution_group:
                raise ResolutionGroupMembershipError(
                    f"resolution group {self.group}: {member.station!r} has no "
                    "known group in station_resolution_group"
                )
            actual = self.station_resolution_group[member.station]
            if actual != self.group:
                raise ResolutionGroupMembershipError(
                    f"resolution group {self.group}: {member.station!r} is "
                    f"classified group {actual!r}, not the declared {self.group!r}"
                )

    @property
    def season(self) -> Season:
        return self.members[0].season

    @property
    def station_count(self) -> int:
        return len(self.members)


def build_resolution_groups(
    members: tuple[StationIntensityDistribution, ...],
    station_resolution_group: Mapping[Station, ResolutionGroupLabel],
) -> tuple[ResolutionGroupIntensityDistribution, ...]:
    """Partitions `members` by `station_resolution_group`'s own label,
    one `ResolutionGroupIntensityDistribution` per label present, sorted by
    label for a deterministic order. A member absent from
    `station_resolution_group` raises (via the constructed distribution's
    own `__post_init__`) rather than being silently dropped."""
    by_label: dict[ResolutionGroupLabel, list[StationIntensityDistribution]] = {}
    for member in members:
        label = station_resolution_group.get(member.station)
        if label is None:
            raise ResolutionGroupMembershipError(
                f"{member.station!r} has no known group in station_resolution_group"
            )
        by_label.setdefault(label, []).append(member)
    return tuple(
        ResolutionGroupIntensityDistribution(
            group=label,
            members=tuple(group_members),
            station_resolution_group=station_resolution_group,
        )
        for label, group_members in sorted(by_label.items())
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class TailPredictionError:
    """D3's pinned `leave_one_out_tail_prediction_error`, wrapped with the
    group it was computed over. `median_abs_error`/`min_error`/`max_error`/
    `within_25pct_fraction` are exactly that function's own returned
    values — copied, never recomputed.

    `n_stations_used` is the number of member stations carrying BOTH a
    q50 and a q99 in this group/season — the candidate pool
    `leave_one_out_tail_prediction_error` draws its held-out predictions
    from. It is NOT necessarily the number of held-out predictions the
    reported statistics average over: a group of exactly one candidate (or
    a candidate whose median or q99 is exactly 0.0) contributes zero
    predictions, at which point the four error fields are `nan` even
    though `n_stations_used == 1` — the same all-`nan` degenerate return
    `leave_one_out_tail_prediction_error` itself already uses for "no
    comparison was possible", not a state this wrapper invents.
    `station_count` is the group's TOTAL membership (D5a's own station
    counts), which may exceed `n_stations_used` when a member's season is
    degenerate for that station (no retained wet hours -> `q50_mm_per_h`/
    `q99_mm_per_h` are `None`)."""

    median_abs_error: float
    min_error: float
    max_error: float
    within_25pct_fraction: float
    n_stations_used: int
    station_count: int

    def __post_init__(self) -> None:
        if self.n_stations_used < 0:
            raise ValueError(
                f"n_stations_used must be >= 0, got {self.n_stations_used}"
            )
        if self.station_count < 1:
            raise ValueError(f"station_count must be >= 1, got {self.station_count}")
        if self.n_stations_used > self.station_count:
            raise ValueError(
                f"n_stations_used ({self.n_stations_used}) exceeds "
                f"station_count ({self.station_count})"
            )
        if not math.isnan(self.within_25pct_fraction) and not (
            0.0 <= self.within_25pct_fraction <= 1.0
        ):
            raise ValueError(
                "within_25pct_fraction must be in [0, 1] or nan, got "
                f"{self.within_25pct_fraction}"
            )
        if (
            not math.isnan(self.min_error)
            and not math.isnan(self.max_error)
            and self.min_error > self.max_error
        ):
            raise ValueError(
                f"min_error ({self.min_error}) exceeds max_error ({self.max_error})"
            )


def _tail_prediction_error(
    members: tuple[StationIntensityDistribution, ...],
) -> TailPredictionError:
    per_station_median = {
        str(m.station): m.q50_mm_per_h for m in members if m.q50_mm_per_h is not None
    }
    per_station_q99 = {
        str(m.station): m.q99_mm_per_h for m in members if m.q99_mm_per_h is not None
    }
    n_used = len(set(per_station_median) & set(per_station_q99))
    result = leave_one_out_tail_prediction_error(per_station_median, per_station_q99)
    return TailPredictionError(
        median_abs_error=result["median_abs_error"],
        min_error=result["min_error"],
        max_error=result["max_error"],
        within_25pct_fraction=result["within_25pct_fraction"],
        n_stations_used=n_used,
        station_count=len(members),
    )


def elevation_band_prediction_error(
    distribution: BandIntensityDistribution,
) -> TailPredictionError:
    """D3 on D5a's elevation cut — the held-out prediction error computed
    WITHIN this band only (the pool for each held-out station's
    `pooled_ratio` is the band's OTHER members, never the full 26-station
    archive) — the natural reading of "stratify by elevation band": this
    is what transfers BETWEEN stations OF THE SAME BAND, not a global
    figure re-sliced after the fact."""
    return _tail_prediction_error(distribution.members)


def resolution_group_prediction_error(
    distribution: ResolutionGroupIntensityDistribution,
) -> TailPredictionError:
    """D3 on D6's reporting-resolution cut — computed WITHIN this
    resolution group only, exactly as `elevation_band_prediction_error`
    is computed within its band (D6: the two cuts use the SAME estimand,
    the SAME statistic, differing only in which grouping partitions the
    stations)."""
    return _tail_prediction_error(distribution.members)


@dataclass(frozen=True, kw_only=True, slots=True)
class TransferabilityComparison:
    """D6's headline artefact — the SAME `TailPredictionError` statistic
    computed over two different station groupings, reported side by side,
    for one season. `declined_attribution` is a DATA field carrying D6's
    refusal; `__post_init__` rejects any value other than the pinned
    `DECLINED_ATTRIBUTION` text, so it cannot be silently dropped or
    reworded by a caller."""

    season: Season
    by_elevation_band: Mapping[ElevationBand, TailPredictionError]
    by_resolution_group: Mapping[ResolutionGroupLabel, TailPredictionError]
    declined_attribution: str = DECLINED_ATTRIBUTION

    def __post_init__(self) -> None:
        if self.declined_attribution != DECLINED_ATTRIBUTION:
            raise ValueError(
                "declined_attribution is not the pinned D6 refusal text — "
                "it is not a caller-suppliable field"
            )
        if not self.by_elevation_band:
            raise ValueError("by_elevation_band: zero elevation bands")
        if not self.by_resolution_group:
            raise ValueError("by_resolution_group: zero resolution groups")


def compare_transferability(
    *,
    by_band: tuple[BandIntensityDistribution, ...],
    by_resolution_group: tuple[ResolutionGroupIntensityDistribution, ...],
) -> TransferabilityComparison:
    """Builds D6's side-by-side comparison from T2's already-validated band
    distributions and this module's resolution-group distributions.

    Two structural checks beyond `TailPredictionError`'s own: (1) every
    distribution in `by_band` shares one season with every distribution in
    `by_resolution_group` — comparing two cuts computed on different
    seasons would not be "the same estimand" (D6); (2) the two cuts cover
    the SAME set of stations (`MismatchedStationPopulationError`) — a
    partial-population mismatch would let a difference between the cuts
    come from which stations were included rather than from the grouping
    itself, exactly the confound D6 forbids pre-empting."""
    if not by_band:
        raise ValueError("compare_transferability: zero elevation bands")
    if not by_resolution_group:
        raise ValueError("compare_transferability: zero resolution groups")

    bands = [d.band for d in by_band]
    if len(set(bands)) != len(bands):
        raise DuplicateGroupError(
            f"compare_transferability: duplicate elevation band in by_band: {bands}"
        )
    groups = [d.group for d in by_resolution_group]
    if len(set(groups)) != len(groups):
        raise DuplicateGroupError(
            "compare_transferability: duplicate resolution group in "
            f"by_resolution_group: {groups}"
        )

    seasons = {d.season for d in by_band} | {d.season for d in by_resolution_group}
    if len(seasons) > 1:
        raise MixedSeasonError(
            "compare_transferability: the elevation and resolution cuts "
            f"must share one season, got {seasons}"
        )
    (season,) = seasons

    band_stations = frozenset(str(m.station) for d in by_band for m in d.members)
    group_stations = frozenset(
        str(m.station) for d in by_resolution_group for m in d.members
    )
    if band_stations != group_stations:
        raise MismatchedStationPopulationError(
            "compare_transferability: the elevation-band cut and the "
            "resolution-group cut do not cover the same stations — "
            f"only in bands: {sorted(band_stations - group_stations)}, "
            f"only in groups: {sorted(group_stations - band_stations)}"
        )

    return TransferabilityComparison(
        season=season,
        by_elevation_band={d.band: elevation_band_prediction_error(d) for d in by_band},
        by_resolution_group={
            d.group: resolution_group_prediction_error(d) for d in by_resolution_group
        },
    )
