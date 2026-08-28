"""Plan 205 (M-A8) task T2 — the apparent rain-phase gradient.

D4/D5's mandatory name — **"apparent rain-phase gradient, uncorrected for
wind catch"**, never "the precipitation gradient" or "the precipitation
lapse rate" — describes rain-phase hours on ONE transect in ONE valley,
screened on Pyramid's own `AT`. Pyramid's gauges are unheated, so
undercatch grows with elevation; the rain-only screen is what makes the
claim legitimate (it is the condition under which M-A10's "undercatch
largely cancels" is actually true), but it removes the SNOW problem, not
the WIND-CATCH problem (D4 (ii)). Rain catch efficiency still falls with
wind, and wind exposure still rises up a transect, so the apparent gradient
is signed an **upper bound in magnitude** on any true decline
(`d log(obs)/dz = d log(true)/dz + d log(CE)/dz`, the catch term negative)
— the true decline is no steeper, and could in principle be nil.

**⛔ D4 (iii) — no wind stratification, no low-wind convergence test.** Low
wind does not select the same precipitation under better catch; it selects
DIFFERENT precipitation (stratiform vs convective), confounded with regime
rather than catch. This module implements no such test — see the module's
Non-goals.

**D9 — reuse, build no second loader.** `pyramid_loader.load_pyramid_lvl1_csv`/
`load_pyramid_lvl1_at_csv` are the ONLY functions that read a Pyramid file
here. `ma6_lapse_check.hour_of_day_equalised_mean` is the ONLY hour-of-day
equalisation used (Pyramid is irregularly sampled — M-A6 measured a 1.00 degC
naive-mean artefact from exactly this). No NPT<->UTC reconciliation is
needed for T2's own RR/AT pairing: both come from the SAME Pyramid station's
own file, on the SAME NPT clock — unlike `ma6_lapse_check`'s ERA5
comparison, there is no cross-source clock to reconcile (D4: "no
cross-source pairing... inherits none of Plan 184 D7's cell-vs-point
caveat").

**P1 — ONE common window, AWS0 excluded by name.** AWS0's own RR record
ends 2004-12-09; it is excluded from the cross-station fit BEFORE the
window is computed (excluding it AFTER would let its absence silently
shrink an already-named window). AWS0's only role is D6's same-elevation
contrast against AWS1 (`same_elevation_discrepancy`), which uses AWS0's
OWN window (2000-2004ish, wherever the two stations' own records overlap),
never the fit window. **Measured on the real archive (2026-08-27): AWS4's
own RR record — despite its `AT` continuing to 2023-12-31 — stops reporting
non-null `RR` after 2018-05-09, so the common window across the five
fit stations (AWS1-AWS5) is bounded late by AWS4's own record, not by
2023.** `compute_gradient_fit_window` computes this from each station's own
RR extent, never assumes it.

**P2 — hour-of-day equalisation does not equalise date or season
exposure.** `hour_of_day_equalised_mean` equalises HOUR-OF-DAY exposure
only; `StationRainPhaseObservation`'s mean is computed over whatever dates
survive the common window + JJAS + rain-screen filters, and that population
is NOT further equalised across stations by date or year. This module's
docstrings and every rendered label say "hour-of-day equalised", never bare
"exposure-equalised".

**P3 — the fit.** `ApparentRainPhaseGradient` is an ordinary-least-squares
line of `log(rain-phase mean hourly intensity)` on station elevation (km),
reported as percent-per-km with a 95% CI, alongside each station's own raw
value and `n`. One form, named (`fit_apparent_rain_phase_gradient`), so two
implementations of this plan are comparable.

**Non-goals (module scope):** any wind stratification or low-wind
convergence test (D4 (iii)); any extrapolation above the rain line; any
snow-phase or all-phase gradient (the unscreened case is structurally
refused, not merely undocumented); any comparison to an ERA5-Land-derived
gradient (D5 — ERA5-Land's `total_precipitation` never sees the 0.1 degree
orography, so a gradient fitted to it is the parent field on a finer grid,
not a competing estimate).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

import polars as pl

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_lapse_check import (
    TransectStation,
    hour_of_day_equalised_mean,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

APPARENT_RAIN_PHASE_GRADIENT_LABEL: Final = (
    "apparent rain-phase gradient, uncorrected for wind catch"
)
"""D4/D5's mandatory name — pinned so it cannot be silently reworded to
"the precipitation gradient" or "the precipitation lapse rate" (D5)."""

PRIMARY_RAIN_SCREEN_THRESHOLD_DEGC: Final = 1.5
"""D4/D14's primary rain screen -- still admits wet snow and mixed phase,
which is exactly why the upward sensitivity leg exists."""

RAIN_SCREEN_THRESHOLDS_DEGC: Final[tuple[float, ...]] = (0.0, 1.5, 2.0, 4.0)
"""D4's four rain-screen legs -- M-A6's 0/1.5/2 degC plus D4's own upward
4 degC extension (1.5 degC still admits wet snow; the upward leg tests
whether the gradient survives an unambiguous rain screen). The ONLY
thresholds `StationRainPhaseObservation`/`ApparentRainPhaseGradient` accept
-- any other value, in particular an unbounded threshold such as `-inf`
that would admit every temperature (i.e. no phase screening at all), is
refused by `UnscreenedGradientRefusedError`, never computed."""

# --- the six-station RR transect (D4: 2,660-5,600 m) ---

RR_TRANSECT_STATIONS: tuple[TransectStation, ...] = (
    TransectStation(
        pyramid_station=Station("AWS3 Lukla"),
        csv_filename="AWS3_Z2660_Lvl1.csv",
        lat=27.70,
        lon=86.72,
        elevation_m=2660.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS5 Namche"),
        csv_filename="AWS5_Z3570_Lvl1.csv",
        lat=27.80,
        lon=86.71,
        elevation_m=3570.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS2 Pheriche"),
        csv_filename="AWS2_Z4260_Lvl1.csv",
        lat=27.90,
        lon=86.82,
        elevation_m=4260.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS0 Pyramid"),
        csv_filename="AWS0_Z5035_Lvl1.csv",
        lat=27.96,
        lon=86.81,
        elevation_m=5035.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS1 Pyramid"),
        csv_filename="AWS1_Z5035_Lvl1.csv",
        lat=27.96,
        lon=86.81,
        elevation_m=5035.0,
    ),
    TransectStation(
        pyramid_station=Station("AWS4 Kala Patthar"),
        csv_filename="AWS4_Z5600_Lvl1.csv",
        lat=27.99,
        lon=86.83,
        elevation_m=5600.0,
    ),
)
"""D4's six RR stations (2,660-5,600 m), reusing `ma6_lapse_check.
TransectStation` (never a second station-metadata type) -- `ma6_lapse_check.
TRANSECT_STATIONS` covers only its own 5-station `AT` transect (AWS0
excluded there, since M-A6 T2 never needed AWS0's `AT`); T2 needs AWS0 for
D6's same-elevation contrast, so this module declares its own six-station
tuple rather than mutating or extending the M-A6 one."""

AWS0 = RR_TRANSECT_STATIONS[3]
AWS1 = RR_TRANSECT_STATIONS[4]
"""D6's same-elevation pair -- both named "Pyramid", both at 5,035 m."""

FIT_STATIONS: tuple[TransectStation, ...] = tuple(
    s for s in RR_TRANSECT_STATIONS if s.pyramid_station != AWS0.pyramid_station
)
"""P1 -- AWS0 excluded from the cross-station fit BY NAME, before any
window is computed."""


class UnscreenedGradientRefusedError(ValueError):
    """`threshold_degc` was not one of D4's pinned rain-screen legs
    (`RAIN_SCREEN_THRESHOLDS_DEGC`) -- in particular, a caller cannot ask
    for the ALL-PHASE (unscreened) gradient by passing an unbounded
    threshold such as `-inf`. D4: the rain-only screen is what makes this
    claim legitimate; an unscreened fit would measure the undercatch
    profile, not precipitation."""


class EmptyGroupError(ValueError):
    """An aggregate here was asked to summarise zero members."""


class DuplicateStationError(ValueError):
    """A `members`/`observations` tuple carried the same station twice."""


class MixedThresholdError(ValueError):
    """`ApparentRainPhaseGradient.observations` carried more than one
    distinct `threshold_degc` -- every member must share the ONE screen the
    gradient is declared at."""


class ZeroVarianceError(ValueError):
    """An OLS fit was requested over stations whose elevations carry zero
    variance -- undefined, not silently reported as an infinite or NaN
    slope."""


class InsufficientObservationsError(ValueError):
    """An OLS fit or a Pearson correlation was requested over fewer than
    the minimum number of usable stations."""


class UnsupportedDegreesOfFreedomError(ValueError):
    """The fit's degrees of freedom fall outside `_T_CRITICAL_95`'s pinned
    table -- refused rather than guessed at (the six-station transect can
    never legitimately need more than df=4)."""


class NotSameElevationError(ValueError):
    """`same_elevation_discrepancy` was given two stations that do not
    share one elevation -- D6's contrast is specifically a SAME-elevation
    comparison (AWS0/AWS1, both 5,035 m)."""


@dataclass(frozen=True, kw_only=True, slots=True)
class StationRainPhaseObservation:
    """One station's rain-phase-screened RR population at one `AT`
    threshold, restricted to P1's common fit window and D4's JJAS-only
    scope. `n_rain_phase_hours` and `mean_hourly_intensity_mm_per_h` are
    the whole deliverable P2 asks for: hour counts up the transect make the
    rain line VISIBLE (a station with `n_rain_phase_hours == 0` in the
    window is not force-fitted, it is reported as zero)."""

    station: Station
    elev_m: float
    threshold_degc: float
    window_start: datetime
    window_end: datetime
    n_rain_phase_hours: int
    mean_hourly_intensity_mm_per_h: float | None
    """`None` exactly when `n_rain_phase_hours == 0` -- an hour-of-day
    equalised mean over zero hours is undefined, never reported as 0.0."""

    def __post_init__(self) -> None:
        if self.threshold_degc not in RAIN_SCREEN_THRESHOLDS_DEGC:
            raise UnscreenedGradientRefusedError(
                f"{self.station!r}: threshold_degc={self.threshold_degc!r} is "
                f"not one of D4's pinned rain-screen legs "
                f"{RAIN_SCREEN_THRESHOLDS_DEGC} -- an unscreened (all-phase) "
                "population is refused, not computed"
            )
        if not math.isfinite(self.elev_m):
            raise ValueError(f"{self.station!r}: elev_m must be finite")
        if self.n_rain_phase_hours < 0:
            raise ValueError(f"{self.station!r}: n_rain_phase_hours must be >= 0")
        if self.window_start >= self.window_end:
            raise ValueError(
                f"{self.station!r}: window_start {self.window_start} must "
                f"precede window_end {self.window_end}"
            )
        if (
            self.n_rain_phase_hours == 0
            and self.mean_hourly_intensity_mm_per_h is not None
        ):
            raise ValueError(
                f"{self.station!r}: zero rain-phase hours but a non-None mean "
                "-- an hour-of-day equalised mean over zero hours is undefined"
            )
        if self.n_rain_phase_hours > 0 and self.mean_hourly_intensity_mm_per_h is None:
            raise ValueError(
                f"{self.station!r}: {self.n_rain_phase_hours} rain-phase "
                "hours but no mean was computed"
            )


def station_rain_phase_observation(
    transect_station: TransectStation,
    *,
    rr: pl.DataFrame,
    at: pl.DataFrame,
    window_start: datetime,
    window_end: datetime,
    threshold_degc: float,
    jjas_months: tuple[int, ...],
) -> StationRainPhaseObservation:
    """`rr`/`at` are the station's OWN already-retained populations
    (`pyramid_loader.load_pyramid_lvl1_csv`/`load_pyramid_lvl1_at_csv`'s
    `.retained`, columns `(station, timestamp, value_mm)`/`(station,
    timestamp, value_degc)`) -- both on the SAME NPT clock (D9: no
    cross-source pairing needed here). Joined on exact timestamp BEFORE any
    filtering or averaging (this track's established discipline: an hour
    survives only when BOTH RR and AT retained it)."""
    joined = rr.select("timestamp", pl.col("value_mm").alias("rr_mm")).join(
        at.select("timestamp", pl.col("value_degc").alias("at_degc")),
        on="timestamp",
        how="inner",
    )
    rain_phase = joined.filter(
        (pl.col("timestamp") >= window_start)
        & (pl.col("timestamp") <= window_end)
        & pl.col("timestamp").dt.month().is_in(jjas_months)
        & (pl.col("at_degc") >= threshold_degc)
    )
    n = rain_phase.height
    mean = hour_of_day_equalised_mean(rain_phase, value_col="rr_mm") if n > 0 else None
    return StationRainPhaseObservation(
        station=transect_station.pyramid_station,
        elev_m=transect_station.elevation_m,
        threshold_degc=threshold_degc,
        window_start=window_start,
        window_end=window_end,
        n_rain_phase_hours=n,
        mean_hourly_intensity_mm_per_h=mean,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class GradientFitWindow:
    """P1 -- ONE common calendar window across the fit's stations, named
    (`start`/`end`) with every excluded station and its reason carried
    alongside."""

    start: datetime
    end: datetime
    fit_stations: tuple[Station, ...]
    excluded_stations: tuple[Station, ...]
    exclusion_reasons: Mapping[Station, str]

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"window start {self.start} must precede end {self.end}")
        if not self.fit_stations:
            raise EmptyGroupError("GradientFitWindow: zero fit stations")
        if len(set(self.fit_stations)) != len(self.fit_stations):
            raise DuplicateStationError(
                f"fit_stations carries a repeated station: {self.fit_stations}"
            )
        overlap = set(self.fit_stations) & set(self.excluded_stations)
        if overlap:
            raise ValueError(
                f"station(s) {sorted(str(s) for s in overlap)} are both "
                "fit and excluded"
            )
        missing_reasons = set(self.excluded_stations) - set(self.exclusion_reasons)
        if missing_reasons:
            raise ValueError(
                f"excluded station(s) {sorted(str(s) for s in missing_reasons)} "
                "have no exclusion_reasons entry"
            )


def compute_gradient_fit_window(
    rr_extent_by_station: Mapping[Station, tuple[datetime, datetime]],
    *,
    excluded: Mapping[Station, str],
) -> GradientFitWindow:
    """`rr_extent_by_station` is every candidate station's OWN
    `(first_retained_rr_timestamp, last_retained_rr_timestamp)` --
    `excluded` (station -> reason) is applied BEFORE the window is computed
    (P1: excluding AWS0 after computing the window would let its absence
    silently shrink an already-named window). The window is the
    intersection `[max(starts), min(ends)]` of the REMAINING stations' own
    extents -- MEASURED, never assumed to be any particular calendar
    span."""
    fit_stations = tuple(s for s in rr_extent_by_station if s not in excluded)
    if not fit_stations:
        raise EmptyGroupError(
            "compute_gradient_fit_window: every candidate station was excluded"
        )
    starts = [rr_extent_by_station[s][0] for s in fit_stations]
    ends = [rr_extent_by_station[s][1] for s in fit_stations]
    start, end = max(starts), min(ends)
    if start >= end:
        raise ValueError(
            f"the {len(fit_stations)} remaining station(s)' RR records do "
            f"not overlap at all: computed start {start} >= end {end}"
        )
    return GradientFitWindow(
        start=start,
        end=end,
        fit_stations=tuple(sorted(fit_stations)),
        excluded_stations=tuple(sorted(excluded)),
        exclusion_reasons=dict(excluded),
    )


_T_CRITICAL_95: Final[dict[int, float]] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
}
"""Standard two-tailed 95% Student's-t critical values by degrees of
freedom -- a fixed reference table (no scipy/statsmodels dependency in this
package), not a re-derivation of anything M-A6/M-A7 computed. The six-
station transect can never legitimately produce df > 4."""


def _t_critical_95(df: int) -> float:
    if df not in _T_CRITICAL_95:
        raise UnsupportedDegreesOfFreedomError(
            f"no pinned 95% t-critical value for df={df} (table covers "
            f"{sorted(_T_CRITICAL_95)})"
        )
    return _T_CRITICAL_95[df]


_OLS_ROUNDING_DECIMALS = 12
"""D8 -- the OLS closed-form (sums over a FIXED, sorted station order) is
already deterministic run to run; this rounding is defence in depth
following the `ma6_estimands._BUCKET_TOTAL_ROUNDING_DECIMALS` precedent."""


def _ols_slope_intercept_se(
    xs: tuple[float, ...], ys: tuple[float, ...]
) -> tuple[float, float, float]:
    """Ordinary least squares, closed form: `ys = intercept + slope * xs`.
    Returns `(slope, intercept, se_slope)`."""
    n = len(xs)
    if n < 3:
        raise InsufficientObservationsError(f"OLS needs at least 3 points, got {n}")
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0.0:
        raise ZeroVarianceError("OLS is undefined when x carries zero variance")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    residuals = tuple(y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True))
    df = n - 2
    sse = sum(r**2 for r in residuals)
    mse = sse / df
    se_slope = math.sqrt(mse / sxx)
    return slope, intercept, se_slope


@dataclass(frozen=True, kw_only=True, slots=True)
class ApparentRainPhaseGradient:
    """D4/D5's headline artefact. `quantity_label` is a `ClassVar` pinned
    to D4/D5's mandatory name -- it travels with every instance and cannot
    be dropped by a downstream renderer (the same mechanism `ma6_estimands.
    JointWetConditionality`/`RetentionConditionality` use for their own
    pinned labels).

    `observations` carries EVERY fit-window station at this threshold,
    including a station with zero rain-phase hours (P2: the rain line is
    measured, not assumed) -- `fit_observations` is the subset actually
    entering the OLS."""

    quantity_label: ClassVar[str] = APPARENT_RAIN_PHASE_GRADIENT_LABEL

    threshold_degc: float
    observations: tuple[StationRainPhaseObservation, ...]

    def __post_init__(self) -> None:
        if self.threshold_degc not in RAIN_SCREEN_THRESHOLDS_DEGC:
            raise UnscreenedGradientRefusedError(
                f"threshold_degc={self.threshold_degc!r} is not one of D4's "
                f"pinned rain-screen legs {RAIN_SCREEN_THRESHOLDS_DEGC}"
            )
        if not self.observations:
            raise EmptyGroupError("ApparentRainPhaseGradient: zero observations")
        stations = tuple(o.station for o in self.observations)
        if len(set(stations)) != len(stations):
            raise DuplicateStationError(
                f"observations carries a repeated station: {stations}"
            )
        thresholds = {o.threshold_degc for o in self.observations}
        if thresholds != {self.threshold_degc}:
            raise MixedThresholdError(
                f"observations carry threshold(s) {thresholds}, not the "
                f"declared {self.threshold_degc}"
            )

    @property
    def fit_observations(self) -> tuple[StationRainPhaseObservation, ...]:
        return tuple(
            sorted(
                (
                    o
                    for o in self.observations
                    if o.mean_hourly_intensity_mm_per_h is not None
                    and o.mean_hourly_intensity_mm_per_h > 0.0
                ),
                key=lambda o: str(o.station),
            )
        )

    @property
    def n_stations_in_fit(self) -> int:
        return len(self.fit_observations)

    @property
    def _fit_xy_km_log(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        fit = self.fit_observations
        xs = tuple(o.elev_m / 1000.0 for o in fit)
        ys = tuple(math.log(o.mean_hourly_intensity_mm_per_h) for o in fit)  # type: ignore[arg-type]
        return xs, ys

    @property
    def slope_log_per_km(self) -> float:
        xs, ys = self._fit_xy_km_log
        slope, _intercept, _se = _ols_slope_intercept_se(xs, ys)
        return round(slope, _OLS_ROUNDING_DECIMALS)

    @property
    def percent_per_km(self) -> float:
        return round(
            (math.exp(self.slope_log_per_km) - 1.0) * 100.0, _OLS_ROUNDING_DECIMALS
        )

    @property
    def percent_per_km_ci95(self) -> tuple[float, float]:
        xs, ys = self._fit_xy_km_log
        slope, _intercept, se = _ols_slope_intercept_se(xs, ys)
        df = len(xs) - 2
        t_crit = _t_critical_95(df)
        lo_log = slope - t_crit * se
        hi_log = slope + t_crit * se
        pct_lo = (math.exp(lo_log) - 1.0) * 100.0
        pct_hi = (math.exp(hi_log) - 1.0) * 100.0
        return (
            round(pct_lo, _OLS_ROUNDING_DECIMALS),
            round(pct_hi, _OLS_ROUNDING_DECIMALS),
        )


def fit_apparent_rain_phase_gradient(
    transect_stations: tuple[TransectStation, ...],
    *,
    rr_by_station: Mapping[Station, pl.DataFrame],
    at_by_station: Mapping[Station, pl.DataFrame],
    window: GradientFitWindow,
    threshold_degc: float,
    jjas_months: tuple[int, ...],
) -> ApparentRainPhaseGradient:
    """Builds one `ApparentRainPhaseGradient` at `threshold_degc` over every
    station in `window.fit_stations` -- `transect_stations` supplies each
    station's own elevation/metadata, `rr_by_station`/`at_by_station` its
    own retained populations."""
    by_pyramid_station = {s.pyramid_station: s for s in transect_stations}
    observations = tuple(
        station_rain_phase_observation(
            by_pyramid_station[station],
            rr=rr_by_station[station],
            at=at_by_station[station],
            window_start=window.start,
            window_end=window.end,
            threshold_degc=threshold_degc,
            jjas_months=jjas_months,
        )
        for station in window.fit_stations
    )
    return ApparentRainPhaseGradient(
        threshold_degc=threshold_degc, observations=observations
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class SameElevationDiscrepancy:
    """D6 -- AWS0/AWS1's OBSERVED same-elevation discrepancy. ⛔ NEVER a
    resolvability floor: a single realized ratio bounds nothing (this track
    already withdrew the identical inference, Plan 184 D8). `station_a`/
    `station_b` must share one elevation (`NotSameElevationError`
    otherwise)."""

    station_a: Station
    station_b: Station
    elev_m: float
    window_start: datetime
    window_end: datetime
    n_common_retained: int
    """RR-only common retained hours (both stations' own RR populations,
    inner-joined on timestamp) -- ALL MONTHS, not JJAS-restricted (D6's own
    diagnostic is a whole-record comparison, not the JJAS-scoped gradient
    fit)."""
    wet_hour_count_a: int
    """Hours with `rr_mm > 0` (any recorded precipitation, not the 0.2 mm/h
    harmonised floor -- D6's "they agree on WHEN" is about detection, not
    about the wet-hour magnitude threshold the rest of this track uses)."""
    wet_hour_count_b: int
    rain_screen_threshold_degc: float
    n_rain_screened_common: int
    """Common hours where BOTH stations' own `AT` >= `rain_screen_
    threshold_degc` -- jointly screened, mirroring `ma6_estimands.
    joint_wet_scale_subset`'s "both sides" discipline on a different axis."""
    rain_amount_a_mm: float
    rain_amount_b_mm: float

    def __post_init__(self) -> None:
        if self.window_start >= self.window_end:
            raise ValueError("window_start must precede window_end")
        if self.n_common_retained < 0 or self.n_rain_screened_common < 0:
            raise ValueError("retained/screened counts must be >= 0")
        if self.n_rain_screened_common > self.n_common_retained:
            raise ValueError("n_rain_screened_common cannot exceed n_common_retained")

    @property
    def wet_hour_count_ratio(self) -> float:
        """`wet_hour_count_a / wet_hour_count_b`."""
        return self.wet_hour_count_a / self.wet_hour_count_b

    @property
    def rain_amount_ratio(self) -> float:
        """`rain_amount_a_mm / rain_amount_b_mm`."""
        return self.rain_amount_a_mm / self.rain_amount_b_mm


def same_elevation_discrepancy(
    station_a: TransectStation,
    station_b: TransectStation,
    *,
    rr_a: pl.DataFrame,
    rr_b: pl.DataFrame,
    at_a: pl.DataFrame,
    at_b: pl.DataFrame,
    rain_screen_threshold_degc: float = PRIMARY_RAIN_SCREEN_THRESHOLD_DEGC,
) -> SameElevationDiscrepancy:
    """`rr_a`/`rr_b`/`at_a`/`at_b` are each station's own already-retained
    population (`pyramid_loader`'s `.retained`). D6's window is whatever
    the two stations' own RR records overlap -- computed here, never
    assumed."""
    if station_a.elevation_m != station_b.elevation_m:
        raise NotSameElevationError(
            f"{station_a.pyramid_station!r} at {station_a.elevation_m} m "
            f"does not share an elevation with "
            f"{station_b.pyramid_station!r} at {station_b.elevation_m} m"
        )
    rr_common = rr_a.select("timestamp", pl.col("value_mm").alias("rr_a")).join(
        rr_b.select("timestamp", pl.col("value_mm").alias("rr_b")),
        on="timestamp",
        how="inner",
    )
    n_common = rr_common.height
    if n_common == 0:
        raise EmptyGroupError(
            f"{station_a.pyramid_station!r}/{station_b.pyramid_station!r} "
            "share zero common-retained RR hours"
        )
    window_start = rr_common["timestamp"].min()
    window_end = rr_common["timestamp"].max()
    wet_a = int((rr_common["rr_a"] > 0.0).sum())
    wet_b = int((rr_common["rr_b"] > 0.0).sum())

    joined = rr_common.join(
        at_a.select("timestamp", pl.col("value_degc").alias("at_a")),
        on="timestamp",
        how="inner",
    ).join(
        at_b.select("timestamp", pl.col("value_degc").alias("at_b")),
        on="timestamp",
        how="inner",
    )
    rain = joined.filter(
        (pl.col("at_a") >= rain_screen_threshold_degc)
        & (pl.col("at_b") >= rain_screen_threshold_degc)
    )
    return SameElevationDiscrepancy(
        station_a=station_a.pyramid_station,
        station_b=station_b.pyramid_station,
        elev_m=station_a.elevation_m,
        window_start=window_start,  # type: ignore[arg-type]
        window_end=window_end,  # type: ignore[arg-type]
        n_common_retained=n_common,
        wet_hour_count_a=wet_a,
        wet_hour_count_b=wet_b,
        rain_screen_threshold_degc=rain_screen_threshold_degc,
        n_rain_screened_common=rain.height,
        rain_amount_a_mm=float(rain["rr_a"].sum() or 0.0),
        rain_amount_b_mm=float(rain["rr_b"].sum() or 0.0),
    )
