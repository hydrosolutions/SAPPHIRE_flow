"""Plan 205 (M-A8) task T1 — the confound bound.

D1 — elevation is analysed WITHIN each reporting-resolution group; only the
BETWEEN-group contrast is reported, and reported as UNIDENTIFIED. Group A
(0.01 mm reporting resolution) and Group B (0.2 mm) occupy non-overlapping
elevation ranges in the 26-station masked population, so any difference
between the two groups is equally a resolution effect and an elevation
effect — no covariate adjustment over these stations can separate them.
That does NOT make elevation unanalysable: it varies widely *inside* each
group at fixed reporting resolution, so a WITHIN-group relationship is
identified (D1's own precedent: the M-A7-motivating exploratory result was
computed "within Group B alone").

**D2 — this module calls M-A6's and M-A7's own public constructors and
factories; it re-implements no estimand.** `classify_stations` calls
`resolution.infer_reporting_resolution` and `ma6_estimands.
assign_elevation_band` directly — it does not re-derive either. The
within-group elevation relationship (`WithinGroupElevationRelationship`) is
deliberately BLIND to which concrete M-A6/M-A7 estimand type produced a
number: it accepts `GroupElevationObservation`s whose `value`/`n` the
CALLER reads off a real estimand's own property (e.g. `MatchedHourMean
Difference.mean_difference_mm_per_h`, `StationIntensityDistribution.
q99_mm_per_h`). A per-estimand-kind dispatcher living HERE would grow
without bound as new M-A6/M-A7 estimand kinds are added, and would
duplicate value-extraction logic each producing module already owns
(`ma6_estimands.BandMember.value`'s own dispatch is exactly that duplication
risk realised once already — this module does not repeat it). This keeps
T1 "mostly assembly" (Plan 205's own proportionality framing): T1 supplies
the classification, the cross-tabulation, the between-group refusal
statement, and a single reusable within-group correlation shape; it does
not know or care which M-A6/M-A7 quantity is being correlated.

**D3 — the `2,000-3,000 m` band is the confound's only evidence.** It is
the one D5a band containing both groups (Aiselukhark/Nagarkot in Group B,
Lete/Lukla Airport/Ghorepani in Group A), and its q99/q50 ratio (M-A7's own
tail-heaviness statistic) splits along group lines.
`BandGroupSplitQuantileRatios` reuses M-A7's `StationIntensityDistribution`
objects verbatim (`.q50_mm_per_h`/`.q99_mm_per_h`) — never a recomputed
quantile.

Every type here follows this track's established structural convention:
a type accepts only what it is derived from, plus selections verified
against it; every check a factory would do lives in `__post_init__`;
frozen, `kw_only`, `slots` dataclasses throughout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand, assign_elevation_band
from scripts.dhm_precip.resolution import infer_reporting_resolution

if TYPE_CHECKING:
    from collections.abc import Mapping

    import polars as pl

    from scripts.dhm_precip.ma7_intensity import StationIntensityDistribution
    from scripts.dhm_precip.ma7_transfer import ResolutionGroupLabel
    from scripts.dhm_precip.params import DhmPrecipParams


class MissingGroupClassificationError(ValueError):
    """`classify_stations` was given a `station_elev_m` entry for a station
    `infer_reporting_resolution` produced no classification for — every
    station this module classifies must actually appear in the ON_GRID
    population the resolution classification is computed over."""


class EmptyGroupError(ValueError):
    """A group/band aggregate was asked to summarise zero members — never
    silently reported as an empty relationship."""


class DuplicateStationError(ValueError):
    """A `members` tuple carried the same station more than once — every
    aggregate here is over DISTINCT member stations."""


class GroupMembershipError(ValueError):
    """A member's own resolution-group classification does not match the
    group an aggregate declares it belongs to."""


class BandMembershipError(ValueError):
    """A member's own elevation does not place it in the D5a band an
    aggregate declares it belongs to (re-derived through
    `assign_elevation_band`, never a separately-suppliable band label)."""


class GroupRangesOverlapError(ValueError):
    """`BetweenGroupContrastStatement` was given Group A/B elevation ranges
    that DO overlap — D1's between-group-unidentified statement is only
    licensed by non-overlapping ranges; if the ranges overlapped the
    between-group contrast would not automatically be unidentified, and
    this pinned statement must not be attached to that case."""


class ZeroVarianceError(ValueError):
    """A Pearson correlation was requested over a group whose elevation or
    value carries zero variance — undefined, not silently reported as 0.0
    or NaN."""


class InsufficientObservationsError(ValueError):
    """A within-group Pearson correlation was requested over fewer than
    `_PEARSON_R_MIN_STATIONS` member stations — not a well-defined
    correlation, refused rather than computed on 1-2 points."""


def _resolution_label(raw: str) -> ResolutionGroupLabel:
    """`infer_reporting_resolution`'s own two labels, typed — the same
    conversion `ma7_run._resolution_label` performs; redeclared here rather
    than importing that module's private helper (this track's own
    convention for a small, single-purpose adapter — see
    `ma6_mass_fraction.py`'s redeclaration of `_T2M_SERIES_FILENAME` for the
    precedent)."""
    if raw not in ("A", "B"):
        raise ValueError(f"unexpected reporting-resolution group label {raw!r}")
    return "A" if raw == "A" else "B"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationClassification:
    """One station's own reporting-resolution group and D5a elevation band
    — both re-derived through the existing helpers
    (`resolution.infer_reporting_resolution`, `ma6_estimands.
    assign_elevation_band`), never independently supplied."""

    station: Station
    group: ResolutionGroupLabel
    band: ElevationBand
    elev_m: float


def classify_stations(
    on_grid: pl.DataFrame,
    *,
    station_elev_m: Mapping[Station, float],
    params: DhmPrecipParams,
) -> tuple[StationClassification, ...]:
    """Classifies every station in `station_elev_m` by calling
    `infer_reporting_resolution(on_grid, params)` and `assign_elevation_band`
    directly — the ONE place this module touches either helper, so every
    `StationClassification` downstream is built from this single call,
    never a second, independently-drifting derivation. `on_grid` is the
    UNMASKED view `resolution.infer_reporting_resolution` itself requires
    (D6's own instruction, `ma7_run.py`'s precedent: the classification is
    defined over `on_grid`, never the masked series)."""
    resolution_frame = infer_reporting_resolution(on_grid, params)
    group_by_station: dict[Station, ResolutionGroupLabel] = {
        Station(str(row["station"])): _resolution_label(str(row["group"]))
        for row in resolution_frame.iter_rows(named=True)
    }
    missing = set(station_elev_m) - set(group_by_station)
    if missing:
        raise MissingGroupClassificationError(
            f"station(s) {sorted(str(s) for s in missing)} have a known "
            "elevation but infer_reporting_resolution produced no "
            "classification for them"
        )
    return tuple(
        StationClassification(
            station=station,
            group=group_by_station[station],
            band=assign_elevation_band(elev_m),
            elev_m=elev_m,
        )
        for station, elev_m in sorted(station_elev_m.items(), key=lambda kv: str(kv[0]))
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupElevationRange:
    """One resolution group's own elevation extent, over its
    `StationClassification` members only. `members`/`group` are the only
    stored fields; every derived figure (`min_elev_m`/`max_elev_m`/
    `relief_m`/`station_count`) is a `@property`."""

    group: ResolutionGroupLabel
    members: tuple[StationClassification, ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise EmptyGroupError(f"group {self.group}: zero member stations")
        stations = tuple(m.station for m in self.members)
        if len(set(stations)) != len(stations):
            raise DuplicateStationError(
                f"group {self.group}: members carries a repeated station: {stations}"
            )
        for member in self.members:
            if member.group != self.group:
                raise GroupMembershipError(
                    f"group {self.group}: {member.station!r} is classified "
                    f"{member.group!r}, not the declared {self.group!r}"
                )

    @property
    def elevations_m(self) -> tuple[float, ...]:
        return tuple(m.elev_m for m in self.members)

    @property
    def min_elev_m(self) -> float:
        return min(self.elevations_m)

    @property
    def max_elev_m(self) -> float:
        return max(self.elevations_m)

    @property
    def relief_m(self) -> float:
        return self.max_elev_m - self.min_elev_m

    @property
    def station_count(self) -> int:
        return len(self.members)


def group_elevation_ranges(
    classifications: tuple[StationClassification, ...],
) -> tuple[GroupElevationRange, ...]:
    """One `GroupElevationRange` per distinct group present in
    `classifications`, sorted by group label for a deterministic order."""
    by_group: dict[ResolutionGroupLabel, list[StationClassification]] = {}
    for classification in classifications:
        by_group.setdefault(classification.group, []).append(classification)
    return tuple(
        GroupElevationRange(group=group, members=tuple(members))
        for group, members in sorted(by_group.items())
    )


BETWEEN_GROUP_CONTRAST_UNIDENTIFIED = (
    "D1 -- Group A (0.01 mm reporting resolution) and Group B (0.2 mm) "
    "occupy non-overlapping elevation ranges in this sample, so any "
    "difference between the two groups is equally a resolution effect and "
    "an elevation effect. No covariate adjustment over these stations can "
    "separate them: the BETWEEN-group contrast is UNIDENTIFIED. This does "
    "not make elevation unanalysable -- see the WITHIN-group relationship "
    "reported beside it."
)
"""D1's pinned refusal text — a data field on `BetweenGroupContrastStatement`
(`ma7_transfer.DECLINED_ATTRIBUTION`'s own precedent), not a comment, so it
survives being quoted out of context and cannot be silently reworded."""


@dataclass(frozen=True, kw_only=True, slots=True)
class BetweenGroupContrastStatement:
    """D1's between-group refusal, carrying the two ranges it is licensed
    by. Constructible ONLY when Group A's and Group B's elevation ranges do
    not overlap (`GroupRangesOverlapError` otherwise) — the statement is
    not a generic label a caller could attach regardless of what the data
    shows."""

    ranges: tuple[GroupElevationRange, ...]
    declined_attribution: str = BETWEEN_GROUP_CONTRAST_UNIDENTIFIED

    def __post_init__(self) -> None:
        if self.declined_attribution != BETWEEN_GROUP_CONTRAST_UNIDENTIFIED:
            raise ValueError(
                "declined_attribution is not the pinned D1 statement -- it "
                "is not a caller-suppliable field"
            )
        groups = sorted(r.group for r in self.ranges)
        if groups != ["A", "B"]:
            raise ValueError(
                "BetweenGroupContrastStatement requires exactly one "
                f"GroupElevationRange for each of A and B, got {groups}"
            )
        by_group = {r.group: r for r in self.ranges}
        group_a, group_b = by_group["A"], by_group["B"]
        if group_a.min_elev_m <= group_b.max_elev_m and (
            group_b.min_elev_m <= group_a.max_elev_m
        ):
            raise GroupRangesOverlapError(
                f"group A [{group_a.min_elev_m}, {group_a.max_elev_m}] m "
                f"overlaps group B [{group_b.min_elev_m}, "
                f"{group_b.max_elev_m}] m -- D1's between-group-unidentified "
                "statement does not hold when the ranges overlap"
            )


def between_group_contrast_statement(
    ranges: tuple[GroupElevationRange, ...],
) -> BetweenGroupContrastStatement:
    return BetweenGroupContrastStatement(ranges=ranges)


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupBandCount:
    """One (group, band) cell of the D1 cross-tabulation, including cells
    with zero members — the full grid, not only the non-empty cells."""

    group: ResolutionGroupLabel
    band: ElevationBand
    station_count: int


_BAND_ORDER: tuple[ElevationBand, ...] = (
    ElevationBand.BELOW_700M,
    ElevationBand.B700_2000M,
    ElevationBand.B2000_3000M,
    ElevationBand.ABOVE_3000M,
)


def group_band_cross_tabulation(
    classifications: tuple[StationClassification, ...],
) -> tuple[GroupBandCount, ...]:
    """Every (group, band) cell over `_BAND_ORDER` x the groups actually
    observed in `classifications`, in a fixed deterministic order —
    including empty cells (`station_count == 0`), matching D1's own
    reported table."""
    if not classifications:
        raise EmptyGroupError("group_band_cross_tabulation: zero classified stations")
    groups: list[ResolutionGroupLabel] = sorted({c.group for c in classifications})
    counts: dict[tuple[ResolutionGroupLabel, ElevationBand], int] = {}
    for classification in classifications:
        key = (classification.group, classification.band)
        counts[key] = counts.get(key, 0) + 1
    return tuple(
        GroupBandCount(
            group=group, band=band, station_count=counts.get((group, band), 0)
        )
        for band in _BAND_ORDER
        for group in groups
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class BandGroupSplitQuantileMember:
    """One member's own M-A7 `StationIntensityDistribution` plus the
    resolution group it belongs to. `q50_mm_per_h`/`q99_mm_per_h`/`ratio`
    are `@property`s read straight off `distribution` — never recomputed
    (D2)."""

    distribution: StationIntensityDistribution
    group: ResolutionGroupLabel

    @property
    def station(self) -> Station:
        return self.distribution.station

    @property
    def q50_mm_per_h(self) -> float | None:
        return self.distribution.q50_mm_per_h

    @property
    def q99_mm_per_h(self) -> float | None:
        return self.distribution.q99_mm_per_h

    @property
    def ratio(self) -> float | None:
        q50, q99 = self.q50_mm_per_h, self.q99_mm_per_h
        if q50 is None or q99 is None or q50 == 0.0:
            return None
        return q99 / q50


@dataclass(frozen=True, kw_only=True, slots=True)
class BandGroupSplitQuantileRatios:
    """D3 — the one D5a band this sample's confound has evidence about,
    reported with the group split. Every member's D5a band membership and
    resolution-group label are verified against `station_elev_m`/
    `station_group` at construction — the same discipline
    `ma6_estimands.ElevationBandEstimand` applies to its own members."""

    band: ElevationBand
    members: tuple[BandGroupSplitQuantileMember, ...]
    station_elev_m: Mapping[Station, float]
    station_group: Mapping[Station, ResolutionGroupLabel]

    def __post_init__(self) -> None:
        if not self.members:
            raise EmptyGroupError(f"{self.band}: zero member stations")
        stations = tuple(m.station for m in self.members)
        if len(set(stations)) != len(stations):
            raise DuplicateStationError(
                f"{self.band}: members carries a repeated station: {stations}"
            )
        for member in self.members:
            if member.station not in self.station_elev_m:
                raise BandMembershipError(
                    f"{self.band}: {member.station!r} has no known elevation "
                    "in station_elev_m"
                )
            actual_band = assign_elevation_band(self.station_elev_m[member.station])
            if actual_band is not self.band:
                raise BandMembershipError(
                    f"{self.band}: {member.station!r} at "
                    f"{self.station_elev_m[member.station]} m belongs to "
                    f"{actual_band} (D5a edges), not the declared {self.band}"
                )
            expected_group = self.station_group.get(member.station)
            if expected_group != member.group:
                raise GroupMembershipError(
                    f"{self.band}: {member.station!r} is labelled group "
                    f"{member.group!r} but station_group says "
                    f"{expected_group!r}"
                )


def band_group_split_quantile_ratios(
    band: ElevationBand,
    distributions: tuple[StationIntensityDistribution, ...],
    *,
    station_elev_m: Mapping[Station, float],
    station_group: Mapping[Station, ResolutionGroupLabel],
) -> BandGroupSplitQuantileRatios:
    """`distributions` is a plain tuple of M-A7's own
    `StationIntensityDistribution` objects — station identity and group are
    both read/verified against `station_elev_m`/`station_group`, never
    trusted from the caller's ordering."""
    unknown_group = [d.station for d in distributions if d.station not in station_group]
    if unknown_group:
        raise GroupMembershipError(
            f"{band}: station(s) {sorted(str(s) for s in unknown_group)} have "
            "no known resolution group in station_group"
        )
    members = tuple(
        BandGroupSplitQuantileMember(distribution=d, group=station_group[d.station])
        for d in sorted(distributions, key=lambda d: str(d.station))
    )
    return BandGroupSplitQuantileRatios(
        band=band,
        members=members,
        station_elev_m=station_elev_m,
        station_group=station_group,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupElevationObservation:
    """One member station's own elevation and a named quantity's value, for
    a within-group elevation relationship (D1). `value` and `n` are
    supplied by the CALLER, always read off a real M-A6/M-A7 estimand's own
    property (e.g. `MatchedHourMeanDifference.mean_difference_mm_per_h`,
    `StationIntensityDistribution.q99_mm_per_h`) — never recomputed here
    (D2, module docstring)."""

    station: Station
    elev_m: float
    value: float
    n: int

    def __post_init__(self) -> None:
        if self.n < 0:
            raise ValueError(f"{self.station!r}: n must be >= 0, got {self.n}")
        if not math.isfinite(self.elev_m):
            raise ValueError(f"{self.station!r}: elev_m {self.elev_m!r} is not finite")
        if not math.isfinite(self.value):
            raise ValueError(f"{self.station!r}: value {self.value!r} is not finite")


_PEARSON_R_MIN_STATIONS = 3
"""A 2-point "correlation" is a line through two points by construction --
not a relationship, D1's own ⛔ against presenting Group A's two 3-station
bands as a trend applies with equal force to any 2-observation fit."""

_PEARSON_R_ROUNDING_DECIMALS = 9
"""D8 — `pearson_r` is a pure-Python computation over a FIXED, sorted
member order (never polars' multi-threaded reduction), so it is already
deterministic run to run; this rounding follows the D8 precedent
(`ma6_estimands._BUCKET_TOTAL_ROUNDING_DECIMALS`) as defence in depth
against any future change that reintroduces order-dependent float
summation, not because a jitter has been observed here."""


def _pearson_r(elevs_m: tuple[float, ...], values: tuple[float, ...]) -> float:
    n = len(elevs_m)
    mean_x = sum(elevs_m) / n
    mean_y = sum(values) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(elevs_m, values, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in elevs_m)
    var_y = sum((y - mean_y) ** 2 for y in values)
    if var_x == 0.0 or var_y == 0.0:
        raise ZeroVarianceError(
            "Pearson r is undefined when elevation or value carries zero "
            "variance across the group's members"
        )
    r = cov / math.sqrt(var_x * var_y)
    return round(r, _PEARSON_R_ROUNDING_DECIMALS)


@dataclass(frozen=True, kw_only=True, slots=True)
class WithinGroupElevationRelationship:
    """D1's within-group elevation analysis — a Pearson correlation of a
    named quantity against elevation, over members drawn from ONE
    resolution group at fixed reporting resolution.

    DESCRIPTIVE ONLY (D1's own ⛔): holding reporting resolution constant
    removes one confound, not all — exposure, siting, catchment character
    and monsoon dynamics all co-vary with elevation too. Nothing here is
    named or documented as an elevation *effect*."""

    quantity_label: str
    group: ResolutionGroupLabel
    members: tuple[GroupElevationObservation, ...]
    station_group: Mapping[Station, ResolutionGroupLabel]

    def __post_init__(self) -> None:
        if not self.members:
            raise EmptyGroupError(
                f"{self.quantity_label}: zero member stations in group {self.group}"
            )
        stations = tuple(m.station for m in self.members)
        if len(set(stations)) != len(stations):
            raise DuplicateStationError(
                f"{self.quantity_label}: members carries a repeated station: {stations}"
            )
        for member in self.members:
            actual = self.station_group.get(member.station)
            if actual != self.group:
                raise GroupMembershipError(
                    f"{self.quantity_label}: {member.station!r} is "
                    f"classified {actual!r}, not the declared group "
                    f"{self.group!r}"
                )

    @property
    def n_stations(self) -> int:
        return len(self.members)

    @property
    def pearson_r(self) -> float:
        if self.n_stations < _PEARSON_R_MIN_STATIONS:
            raise InsufficientObservationsError(
                f"{self.quantity_label}: only {self.n_stations} station(s) "
                f"in group {self.group} -- a within-group Pearson "
                f"correlation needs at least {_PEARSON_R_MIN_STATIONS}"
            )
        ordered = sorted(self.members, key=lambda m: str(m.station))
        return _pearson_r(
            tuple(m.elev_m for m in ordered), tuple(m.value for m in ordered)
        )


def within_group_elevation_relationship(
    quantity_label: str,
    group: ResolutionGroupLabel,
    observations: tuple[GroupElevationObservation, ...],
    *,
    station_group: Mapping[Station, ResolutionGroupLabel],
) -> WithinGroupElevationRelationship:
    return WithinGroupElevationRelationship(
        quantity_label=quantity_label,
        group=group,
        members=observations,
        station_group=station_group,
    )
