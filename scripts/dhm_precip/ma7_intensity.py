"""Plan 193 (M-A7) task T2 — wet-hour intensity distributions.

D1 — computed on Plan 184 T1's gauge-only masked population, exactly as
`ma7_profiles` (D10: one masked series feeding both T1 and T2, never a
second implementation).

D4 PINNED — frequency statistics (wet-hour fraction, the q50/q99 body/tail
split) use the 0.2 mm/h harmonised floor (`params.wet_threshold_mm_per_h`);
the mass statistic is the station's TOTAL RETAINED PRECIPITATION MASS in
mm over the reported season, UNTHRESHOLDED (every retained hour, wet or
dry), carrying its own retained-hour `n`. Never mixed.

D3/D5 PINNED — body = the distribution up to q50, tail = q99, matching
`stats_precision.leave_one_out_tail_prediction_error`'s own choice (T3's
transferability headline consumes exactly these two numbers).

D5a — a band distribution gives each member station EQUAL PROBABILITY
MASS, never equal observation count (station wet-hour counts span
732-5,232) — implemented as a weighted quantile with per-station weight
`1 / n_station`. The pooled (equal-observation, retention-weighted) form is
exposed separately as a named sensitivity, never the headline.

D9 PINNED — as `ma7_profiles`: whole-season-year resampling, percentile
2.5/97.5 interval, `params.ma7_bootstrap_resamples` resamples, an injected
seeded RNG.

Exposure (2026-08-27 amendment) — every distribution carries its retained
wet-hour count AND its total retained-hour count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from scripts.dhm_precip.ma6_estimands import ElevationBand, assign_elevation_band
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries
from scripts.dhm_precip.ma7_profiles import (
    DuplicateBandMemberError,
    EmptyBootstrapResultError,
    MixedSelectionParamsError,
    NonPositiveResampleCountError,
    NoSeasonYearsError,
    season_frame,
    season_year_expr,
)
from scripts.dhm_precip.numeric import as_float
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping, Sequence

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.seasons import Season

_DEFAULT_MIN_SEASON_YEARS = DEFAULT_PARAMS.coloc_min_season_years_for_adequacy
"""Same alias as ma7_profiles, module-level only for line length."""

_BODY_QUANTILE = 0.5
_TAIL_QUANTILE = 0.99


class BandMembershipError(ValueError):
    """A `BandIntensityDistribution` member's own elevation does not place
    it in the declared `band` (re-derived through `assign_elevation_band`),
    or the member has no known elevation at all — same discipline as
    `ma7_profiles.BandDiurnalProfile`."""


class MixedSeasonError(ValueError):
    """`BandIntensityDistribution.members` carried distributions computed
    for more than one `Season`."""


def _wet_predicate(params: DhmPrecipParams) -> pl.Expr:
    threshold = pl.lit(params.wet_threshold_mm_per_h)
    if params.wet_threshold_side == ">=":
        return pl.col("value_mm") >= threshold
    return pl.col("value_mm") > threshold


@dataclass(frozen=True, kw_only=True, slots=True)
class StationIntensityDistribution:
    """One station's wet-hour intensity distribution for one D8 season
    (D1). `series` -> `station` is derived, never a separately-suppliable
    field — `ma7_profiles.StationDiurnalProfile`'s own discipline."""

    series: MaskedGaugeSeries
    season: Season
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        if not isinstance(self.series, MaskedGaugeSeries):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                f"StationIntensityDistribution.series must be a MaskedGaugeSeries, "
                f"got {type(self.series)}"
            )

    @property
    def station(self) -> Station:
        return self.series.station

    @property
    def _season_frame(self) -> pl.DataFrame:
        return season_frame(self.series.frame, self.season, self.params).filter(
            pl.col("value_mm").is_not_null()
        )

    @property
    def _wet_frame(self) -> pl.DataFrame:
        """D4 — the 0.2 mm/h floor applied to the season's retained hours;
        this is the population every frequency statistic (wet-hour
        fraction, q50, q99) is computed over."""
        return self._season_frame.filter(_wet_predicate(self.params))

    @property
    def n_total_retained(self) -> int:
        """All retained hours in this season, wet or dry — the exposure
        figure the D4-pinned mass statistic carries."""
        return self._season_frame.height

    @property
    def n_wet_retained(self) -> int:
        """D4 — retained hours at or above the 0.2 mm/h floor."""
        return self._wet_frame.height

    @property
    def wet_hour_fraction(self) -> float | None:
        n_total = self.n_total_retained
        if n_total == 0:
            return None
        return self.n_wet_retained / n_total

    def quantile_mm_per_h(self, q: float) -> float | None:
        """D4 — a quantile of the WET-hour population (frequency floor
        applied). `None` when the season has zero retained wet hours."""
        wet = self._wet_frame
        if wet.height == 0:
            return None
        return as_float(
            wet.select(
                pl.col("value_mm").quantile(
                    q, interpolation=self.params.quantile_definition
                )
            ).item()
        )

    @property
    def q50_mm_per_h(self) -> float | None:
        """D3/D5 PINNED — the body estimand."""
        return self.quantile_mm_per_h(_BODY_QUANTILE)

    @property
    def q99_mm_per_h(self) -> float | None:
        """D3/D5 PINNED — the tail estimand."""
        return self.quantile_mm_per_h(_TAIL_QUANTILE)

    @property
    def total_retained_mass_mm(self) -> float | None:
        """D4 PINNED — the station's total retained precipitation mass over
        the season, UNTHRESHOLDED (every retained hour, including dry hours
        contributing 0.0 mm), carrying `n_total_retained`. `None` when the
        season has zero retained hours entirely."""
        season = self._season_frame
        if season.height == 0:
            return None
        return as_float(season.select(pl.col("value_mm").sum()).item())


def _member_wet_values_mm(member: StationIntensityDistribution) -> list[float]:
    """One member's own wet-hour values, recomputed from the SAME
    primitives `StationIntensityDistribution._wet_frame` itself uses
    (`season_frame` + `_wet_predicate`) — a band aggregate never reaches
    into another type's private property to get them."""
    wet = season_frame(member.series.frame, member.season, member.params).filter(
        pl.col("value_mm").is_not_null() & _wet_predicate(member.params)
    )
    return wet["value_mm"].to_list()


def _wet_values_by_season_year(
    frame: pl.DataFrame, season: Season, params: DhmPrecipParams
) -> dict[int, list[float]]:
    """One station's own wet-hour values, grouped by D8 season-year (D8's
    ⛔ DJF warning applies here exactly as in `ma7_profiles`)."""
    within_season = season_frame(frame, season, params).filter(
        pl.col("value_mm").is_not_null()
    )
    wet = within_season.filter(_wet_predicate(params))
    with_year = wet.with_columns(season_year_expr(season, params).alias("season_year"))
    by_year: dict[int, list[float]] = {}
    for row in with_year.select("season_year", "value_mm").iter_rows(named=True):
        by_year.setdefault(int(row["season_year"]), []).append(
            as_float(row["value_mm"])
        )
    return by_year


@dataclass(frozen=True, kw_only=True, slots=True)
class QuantileBootstrap:
    """D9 PINNED — a percentile-method (2.5/97.5) bootstrap interval on a
    wet-hour quantile (q50 or q99), resampling whole season-years.
    `adequate_sample` is `n_season_years >= min_season_years_for_adequacy`
    (Plan 182 D5's bar, 5) — gate on THIS, never on interval width alone."""

    quantile: float
    point_estimate_mm_per_h: float | None
    resampled_values_mm_per_h: tuple[float, ...]
    ci_low_mm_per_h: float
    ci_high_mm_per_h: float
    n_season_years: int
    adequate_sample: bool

    def __post_init__(self) -> None:
        if not 0.0 < self.quantile < 1.0:
            raise ValueError(f"quantile must be in (0, 1), got {self.quantile}")
        if self.n_season_years < 0:
            raise ValueError(f"n_season_years must be >= 0, got {self.n_season_years}")


def _bootstrap_quantile_from_year_table(
    by_year: Mapping[int, Sequence[float]],
    *,
    quantile: float,
    point_estimate: float | None,
    rng: random.Random,
    n_resamples: int,
    min_season_years_for_adequacy: int,
    percentile_low: float,
    percentile_high: float,
) -> QuantileBootstrap:
    if n_resamples < 1:
        raise NonPositiveResampleCountError(
            f"n_resamples must be >= 1, got {n_resamples}"
        )
    years = sorted(by_year)
    n_season_years = len(years)
    if n_season_years == 0:
        raise NoSeasonYearsError("zero season-years available for the D9 bootstrap")

    resampled: list[float] = []
    for _ in range(n_resamples):
        drawn = [rng.choice(years) for _ in range(n_season_years)]
        pooled = [v for year in drawn for v in by_year[year]]
        if not pooled:
            continue
        resampled.append(
            float(np.quantile(np.asarray(pooled, dtype=np.float64), quantile))
        )

    if not resampled:
        raise EmptyBootstrapResultError(
            "every bootstrap resample produced zero wet hours"
        )
    lo = float(np.percentile(resampled, percentile_low))
    hi = float(np.percentile(resampled, percentile_high))
    return QuantileBootstrap(
        quantile=quantile,
        point_estimate_mm_per_h=point_estimate,
        resampled_values_mm_per_h=tuple(resampled),
        ci_low_mm_per_h=lo,
        ci_high_mm_per_h=hi,
        n_season_years=n_season_years,
        adequate_sample=n_season_years >= min_season_years_for_adequacy,
    )


def bootstrap_station_quantile(
    distribution: StationIntensityDistribution,
    *,
    quantile: float,
    rng: random.Random,
    n_resamples: int = DEFAULT_PARAMS.ma7_bootstrap_resamples,
    min_season_years_for_adequacy: int = _DEFAULT_MIN_SEASON_YEARS,
) -> QuantileBootstrap:
    by_year = _wet_values_by_season_year(
        distribution.series.frame, distribution.season, distribution.params
    )
    return _bootstrap_quantile_from_year_table(
        by_year,
        quantile=quantile,
        point_estimate=distribution.quantile_mm_per_h(quantile),
        rng=rng,
        n_resamples=n_resamples,
        min_season_years_for_adequacy=min_season_years_for_adequacy,
        percentile_low=distribution.params.ma7_bootstrap_percentile_low,
        percentile_high=distribution.params.ma7_bootstrap_percentile_high,
    )


def _weighted_quantile(
    values: Sequence[float], weights: Sequence[float], q: float
) -> float:
    """D5a's station-equal-probability-mass mechanism: each observation's
    weight is normalised so every STATION's total weight is equal (never
    equal observation count), then the quantile is read off the weighted
    empirical CDF (linear interpolation on cumulative-weight midpoints —
    the standard weighted-percentile construction)."""
    arr = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    order = np.argsort(arr)
    arr = arr[order]
    w = w[order]
    cum_weights = np.cumsum(w) - 0.5 * w
    cum_weights /= np.sum(w)
    return float(np.interp(q, cum_weights, arr))


@dataclass(frozen=True, kw_only=True, slots=True)
class BandIntensityDistribution:
    """D5a — every quantile computed here gives each member station EQUAL
    PROBABILITY MASS (`station_equal_quantile`), never equal observation
    count. `pooled_quantile` is the plain (equal-observation, retention-
    weighted) sensitivity form.

    `members`/`band`/`station_elev_m` are the only stored fields — every
    other value is a `@property` derived from them (same structural
    convention as `ma7_profiles.BandDiurnalProfile` and
    `ma6_estimands.ElevationBandEstimand`)."""

    band: ElevationBand
    members: tuple[StationIntensityDistribution, ...]
    station_elev_m: Mapping[Station, float]

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError(f"{self.band}: zero member stations")
        stations = tuple(m.station for m in self.members)
        if len(set(stations)) != len(stations):
            raise DuplicateBandMemberError(
                f"{self.band}: members carries a repeated station: {stations}"
            )
        seasons = {m.season for m in self.members}
        if len(seasons) > 1:
            raise MixedSeasonError(
                f"{self.band}: members span more than one season: {seasons}"
            )
        selection_params = {m.params for m in self.members}
        if len(selection_params) > 1:
            raise MixedSelectionParamsError(
                f"{self.band}: members were selected under different DhmPrecipParams"
            )
        for station in stations:
            if station not in self.station_elev_m:
                raise BandMembershipError(
                    f"{self.band}: {station!r} has no known elevation in station_elev_m"
                )
            actual = assign_elevation_band(self.station_elev_m[station])
            if actual is not self.band:
                raise BandMembershipError(
                    f"{self.band}: {station!r} at {self.station_elev_m[station]} m "
                    f"belongs to {actual} (D4a edges), not the declared {self.band}"
                )

    @property
    def season(self) -> Season:
        return self.members[0].season

    @property
    def params(self) -> DhmPrecipParams:
        return self.members[0].params

    @property
    def station_count(self) -> int:
        return len(self.members)

    @property
    def n_wet_retained(self) -> int:
        """Sum of member wet-hour counts — an exposure diagnostic, never a
        weight in `station_equal_quantile` (D5a)."""
        return sum(m.n_wet_retained for m in self.members)

    @property
    def n_total_retained(self) -> int:
        return sum(m.n_total_retained for m in self.members)

    def station_equal_quantile(self, q: float) -> float | None:
        """D5a headline: every member contributes EQUAL total probability
        mass (weight `1 / n_station`), never equal observation count."""
        values: list[float] = []
        weights: list[float] = []
        for member in self.members:
            member_values = _member_wet_values_mm(member)
            n = len(member_values)
            if n == 0:
                continue
            values.extend(member_values)
            weights.extend([1.0 / n] * n)
        if not values:
            return None
        return _weighted_quantile(values, weights, q)

    def pooled_quantile(self, q: float) -> float | None:
        """D5a sensitivity: the plain (equal-observation-weight) quantile
        over every member's raw wet-hour values pooled together — the
        wettest-sampled station dominates here, unlike the headline."""
        values: list[float] = []
        for member in self.members:
            values.extend(_member_wet_values_mm(member))
        if not values:
            return None
        return float(np.quantile(np.asarray(values, dtype=np.float64), q))

    @property
    def station_equal_q50_mm_per_h(self) -> float | None:
        return self.station_equal_quantile(_BODY_QUANTILE)

    @property
    def station_equal_q99_mm_per_h(self) -> float | None:
        return self.station_equal_quantile(_TAIL_QUANTILE)

    @property
    def pooled_q50_mm_per_h(self) -> float | None:
        return self.pooled_quantile(_BODY_QUANTILE)

    @property
    def pooled_q99_mm_per_h(self) -> float | None:
        return self.pooled_quantile(_TAIL_QUANTILE)


def bootstrap_band_quantile(
    distribution: BandIntensityDistribution,
    *,
    quantile: float,
    rng: random.Random,
    n_resamples: int = DEFAULT_PARAMS.ma7_bootstrap_resamples,
    min_season_years_for_adequacy: int = _DEFAULT_MIN_SEASON_YEARS,
) -> QuantileBootstrap:
    """D5a — band adequacy (D9) uses the season-years COMMON to every
    member station, not their union. Each resample draws from that
    intersection; each drawn year's pooled sample gives every member
    station EQUAL PROBABILITY MASS within that year (weight `1 /
    n_station_that_year`), the same station-equal discipline as the point
    estimate."""
    per_member_by_year = [
        _wet_values_by_season_year(
            m.series.frame, distribution.season, distribution.params
        )
        for m in distribution.members
    ]
    common_years: set[int] = set(per_member_by_year[0]) if per_member_by_year else set()
    for d in per_member_by_year[1:]:
        common_years &= set(d)
    years = sorted(common_years)
    n_season_years = len(years)
    if n_season_years == 0:
        raise NoSeasonYearsError("zero season-years common to every band member")
    if n_resamples < 1:
        raise NonPositiveResampleCountError(
            f"n_resamples must be >= 1, got {n_resamples}"
        )

    resampled: list[float] = []
    for _ in range(n_resamples):
        drawn = [rng.choice(years) for _ in range(n_season_years)]
        values: list[float] = []
        weights: list[float] = []
        for year in drawn:
            for member_years in per_member_by_year:
                year_values = member_years.get(year, [])
                n = len(year_values)
                if n == 0:
                    continue
                values.extend(year_values)
                weights.extend([1.0 / n] * n)
        if not values:
            continue
        resampled.append(_weighted_quantile(values, weights, quantile))

    if not resampled:
        raise EmptyBootstrapResultError(
            "every bootstrap resample produced zero wet hours"
        )
    params = distribution.params
    lo = float(np.percentile(resampled, params.ma7_bootstrap_percentile_low))
    hi = float(np.percentile(resampled, params.ma7_bootstrap_percentile_high))
    return QuantileBootstrap(
        quantile=quantile,
        point_estimate_mm_per_h=distribution.station_equal_quantile(quantile),
        resampled_values_mm_per_h=tuple(resampled),
        ci_low_mm_per_h=lo,
        ci_high_mm_per_h=hi,
        n_season_years=n_season_years,
        adequate_sample=n_season_years >= min_season_years_for_adequacy,
    )
