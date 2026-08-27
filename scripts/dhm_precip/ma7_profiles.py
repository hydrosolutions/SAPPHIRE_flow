"""Plan 193 (M-A7) task T1 — masked diurnal profiles with exposure.

D1 — every profile is computed on Plan 184 T1's gauge-only M-A3-masked
population (`ma6_pairs.load_gauge_masked_population`/`GaugeMaskedPopulation`),
never on unmasked `on_grid` (the trap the plan names: `pipeline.py`'s
`diurnal_profiles` feeds unmasked data and is not reused here).

D2 — every hour-of-day mean carries its own retained-hour count as a
`@property` derived from the frame it was computed on (`HourlyMean.
n_retained`), never a separately-suppliable field — the same discipline
`ma6_pairs`/`ma6_estimands` already apply to every other exposure count in
this track.

D8 — all four seasons (MAM, JJAS, ON, DJF) are reported for every station,
never filtered on retention; the DJF season-year uses `params.
year_attribution` (december belongs to the FOLLOWING DJF), never a bare
`timestamp.dt.year()` (D8's own measured warning: naive year-grouping
mislabels Lete's and Olangchunggola's DJF adequacy).

D5a — a band profile is the UNWEIGHTED MEAN of its member stations' own
profiles (station-equal), never a pooled, retention-weighted mean; the
pooled form is exposed separately as a named sensitivity. Band adequacy
(D9) is computed over the season-years COMMON to every member station, not
their union.

D9 PINNED — the bootstrap resamples whole season-years (never individual
hours — serial correlation), over `params.ma7_bootstrap_resamples` resamples
and an injected seeded RNG. Peak hour is CIRCULAR (hour-of-day), so the
interval is `circular.circular_range_hours` over the resampled peak hours —
`coloc_bootstrap.BootstrapPeakSpread`'s own construction — never a linear
percentile low/high pair (2026-08-27 correction: a linear 2.5/97.5 interval
on the `< 700 m` band measured 21.0 h wide against a 6.0 h circular spread,
for resamples straddling midnight). The percentile-method 2.5/97.5 interval
stays correct for `ma7_intensity`'s LINEAR q50/q99 bootstraps — this
correction is peak-hour-only.

D7 — Olangchunggola's open 03 UTC anomaly is recorded, not adjudicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.circular import circular_range_hours
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand, assign_elevation_band
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries
from scripts.dhm_precip.numeric import as_float, as_int
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams
from scripts.dhm_precip.seasons import Season

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping

_HOURS: tuple[int, ...] = tuple(range(24))
_DEFAULT_MIN_SEASON_YEARS = DEFAULT_PARAMS.coloc_min_season_years_for_adequacy
"""Plan 182 D5's own adequacy bar (>= 5 season-years), reused verbatim
(D9 pinned: "the ADEQUACY FLAG... Plan 182 D5") -- module-level alias only
to keep default-argument lines under the line-length limit."""

OLANGCHUNGGOLA = Station("Olangchunggola")
"""D7 — the one station whose 03 UTC peak is a recorded, open anomaly: zero
sentinels, immovable across M-A10's ablation ladder, no co-located station
exists to adjudicate against. Named here, once, so callers never re-type
the station string."""

_OLANGCHUNGGOLA_D7_NOTE = (
    "D7 — Olangchunggola's 03 UTC peak is REPORTED, not adjudicated: zero "
    "sentinels, immovable across M-A10's ablation ladder, and no co-located "
    "station exists to adjudicate against. Status recorded as OPEN, not "
    "resolved by this milestone."
)


class NonPositiveResampleCountError(ValueError):
    """A bootstrap was asked for `n_resamples < 1` — never silently clamped."""


class NoSeasonYearsError(ValueError):
    """A station or band has zero season-years available for the D9
    bootstrap over the requested season — e.g. a degenerate season (D8:
    reported, never omitted, but a bootstrap genuinely cannot run on zero
    years). Callers label this explicitly rather than treating an empty
    bootstrap as a spread of 0.0."""


class EmptyBootstrapResultError(ValueError):
    """Every resample produced no observed hour — should not occur once
    `NoSeasonYearsError` has already ruled out zero season-years, but never
    silently reported as a valid interval if it somehow does."""


class DuplicateBandMemberError(ValueError):
    """A `BandDiurnalProfile.members` tuple carried the same station more
    than once — a band profile is the unweighted mean over DISTINCT member
    stations (D5a); a repeated station would silently double-count it."""


class MixedSeasonError(ValueError):
    """`BandDiurnalProfile.members` carried profiles computed for more than
    one `Season` — a band profile is defined for exactly one season at a
    time, never a mix."""


class MixedSelectionParamsError(ValueError):
    """`BandDiurnalProfile.members` carried profiles selected under
    different `DhmPrecipParams` — a band mean would then publish one number
    over two differently-selected populations (the same commensurability
    check `ma6_estimands.ElevationBandEstimand` already applies)."""


class BandMembershipError(ValueError):
    """A `BandDiurnalProfile` member's own elevation does not place it in
    the declared `band` (re-derived through `assign_elevation_band`, D4a's
    own edges — never a separately-suppliable label), or the member has no
    known elevation at all."""


def _season_months(season: Season, params: DhmPrecipParams) -> frozenset[int]:
    return frozenset(
        {
            Season.MAM: params.mam_months,
            Season.JJAS: params.jjas_months,
            Season.ON: params.on_months,
            Season.DJF: params.djf_months,
        }[season]
    )


def season_year_expr(season: Season, params: DhmPrecipParams) -> pl.Expr:
    """D8's ⛔ warning: a DJF season-year is NOT a calendar year. MAM/JJAS/ON
    never cross a year boundary, so their season-year IS the timestamp's
    calendar year. DJF's season-year is read from `params.year_attribution`
    — the only value the `Literal` currently admits is "december_belongs_
    to_following_djf" (December's season-year is the FOLLOWING January/
    February's calendar year); a future second value would need this
    branch extended, so it is asserted here rather than silently assumed."""
    if season is not Season.DJF:
        return pl.col("timestamp").dt.year()
    if params.year_attribution != "december_belongs_to_following_djf":
        raise ValueError(
            f"unsupported year_attribution {params.year_attribution!r} — "
            "season_year_expr only implements 'december_belongs_to_following_djf'"
        )
    return (
        pl.when(pl.col("timestamp").dt.month() == 12)
        .then(pl.col("timestamp").dt.year() + 1)
        .otherwise(pl.col("timestamp").dt.year())
    )


def season_frame(
    frame: pl.DataFrame, season: Season, params: DhmPrecipParams
) -> pl.DataFrame:
    """`frame` restricted to `season`'s calendar months — the one place
    every profile/distribution in this module and `ma7_intensity` filters
    by season, so the four D8 seasons are always the SAME partition."""
    return frame.filter(
        pl.col("timestamp").dt.month().is_in(_season_months(season, params))
    )


def per_season_year_hourly_means(
    frame: pl.DataFrame, season: Season, params: DhmPrecipParams
) -> pl.DataFrame:
    """`(station, timestamp, value_mm)` -> `(season_year, hour,
    mean_value_mm)` for one station's masked series, restricted to `season`.
    Generalises `coloc_bootstrap.per_season_hourly_means` (JJAS-only by its
    own docstring) to all four D8 seasons via `season_year_expr`."""
    within_season = season_frame(frame, season, params).filter(
        pl.col("value_mm").is_not_null()
    )
    with_year_hour = within_season.with_columns(
        season_year_expr(season, params).alias("season_year"),
        pl.col("timestamp").dt.hour().alias("hour"),
    )
    return with_year_hour.group_by(["season_year", "hour"]).agg(
        pl.col("value_mm").mean().alias("mean_value_mm")
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class HourlyMean:
    """One hour-of-day cell of a diurnal profile (D2 — exposure travels with
    every profile). `n_retained == 0` iff `mean_value_mm is None` — an hour
    with zero retained observations carries no mean, never a silent 0.0."""

    hour: int
    mean_value_mm: float | None
    n_retained: int

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError(f"hour must be 0-23, got {self.hour}")
        if self.n_retained < 0:
            raise ValueError(f"n_retained must be >= 0, got {self.n_retained}")
        if (self.n_retained == 0) != (self.mean_value_mm is None):
            raise ValueError(
                f"hour {self.hour}: n_retained={self.n_retained} and "
                f"mean_value_mm={self.mean_value_mm!r} disagree on whether "
                "this hour has any retained observation"
            )


def _peak_hour(hourly: tuple[HourlyMean, ...]) -> int | None:
    """argmax over hours with retained data, ties broken toward the LARGER
    hour — the same tie-break `coloc_bootstrap.bootstrap_peak_hour_spread`
    uses. Ranking by raw `mean_value_mm` (rather than a grand-mean-
    normalised profile) gives the identical argmax whenever the grand mean
    is positive, since normalising divides every hour by the SAME positive
    scalar — so no normalisation step is needed just to find the peak."""
    observed = [h for h in hourly if h.n_retained > 0 and h.mean_value_mm is not None]
    if not observed:
        return None
    return max(observed, key=lambda h: (h.mean_value_mm, h.hour)).hour  # type: ignore[operator]


@dataclass(frozen=True, kw_only=True, slots=True)
class StationDiurnalProfile:
    """One station's masked diurnal profile for one D8 season (D1). `series`
    is the `GaugeMaskedPopulation` member this was computed from — `station`
    is derived from it, never a separately-suppliable field, following
    `ma6_pairs`'s own discipline."""

    series: MaskedGaugeSeries
    season: Season
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        if not isinstance(self.series, MaskedGaugeSeries):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                f"StationDiurnalProfile.series must be a MaskedGaugeSeries, "
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
    def n_season_retained(self) -> int:
        """Total retained hours in this season, across all hours-of-day —
        the season-level exposure figure alongside the per-hour ones."""
        return self._season_frame.height

    @property
    def hourly(self) -> tuple[HourlyMean, ...]:
        """24 rows, hour 0-23, each carrying its own retained count (D2). An
        hour with zero retained observations in this season still appears,
        with `n_retained == 0` and `mean_value_mm is None` — never omitted
        (D8: stratify by retention, never filter on it)."""
        agg = (
            self._season_frame.with_columns(pl.col("timestamp").dt.hour().alias("hour"))
            .group_by("hour")
            .agg(
                pl.col("value_mm").mean().alias("mean_value_mm"),
                pl.len().alias("n_retained"),
            )
        )
        by_hour: dict[int, tuple[float, int]] = {
            as_int(row["hour"]): (
                as_float(row["mean_value_mm"]),
                as_int(row["n_retained"]),
            )
            for row in agg.iter_rows(named=True)
        }
        return tuple(
            HourlyMean(
                hour=h,
                mean_value_mm=by_hour[h][0] if h in by_hour else None,
                n_retained=by_hour[h][1] if h in by_hour else 0,
            )
            for h in _HOURS
        )

    @property
    def peak_hour(self) -> int | None:
        """`None` when this season is degenerate for this station (zero
        retained hours) — a degenerate season is reported, not omitted
        (D8), but has no peak to name."""
        return _peak_hour(self.hourly)

    @property
    def open_anomaly_note(self) -> str | None:
        """D7 — non-`None` for exactly Olangchunggola; every other station
        carries `None`."""
        return _OLANGCHUNGGOLA_D7_NOTE if self.station == OLANGCHUNGGOLA else None


@dataclass(frozen=True, kw_only=True, slots=True)
class PeakHourBootstrap:
    """D9 (2026-08-27 corrected) — a CIRCULAR bootstrap spread on the peak
    hour, resampling whole season-years. Hour-of-day is a point on a 24h
    circle, never a linear scalar, so the spread is `circular.
    circular_range_hours` over `resampled_peak_hours` — `coloc_bootstrap.
    BootstrapPeakSpread`'s own construction — never a linear percentile
    low/high pair (measured: a linear 2.5/97.5 interval read 21.0h on a
    distribution whose true circular spread is 6.0h). `adequate_sample` is
    `n_season_years >= min_season_years_for_adequacy` (Plan 182 D5's own
    bar, 5) — a caller must gate on THIS, never on spread width alone."""

    peak_hour: int | None
    """The POINT ESTIMATE's own peak hour (the full season, not a
    resample) — `None` exactly when the underlying profile's is."""
    resampled_peak_hours: tuple[int, ...]
    spread_hours: float
    n_season_years: int
    adequate_sample: bool

    def __post_init__(self) -> None:
        if self.n_season_years < 0:
            raise ValueError(f"n_season_years must be >= 0, got {self.n_season_years}")
        if self.spread_hours < 0:
            raise ValueError(f"spread_hours must be >= 0, got {self.spread_hours}")


def _bootstrap_peak_hour_from_year_table(
    by_year: Mapping[int, Mapping[int, float]],
    *,
    point_estimate_peak_hour: int | None,
    rng: random.Random,
    n_resamples: int,
    min_season_years_for_adequacy: int,
) -> PeakHourBootstrap:
    if n_resamples < 1:
        raise NonPositiveResampleCountError(
            f"n_resamples must be >= 1, got {n_resamples}"
        )
    season_years = sorted(by_year)
    n_season_years = len(season_years)
    if n_season_years == 0:
        raise NoSeasonYearsError("zero season-years available for the D9 bootstrap")

    peak_hours: list[int] = []
    for _ in range(n_resamples):
        drawn = [rng.choice(season_years) for _ in range(n_season_years)]
        sums = [0.0] * 24
        counts = [0] * 24
        for year in drawn:
            for hour, value in by_year[year].items():
                sums[hour] += value
                counts[hour] += 1
        observed = [h for h in range(24) if counts[h] > 0]
        if not observed:
            continue
        means = {h: sums[h] / counts[h] for h in observed}
        peak_hours.append(max(observed, key=lambda h: (means[h], h)))

    if not peak_hours:
        raise EmptyBootstrapResultError(
            "every bootstrap resample produced no observed hour"
        )
    spread = circular_range_hours([float(h) for h in peak_hours])
    return PeakHourBootstrap(
        peak_hour=point_estimate_peak_hour,
        resampled_peak_hours=tuple(peak_hours),
        spread_hours=spread,
        n_season_years=n_season_years,
        adequate_sample=n_season_years >= min_season_years_for_adequacy,
    )


def _year_hour_dict(per_year_hourly: pl.DataFrame) -> dict[int, dict[int, float]]:
    by_year: dict[int, dict[int, float]] = {}
    for row in per_year_hourly.iter_rows(named=True):
        by_year.setdefault(as_int(row["season_year"]), {})[as_int(row["hour"])] = (
            as_float(row["mean_value_mm"])
        )
    return by_year


def bootstrap_station_peak_hour(
    profile: StationDiurnalProfile,
    *,
    rng: random.Random,
    n_resamples: int = DEFAULT_PARAMS.ma7_bootstrap_resamples,
    min_season_years_for_adequacy: int = _DEFAULT_MIN_SEASON_YEARS,
) -> PeakHourBootstrap:
    """D9 — resamples whole season-years of `profile`'s own station/season."""
    per_year = per_season_year_hourly_means(
        profile.series.frame, profile.season, profile.params
    )
    return _bootstrap_peak_hour_from_year_table(
        _year_hour_dict(per_year),
        point_estimate_peak_hour=profile.peak_hour,
        rng=rng,
        n_resamples=n_resamples,
        min_season_years_for_adequacy=min_season_years_for_adequacy,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class BandDiurnalProfile:
    """D5a — the band profile is the UNWEIGHTED MEAN of its member stations'
    own profiles (`station_equal_hourly`), never a pooled, retention-
    weighted mean — an MNAR mask would otherwise let a high-retention
    station dominate. `pooled_hourly` is the SAME data pooled by raw hour
    instead, exposed as a named sensitivity (D5a), never the headline.

    `members`/`band`/`station_elev_m` are the only stored fields; every
    other value is a `@property` derived from them, so direct construction
    is exactly as safe as a factory (this track's structural convention,
    `ma6_estimands.ElevationBandEstimand`)."""

    band: ElevationBand
    members: tuple[StationDiurnalProfile, ...]
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
    def station_equal_hourly(self) -> tuple[HourlyMean, ...]:
        """D5a's headline: unweighted mean, per hour, across members that
        have data at that hour. `n_retained` carried per hour is the SUM of
        member retained-hour counts — an exposure diagnostic (D2), never a
        weight in the mean itself."""
        result: list[HourlyMean] = []
        for hour in _HOURS:
            member_hours = [m.hourly[hour] for m in self.members]
            n_total = sum(h.n_retained for h in member_hours)
            # Filter and yield `h.mean_value_mm` in the SAME comprehension so
            # pyright narrows it to `float` (never `None`) for every element
            # of `values` — the `HourlyMean` invariant (n_retained == 0 iff
            # mean_value_mm is None) already guarantees this at runtime.
            values = [
                h.mean_value_mm for h in member_hours if h.mean_value_mm is not None
            ]
            mean = sum(values) / len(values) if values else None
            result.append(HourlyMean(hour=hour, mean_value_mm=mean, n_retained=n_total))
        return tuple(result)

    @property
    def pooled_hourly(self) -> tuple[HourlyMean, ...]:
        """D5a's sensitivity: the retention-weighted pooled mean —
        `sum(mean_i * n_i) / sum(n_i)` over members, the arithmetic-
        identity reconstruction of the true pooled mean over raw retained
        values (`ma6_representativeness.compute_within_cell_pair`'s own
        technique) without re-reading raw rows."""
        result: list[HourlyMean] = []
        for hour in _HOURS:
            member_hours = [m.hourly[hour] for m in self.members]
            n_total = sum(h.n_retained for h in member_hours)
            if n_total == 0:
                result.append(HourlyMean(hour=hour, mean_value_mm=None, n_retained=0))
                continue
            # Same same-comprehension narrowing trick as `station_equal_hourly`.
            weighted_pairs = [
                (h.mean_value_mm, h.n_retained)
                for h in member_hours
                if h.mean_value_mm is not None
            ]
            weighted_sum = sum(mean * n for mean, n in weighted_pairs)
            result.append(
                HourlyMean(
                    hour=hour, mean_value_mm=weighted_sum / n_total, n_retained=n_total
                )
            )
        return tuple(result)

    @property
    def station_equal_peak_hour(self) -> int | None:
        return _peak_hour(self.station_equal_hourly)

    @property
    def pooled_peak_hour(self) -> int | None:
        return _peak_hour(self.pooled_hourly)


def bootstrap_band_peak_hour(
    profile: BandDiurnalProfile,
    *,
    rng: random.Random,
    n_resamples: int = DEFAULT_PARAMS.ma7_bootstrap_resamples,
    min_season_years_for_adequacy: int = _DEFAULT_MIN_SEASON_YEARS,
) -> PeakHourBootstrap:
    """D5a — band adequacy (D9) uses the UNION of member stations'
    season-years (owner decision 2026-08-27), and each drawn year's per-hour
    value is the station-equal mean across whichever members have data for
    that (year, hour) cell. An INTERSECTION would quantify a population the
    reported point estimate does not come from: the point estimate uses each
    station's FULL record, so restricting resamples to common years made the
    interval describe different data than the number it annotates — and it
    collapsed `700-2,000 m` to ONE common season-year against a union of six."""
    per_member_year_hour = [
        _year_hour_dict(
            per_season_year_hourly_means(m.series.frame, profile.season, profile.params)
        )
        for m in profile.members
    ]
    union_years: set[int] = set()
    for d in per_member_year_hour:
        union_years |= set(d)

    band_year_hour: dict[int, dict[int, float]] = {}
    for year in sorted(union_years):
        row: dict[int, float] = {}
        for hour in _HOURS:
            values = [
                d[year][hour] for d in per_member_year_hour if hour in d.get(year, {})
            ]
            if values:
                row[hour] = sum(values) / len(values)
        band_year_hour[year] = row

    return _bootstrap_peak_hour_from_year_table(
        band_year_hour,
        point_estimate_peak_hour=profile.station_equal_peak_hour,
        rng=rng,
        n_resamples=n_resamples,
        min_season_years_for_adequacy=min_season_years_for_adequacy,
    )
