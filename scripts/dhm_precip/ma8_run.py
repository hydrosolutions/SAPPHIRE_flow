#!/usr/bin/env python3
# ruff: noqa: T201
"""Plan 205 (M-A8) task T3 — the report and Exit.

One runner, following the M-A10 shape (`ma6_run.py`/`ma7_run.py`): a
tested, loader-agnostic core (`run_ma8_report`) taking an injected
`Ma8Inputs` bundle, and `main()` wiring the real production paths.

**D9/D2 — this module assembles; it computes nothing M-A6/M-A7 already
did.** The two within-group quantities (module docstring's own pinned
choice) are `MatchedHourMeanDifference.mean_difference_mm_per_h` (M-A6, at
`Scale.JJAS`) and `StationIntensityDistribution.q99_mm_per_h` (M-A7, at
`Season.JJAS`) — the SAME two estimand kinds `ma8_confound.py`'s own module
docstring names as its worked example. `_production_inputs` gets both by
calling M-A6's and M-A7's OWN runner functions (`ma6_run._production_inputs`
+ `run_ma6_comparison`, `ma7_run._production_inputs` + `run_ma7_report`) —
the same cross-module reuse `tests/integration/test_dhm_precip_reproduction.
py` already establishes as this codebase's convention — and reads the two
quantities, with their companions, straight off the results. T1's
classification (`StationClassification`) is built from `Ma7Report`'s own
already-computed `station_resolution_group`/`bands_by_station`/
`station_elev_m` fields, never a second `on_grid` read.

**Q3 — T3 introduces no new randomness.** The only bootstrap-derived
quantity this report renders (M-A7's q99 adequacy designation and CI, D2's
own companion requirement) is `ma7_run`'s own, already seeded by
`ma7_run.REPORT_SEED` when `run_ma7_report` was called — reused verbatim,
never re-seeded here. That seed is printed in section (g).

**Pyramid side (T2's gradient) has no M-A6/M-A7 analogue** — `ma6_lapse_
check.py` reads Pyramid `AT` for the lapse-check transect but never `RR`.
`_production_inputs` therefore wires `pyramid_loader.load_pyramid_lvl1_csv`/
`load_pyramid_lvl1_at_csv` itself (D9: the ONLY functions that read a
Pyramid file), over `ma8_gradient.RR_TRANSECT_STATIONS` — the substrate T2
already established.

⛔ **The runner writes ONLY under `--out`** (Plan 184 T6 P5 / Plan 193 T4
P5, reapplied here) — it never touches `docs/design/dhm-precipitation-
milestones.md` or any other tracked file. Folding results into that
document is a human step.

Refusals render as explicit absences with their reasons (Plan 184 T6 P3 /
Plan 193 T4 P5's convention) — never a blank, a zero, or a dropped row.

Usage:
    uv run python scripts/dhm_precip/ma8_run.py --out <dir>

Environment:
    DHM_PRECIP_XLSX     path to the source workbook (required)
    DHM_PRECIP_COORDS   path to the D12 coordinate table (optional)
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import structlog  # noqa: E402

from scripts.dhm_precip.domain_types import Station  # noqa: E402
from scripts.dhm_precip.era5_errors import Era5AcquisitionError  # noqa: E402
from scripts.dhm_precip.loader import DhmPrecipLoaderError  # noqa: E402
from scripts.dhm_precip.ma6_estimands import ElevationBand  # noqa: E402
from scripts.dhm_precip.ma6_lapse_check import (  # noqa: E402
    DEFAULT_PRECIP_DATA_ROOT,
    DEFAULT_PYRAMID_DIR,
    DEFAULT_T2M_DATA_ROOT,
)
from scripts.dhm_precip.ma6_pairs import Scale  # noqa: E402
from scripts.dhm_precip.ma6_run import (  # noqa: E402
    AbsentResult,
    Ma6Report,
    MagnitudeKind,
    PrecipBundleIdentityMismatchError,
    StationMagnitudeCell,
    run_ma6_comparison,
)
from scripts.dhm_precip.ma6_run import (  # noqa: E402
    _production_inputs as _ma6_production_inputs,  # pyright: ignore[reportPrivateUsage]
)
from scripts.dhm_precip.ma7_run import REPORT_SEED as MA7_REPORT_SEED  # noqa: E402
from scripts.dhm_precip.ma7_run import (  # noqa: E402
    BootstrapRefusal,
    Ma7Report,
    StationSeasonIntensity,
    run_ma7_report,
)
from scripts.dhm_precip.ma7_run import (  # noqa: E402
    _production_inputs as _ma7_production_inputs,  # pyright: ignore[reportPrivateUsage]
)
from scripts.dhm_precip.ma8_confound import (  # noqa: E402
    BandGroupSplitQuantileRatios,
    BetweenGroupContrastStatement,
    GroupBandCount,
    GroupElevationObservation,
    GroupElevationRange,
    StationClassification,
    WithinGroupElevationRelationship,
    band_group_split_quantile_ratios,
    between_group_contrast_statement,
    group_band_cross_tabulation,
    group_elevation_ranges,
    within_group_elevation_relationship,
)
from scripts.dhm_precip.ma8_confound import (  # noqa: E402
    EmptyGroupError as ConfoundEmptyGroupError,
)
from scripts.dhm_precip.ma8_confound import (  # noqa: E402
    InsufficientObservationsError as ConfoundInsufficientObservationsError,
)
from scripts.dhm_precip.ma8_confound import (  # noqa: E402
    ZeroVarianceError as ConfoundZeroVarianceError,
)
from scripts.dhm_precip.ma8_gradient import (  # noqa: E402
    APPARENT_RAIN_PHASE_GRADIENT_LABEL,
    AWS0,
    AWS1,
    RAIN_SCREEN_THRESHOLDS_DEGC,
    RR_TRANSECT_STATIONS,
    ApparentRainPhaseGradient,
    GradientFitWindow,
    NotSameElevationError,
    SameElevationDiscrepancy,
    UnsupportedDegreesOfFreedomError,
    compute_gradient_fit_window,
    fit_apparent_rain_phase_gradient,
    same_elevation_discrepancy,
)
from scripts.dhm_precip.ma8_gradient import (  # noqa: E402
    EmptyGroupError as GradientEmptyGroupError,
)
from scripts.dhm_precip.ma8_gradient import (  # noqa: E402
    InsufficientObservationsError as GradientInsufficientObservationsError,
)
from scripts.dhm_precip.ma8_gradient import (  # noqa: E402
    ZeroVarianceError as GradientZeroVarianceError,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams  # noqa: E402
from scripts.dhm_precip.pyramid_loader import (  # noqa: E402
    PyramidLoaderError,
    load_pyramid_lvl1_at_csv,
    load_pyramid_lvl1_csv,
)
from scripts.dhm_precip.seasons import Season  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import polars as pl

    from scripts.dhm_precip.ma6_lapse_check import TransectStation
    from scripts.dhm_precip.ma7_intensity import QuantileBootstrap
    from scripts.dhm_precip.ma7_transfer import ResolutionGroupLabel

log = structlog.get_logger(__name__)

BIAS_ESTIMAND_LABEL = "M-A6 matched-hour mean difference (mm/h, JJAS)"
"""D2/D9 -- the FIRST of the two assembled quantities, pinned by module
docstring: `MatchedHourMeanDifference.mean_difference_mm_per_h` at
`Scale.JJAS`, the same estimand `ma8_confound.py`'s own docstring names."""

TAIL_ESTIMAND_LABEL = "M-A7 q99 wet-hour intensity (mm/h, JJAS)"
"""The SECOND assembled quantity: `StationIntensityDistribution.
q99_mm_per_h` at `Season.JJAS` -- `ma8_confound.py`'s other worked example."""

_HEADLINE_SEASON = Season.JJAS
_MAGNITUDE_SCALE = Scale.JJAS
"""D9's own headline season/scale -- the season M-A7's transferability
comparison already treats as canonical (D8) and the ONLY scale
`MatchedHourMeanDifference` is scoped to besides DJF."""

AWS0_EXCLUSION_REASON = (
    "P1 -- AWS0's own RR record ends 2004-12-09, excluded from the "
    "cross-station gradient fit by name before the fit window is computed"
)


class Ma8InputsConsistencyError(ValueError):
    """`Ma8Inputs` was given cross-referencing collections that do not
    agree on which stations exist -- checked eagerly rather than surfacing
    as a `KeyError` deep inside `run_ma8_report`."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Refusal:
    """A computation that legitimately could not run (zero/insufficient
    observations, zero variance, an unsupported degrees-of-freedom).
    Rendered as an explicit absence with `reason`, never a blank, a zero,
    or a dropped row (Plan 184 T6 P3 / Plan 193 T4 P5's convention)."""

    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma8Inputs:
    """T3's DI seam (CLAUDE.md dependency injection) -- `run_ma8_report`'s
    tested core takes this bundle and never touches disk itself, mirroring
    `ma7_run.Ma7Inputs`/`ma6_run.Ma6Inputs`. Every field is a RAW ingredient
    (an M-A6/M-A7 result object, or a Pyramid retained frame) -- the core
    assembles, it does not recompute (D2/D9)."""

    classifications: tuple[StationClassification, ...]
    station_magnitude: Mapping[Station, StationMagnitudeCell | AbsentResult]
    station_intensity: Mapping[Station, StationSeasonIntensity]
    ma7_seed: int
    rr_transect_stations: tuple[TransectStation, ...]
    rr_by_station: Mapping[Station, pl.DataFrame]
    at_by_station: Mapping[Station, pl.DataFrame]
    provenance_lines: tuple[str, ...]
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        classified = frozenset(c.station for c in self.classifications)
        if not classified:
            raise Ma8InputsConsistencyError("classifications is empty")
        missing_magnitude = classified - frozenset(self.station_magnitude)
        if missing_magnitude:
            raise Ma8InputsConsistencyError(
                "classified station(s) missing from station_magnitude: "
                f"{sorted(str(s) for s in missing_magnitude)}"
            )
        missing_intensity = classified - frozenset(self.station_intensity)
        if missing_intensity:
            raise Ma8InputsConsistencyError(
                "classified station(s) missing from station_intensity: "
                f"{sorted(str(s) for s in missing_intensity)}"
            )
        transect_stations = frozenset(
            s.pyramid_station for s in self.rr_transect_stations
        )
        missing_rr = transect_stations - frozenset(self.rr_by_station)
        if missing_rr:
            raise Ma8InputsConsistencyError(
                f"rr_transect_stations missing from rr_by_station: "
                f"{sorted(str(s) for s in missing_rr)}"
            )
        missing_at = transect_stations - frozenset(self.at_by_station)
        if missing_at:
            raise Ma8InputsConsistencyError(
                f"rr_transect_stations missing from at_by_station: "
                f"{sorted(str(s) for s in missing_at)}"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma8Headlines:
    """Q2 -- the locked headline set, SMALL and NAMED. Every OTHER number
    in the report is free to change without breaking a reproduction test;
    only these fields are."""

    within_group_b_pearson_r_bias: float | None
    within_group_b_pearson_r_tail: float | None
    gradient_percent_per_km_by_threshold: Mapping[float, float]
    gradient_ci95_by_threshold: Mapping[float, tuple[float, float]]
    fit_window_start: datetime
    fit_window_end: datetime
    aws0_aws1_wet_hour_count_ratio: float | None
    aws0_aws1_rain_amount_ratio: float | None


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma8Report:
    generated_at: datetime
    params: DhmPrecipParams
    provenance_lines: tuple[str, ...]
    ma7_seed: int

    classifications: tuple[StationClassification, ...]
    group_ranges: tuple[GroupElevationRange, ...]
    between_group_statement: BetweenGroupContrastStatement
    cross_tabulation: tuple[GroupBandCount, ...]

    group_b_bias_relationship: WithinGroupElevationRelationship | Refusal
    group_b_tail_relationship: WithinGroupElevationRelationship | Refusal
    group_a_members: tuple[StationClassification, ...]

    band_split: BandGroupSplitQuantileRatios | Refusal

    gradient_window: GradientFitWindow
    gradients_by_threshold: Mapping[float, ApparentRainPhaseGradient | Refusal]
    same_elevation: SameElevationDiscrepancy | Refusal

    station_magnitude: Mapping[Station, StationMagnitudeCell | AbsentResult]
    station_intensity: Mapping[Station, StationSeasonIntensity]

    headlines: Ma8Headlines


# --------------------------------------------------------------------------
# the tested, loader-agnostic core
# --------------------------------------------------------------------------


def _bias_observations(
    members: tuple[StationClassification, ...],
    *,
    station_magnitude: Mapping[Station, StationMagnitudeCell | AbsentResult],
) -> tuple[GroupElevationObservation, ...]:
    observations: list[GroupElevationObservation] = []
    for member in members:
        cell = station_magnitude.get(member.station)
        if not isinstance(cell, StationMagnitudeCell):
            continue
        observations.append(
            GroupElevationObservation(
                station=member.station, elev_m=member.elev_m, value=cell.value, n=cell.n
            )
        )
    return tuple(observations)


def _tail_observations(
    members: tuple[StationClassification, ...],
    *,
    station_intensity: Mapping[Station, StationSeasonIntensity],
) -> tuple[GroupElevationObservation, ...]:
    observations: list[GroupElevationObservation] = []
    for member in members:
        entry = station_intensity.get(member.station)
        if entry is None:
            continue
        q99 = entry.distribution.q99_mm_per_h
        if q99 is None:
            continue
        observations.append(
            GroupElevationObservation(
                station=member.station,
                elev_m=member.elev_m,
                value=q99,
                n=entry.distribution.n_wet_retained,
            )
        )
    return tuple(observations)


def _within_group_b_relationship(
    quantity_label: str,
    members: tuple[StationClassification, ...],
    observations: tuple[GroupElevationObservation, ...],
    *,
    station_group: Mapping[Station, ResolutionGroupLabel],
) -> WithinGroupElevationRelationship | Refusal:
    if not observations:
        return Refusal(
            reason=f"{quantity_label}: zero stations with a computable value"
        )
    relationship = within_group_elevation_relationship(
        quantity_label, "B", observations, station_group=station_group
    )
    try:
        _ = relationship.pearson_r
    except (ConfoundInsufficientObservationsError, ConfoundZeroVarianceError) as exc:
        return Refusal(reason=str(exc))
    return relationship


def _band_split_or_refusal(
    band: ElevationBand,
    members: tuple[StationClassification, ...],
    *,
    station_intensity: Mapping[Station, StationSeasonIntensity],
    station_group: Mapping[Station, ResolutionGroupLabel],
) -> BandGroupSplitQuantileRatios | Refusal:
    band_members = [m for m in members if m.band is band]
    distributions = [
        station_intensity[m.station].distribution
        for m in band_members
        if m.station in station_intensity
    ]
    if not distributions:
        return Refusal(
            reason=f"{band}: zero member stations with an intensity distribution"
        )
    station_elev_m = {m.station: m.elev_m for m in members}
    try:
        return band_group_split_quantile_ratios(
            band,
            tuple(distributions),
            station_elev_m=station_elev_m,
            station_group=station_group,
        )
    except ConfoundEmptyGroupError as exc:
        return Refusal(reason=str(exc))


def _gradient_or_refusal(
    threshold_degc: float,
    *,
    rr_transect_stations: tuple[TransectStation, ...],
    rr_by_station: Mapping[Station, pl.DataFrame],
    at_by_station: Mapping[Station, pl.DataFrame],
    window: GradientFitWindow,
    params: DhmPrecipParams,
) -> ApparentRainPhaseGradient | Refusal:
    try:
        gradient = fit_apparent_rain_phase_gradient(
            rr_transect_stations,
            rr_by_station=rr_by_station,
            at_by_station=at_by_station,
            window=window,
            threshold_degc=threshold_degc,
            jjas_months=params.jjas_months,
        )
        # Forced eagerly (T6 P4 precedent): a refusal is caught HERE, at
        # build time, so rendering later can never crash on an unforced
        # property.
        _ = gradient.percent_per_km
        _ = gradient.percent_per_km_ci95
    except (
        GradientEmptyGroupError,
        GradientInsufficientObservationsError,
        GradientZeroVarianceError,
        UnsupportedDegreesOfFreedomError,
    ) as exc:
        return Refusal(reason=str(exc))
    return gradient


def _same_elevation_or_refusal(
    *,
    rr_by_station: Mapping[Station, pl.DataFrame],
    at_by_station: Mapping[Station, pl.DataFrame],
    params: DhmPrecipParams,
) -> SameElevationDiscrepancy | Refusal:
    try:
        same = same_elevation_discrepancy(
            AWS0,
            AWS1,
            rr_a=rr_by_station[AWS0.pyramid_station],
            rr_b=rr_by_station[AWS1.pyramid_station],
            at_a=at_by_station[AWS0.pyramid_station],
            at_b=at_by_station[AWS1.pyramid_station],
        )
        # Forced eagerly (T6 P4 precedent, matching `_gradient_or_refusal`):
        # `wet_hour_count_ratio`/`rain_amount_ratio` divide by AWS1's own
        # count/amount, which is legitimately zero when the rain-screened
        # common population is empty (e.g. an all-dry AT screen) -- caught
        # HERE, at build time, so rendering later can never crash on an
        # unforced property.
        _ = same.wet_hour_count_ratio
        _ = same.rain_amount_ratio
    except (GradientEmptyGroupError, NotSameElevationError, ZeroDivisionError) as exc:
        return Refusal(reason=str(exc))
    return same


def _headlines(
    *,
    group_b_bias_relationship: WithinGroupElevationRelationship | Refusal,
    group_b_tail_relationship: WithinGroupElevationRelationship | Refusal,
    gradient_window: GradientFitWindow,
    gradients_by_threshold: Mapping[float, ApparentRainPhaseGradient | Refusal],
    same_elevation: SameElevationDiscrepancy | Refusal,
) -> Ma8Headlines:
    bias_r = (
        group_b_bias_relationship.pearson_r
        if isinstance(group_b_bias_relationship, WithinGroupElevationRelationship)
        else None
    )
    tail_r = (
        group_b_tail_relationship.pearson_r
        if isinstance(group_b_tail_relationship, WithinGroupElevationRelationship)
        else None
    )
    pct_by_threshold: dict[float, float] = {}
    ci_by_threshold: dict[float, tuple[float, float]] = {}
    for threshold, outcome in gradients_by_threshold.items():
        if isinstance(outcome, ApparentRainPhaseGradient):
            pct_by_threshold[threshold] = outcome.percent_per_km
            ci_by_threshold[threshold] = outcome.percent_per_km_ci95
    wet_ratio = (
        same_elevation.wet_hour_count_ratio
        if isinstance(same_elevation, SameElevationDiscrepancy)
        else None
    )
    rain_ratio = (
        same_elevation.rain_amount_ratio
        if isinstance(same_elevation, SameElevationDiscrepancy)
        else None
    )
    return Ma8Headlines(
        within_group_b_pearson_r_bias=bias_r,
        within_group_b_pearson_r_tail=tail_r,
        gradient_percent_per_km_by_threshold=pct_by_threshold,
        gradient_ci95_by_threshold=ci_by_threshold,
        fit_window_start=gradient_window.start,
        fit_window_end=gradient_window.end,
        aws0_aws1_wet_hour_count_ratio=wet_ratio,
        aws0_aws1_rain_amount_ratio=rain_ratio,
    )


def run_ma8_report(
    *,
    inputs: Ma8Inputs,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Ma8Report:
    """The M-A8 Exit deliverable, composed. No RNG parameter (Q3): every
    bootstrap-derived number this report renders is already computed inside
    `inputs.station_intensity`, seeded by `ma7_run.REPORT_SEED` before this
    function is ever called."""
    params = inputs.params
    classifications = tuple(
        sorted(inputs.classifications, key=lambda c: str(c.station))
    )
    station_group: dict[Station, ResolutionGroupLabel] = {
        c.station: c.group for c in classifications
    }

    group_ranges = group_elevation_ranges(classifications)
    between_group_statement = between_group_contrast_statement(group_ranges)
    cross_tabulation = group_band_cross_tabulation(classifications)

    group_b_members = tuple(c for c in classifications if c.group == "B")
    group_a_members = tuple(c for c in classifications if c.group == "A")

    bias_observations = _bias_observations(
        group_b_members, station_magnitude=inputs.station_magnitude
    )
    tail_observations = _tail_observations(
        group_b_members, station_intensity=inputs.station_intensity
    )
    group_b_bias_relationship = _within_group_b_relationship(
        BIAS_ESTIMAND_LABEL,
        group_b_members,
        bias_observations,
        station_group=station_group,
    )
    group_b_tail_relationship = _within_group_b_relationship(
        TAIL_ESTIMAND_LABEL,
        group_b_members,
        tail_observations,
        station_group=station_group,
    )

    band_split = _band_split_or_refusal(
        ElevationBand.B2000_3000M,
        classifications,
        station_intensity=inputs.station_intensity,
        station_group=station_group,
    )

    rr_extent_by_station: dict[Station, tuple[datetime, datetime]] = {
        s.pyramid_station: (
            inputs.rr_by_station[s.pyramid_station]["timestamp"].min(),  # type: ignore[arg-type]
            inputs.rr_by_station[s.pyramid_station]["timestamp"].max(),  # type: ignore[arg-type]
        )
        for s in inputs.rr_transect_stations
    }
    gradient_window = compute_gradient_fit_window(
        rr_extent_by_station, excluded={AWS0.pyramid_station: AWS0_EXCLUSION_REASON}
    )
    gradients_by_threshold = {
        threshold: _gradient_or_refusal(
            threshold,
            rr_transect_stations=inputs.rr_transect_stations,
            rr_by_station=inputs.rr_by_station,
            at_by_station=inputs.at_by_station,
            window=gradient_window,
            params=params,
        )
        for threshold in RAIN_SCREEN_THRESHOLDS_DEGC
    }
    same_elevation = _same_elevation_or_refusal(
        rr_by_station=inputs.rr_by_station,
        at_by_station=inputs.at_by_station,
        params=params,
    )

    headlines = _headlines(
        group_b_bias_relationship=group_b_bias_relationship,
        group_b_tail_relationship=group_b_tail_relationship,
        gradient_window=gradient_window,
        gradients_by_threshold=gradients_by_threshold,
        same_elevation=same_elevation,
    )
    return Ma8Report(
        generated_at=clock(),
        params=params,
        provenance_lines=inputs.provenance_lines,
        ma7_seed=inputs.ma7_seed,
        classifications=classifications,
        group_ranges=group_ranges,
        between_group_statement=between_group_statement,
        cross_tabulation=cross_tabulation,
        group_b_bias_relationship=group_b_bias_relationship,
        group_b_tail_relationship=group_b_tail_relationship,
        group_a_members=group_a_members,
        band_split=band_split,
        gradient_window=gradient_window,
        gradients_by_threshold=gradients_by_threshold,
        same_elevation=same_elevation,
        station_magnitude=inputs.station_magnitude,
        station_intensity=inputs.station_intensity,
        headlines=headlines,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _md_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---:"] * len(headers)) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    lines.append("")
    return lines


def _fmt_mm(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "n/a"


def _fmt_bias_cell(
    cell: StationMagnitudeCell | AbsentResult | None,
) -> tuple[str, str, str]:
    """`(value, n, mass-fraction companion)` -- D2's companion travels with
    every rendered bias value."""
    if cell is None:
        return "n/a", "n/a", "refused — not classified"
    if isinstance(cell, AbsentResult):
        return "n/a", "n/a", f"refused — {cell.reason}"
    fraction = cell.mass_fraction
    return (
        _fmt_mm(cell.value),
        str(cell.n),
        f"{fraction.sub_freezing_mass_fraction:.4f} sub-freezing "
        f"(unclassifiable {fraction.unclassifiable_mass_share:.4f})",
    )


def _fmt_quantile_outcome(
    outcome: QuantileBootstrap | BootstrapRefusal | None,
) -> tuple[str, str, str]:
    """`(value, n season-years/adequacy, CI)` -- D2's companion (M-A7's
    adequacy designation) travels with every rendered q99 value."""
    if outcome is None:
        return "n/a", "n/a", "refused — not classified"
    if isinstance(outcome, BootstrapRefusal):
        return "n/a", "n/a", f"refused — {outcome.reason}"
    adequate = "yes" if outcome.adequate_sample else "NO (< 5 season-years)"
    ci = f"[{outcome.ci_low_mm_per_h:.4f}, {outcome.ci_high_mm_per_h:.4f}] 95% CI"
    return (
        _fmt_mm(outcome.point_estimate_mm_per_h),
        f"n={outcome.n_season_years} ({adequate})",
        ci,
    )


def _relationship_lines(
    label: str, relationship: WithinGroupElevationRelationship | Refusal
) -> list[str]:
    lines = [f"### {label}", ""]
    if isinstance(relationship, WithinGroupElevationRelationship):
        lines.append(
            f"- Pearson r = {relationship.pearson_r:.4f} over "
            f"n = {relationship.n_stations} station(s)"
        )
    else:
        lines.append(f"- refused — {relationship.reason}")
    lines.append("")
    return lines


def _group_b_bias_table(
    members: tuple[StationClassification, ...],
    station_magnitude: Mapping[Station, StationMagnitudeCell | AbsentResult],
) -> list[str]:
    rows = [
        (
            str(member.station),
            f"{member.elev_m:.0f}",
            *_fmt_bias_cell(station_magnitude.get(member.station)),
        )
        for member in members
    ]
    return _md_table(
        ("station", "elevation (m)", "value (mm/h)", "n", "companion"), rows
    )


def _group_b_tail_table(
    members: tuple[StationClassification, ...],
    station_intensity: Mapping[Station, StationSeasonIntensity],
) -> list[str]:
    rows: list[tuple[str, ...]] = []
    for member in members:
        entry = station_intensity.get(member.station)
        q99, n, companion = _fmt_quantile_outcome(
            entry.q99_bootstrap if entry is not None else None
        )
        rows.append((str(member.station), f"{member.elev_m:.0f}", q99, n, companion))
    return _md_table(
        ("station", "elevation (m)", "value (mm/h)", "n", "companion"), rows
    )


def _group_b_relationship_section(report: Ma8Report) -> list[str]:
    lines = [
        "## (a) Within-Group-B elevation relationship (D1 -- descriptive, "
        "NOT an elevation effect)",
        "",
    ]
    group_b_members = tuple(c for c in report.classifications if c.group == "B")
    lines += _relationship_lines(BIAS_ESTIMAND_LABEL, report.group_b_bias_relationship)
    lines += _group_b_bias_table(group_b_members, report.station_magnitude)
    lines += _relationship_lines(TAIL_ESTIMAND_LABEL, report.group_b_tail_relationship)
    lines += _group_b_tail_table(group_b_members, report.station_intensity)
    return lines


def _group_a_section(report: Ma8Report) -> list[str]:
    lines = [
        "## (b) Group A, per station (reported beside Group B, no fitted relationship)",
        "",
    ]
    rows: list[tuple[str, ...]] = []
    for member in report.group_a_members:
        bias_value, bias_n, bias_companion = _fmt_bias_cell(
            report.station_magnitude.get(member.station)
        )
        entry = report.station_intensity.get(member.station)
        tail_value, tail_n, tail_companion = _fmt_quantile_outcome(
            entry.q99_bootstrap if entry is not None else None
        )
        rows.append(
            (
                str(member.station),
                f"{member.elev_m:.0f}",
                str(member.band),
                bias_value,
                bias_n,
                bias_companion,
                tail_value,
                tail_n,
                tail_companion,
            )
        )
    lines += _md_table(
        (
            "station",
            "elevation (m)",
            "band",
            "bias (mm/h)",
            "bias n",
            "bias companion",
            "q99 (mm/h)",
            "q99 n",
            "q99 companion",
        ),
        rows,
    )
    return lines


def _cross_tabulation_section(report: Ma8Report) -> list[str]:
    lines = [
        "## (c) Group x band cross-tabulation, and the between-group "
        "unidentified statement",
        "",
    ]
    rows = [
        (str(cell.group), str(cell.band), str(cell.station_count))
        for cell in report.cross_tabulation
    ]
    lines += _md_table(("group", "band", "station count"), rows)
    range_rows = [
        (
            str(r.group),
            f"{r.min_elev_m:.0f}",
            f"{r.max_elev_m:.0f}",
            f"{r.relief_m:.0f}",
        )
        for r in report.group_ranges
    ]
    lines += _md_table(
        ("group", "min elevation (m)", "max elevation (m)", "relief (m)"), range_rows
    )
    lines += [f"**{report.between_group_statement.declined_attribution}**", ""]
    return lines


def _band_split_section(report: Ma8Report) -> list[str]:
    lines = [
        "## (d) The 2,000-3,000 m band's group-split q99/q50 ratios (D3 -- "
        "the confound's ONLY evidence)",
        "",
    ]
    if isinstance(report.band_split, Refusal):
        lines += [f"refused — {report.band_split.reason}", ""]
        return lines
    rows = [
        (
            str(m.station),
            str(m.group),
            _fmt_mm(m.q50_mm_per_h),
            _fmt_mm(m.q99_mm_per_h),
            f"{m.ratio:.2f}" if m.ratio is not None else "n/a",
        )
        for m in report.band_split.members
    ]
    lines += _md_table(
        ("station", "group", "q50 (mm/h)", "q99 (mm/h)", "q99/q50"), rows
    )
    return lines


def _gradient_section(report: Ma8Report) -> list[str]:
    window = report.gradient_window
    lines = [
        f"## (e) The {APPARENT_RAIN_PHASE_GRADIENT_LABEL} (D4/Q4)",
        "",
        f"Fit window (P1, measured): `{window.start.isoformat()}` -> "
        f"`{window.end.isoformat()}`. Fit stations: "
        f"{', '.join(str(s) for s in window.fit_stations)}. Excluded: "
        + "; ".join(
            f"{s} ({window.exclusion_reasons[s]})" for s in window.excluded_stations
        )
        + ". AWS4 is the station whose RR record ends the window "
        "(2018-05-09); keeping it preserves the full 2,940 m transect at "
        "the cost of 2018-2023 for every other station (P1's trade-off).",
        "",
    ]
    for threshold in RAIN_SCREEN_THRESHOLDS_DEGC:
        outcome = report.gradients_by_threshold[threshold]
        lines += [f"### rain screen: AT >= {threshold} degC", ""]
        if isinstance(outcome, Refusal):
            lines += [f"refused — {outcome.reason}", ""]
            continue
        lo, hi = outcome.percent_per_km_ci95
        lines.append(
            f"- {outcome.percent_per_km:.4f} % per km, 95% CI "
            f"[{lo:.4f}, {hi:.4f}], n_stations_in_fit={outcome.n_stations_in_fit} "
            "-- an UPPER BOUND IN MAGNITUDE on any true decline (D4), never "
            "an estimate of precipitation decline"
        )
        lines.append("")
        rows = [
            (
                str(o.station),
                f"{o.elev_m:.0f}",
                str(o.n_rain_phase_hours),
                _fmt_mm(o.mean_hourly_intensity_mm_per_h),
            )
            for o in outcome.observations
        ]
        lines += _md_table(
            (
                "station",
                "elevation (m)",
                "n rain-phase hours",
                "mean mm/h (hour-of-day equalised)",
            ),
            rows,
        )
    return lines


def _same_elevation_section(report: Ma8Report) -> list[str]:
    lines = [
        "## (f) AWS0/AWS1 observed same-elevation discrepancy (D6 -- NOT a "
        "resolvability floor)",
        "",
    ]
    same = report.same_elevation
    if isinstance(same, Refusal):
        lines += [f"refused — {same.reason}", ""]
        return lines
    lines += [
        f"- elevation: {same.elev_m:.0f} m ; window "
        f"`{same.window_start.isoformat()}` -> `{same.window_end.isoformat()}`",
        f"- n common retained (all months): {same.n_common_retained}",
        f"- wet-hour counts: {same.station_a} = {same.wet_hour_count_a}, "
        f"{same.station_b} = {same.wet_hour_count_b}, ratio = "
        f"{same.wet_hour_count_ratio:.4f}",
        f"- rain-screened (AT >= {same.rain_screen_threshold_degc} degC) "
        f"amounts: {same.station_a} = {same.rain_amount_a_mm:.3f} mm, "
        f"{same.station_b} = {same.rain_amount_b_mm:.3f} mm, ratio = "
        f"{same.rain_amount_ratio:.4f}",
        "",
        "This is a single realized ratio over one window (2000-2004ish, "
        "older than the rest of the record) and bounds nothing on its own.",
        "",
    ]
    return lines


def _inputs_and_seed_section(report: Ma8Report) -> list[str]:
    lines = [
        "## (g) Consumed inputs, fit window, and the seed",
        "",
        "- T3 introduces no new randomness (Q3). The only bootstrap-derived "
        "quantity rendered above (M-A7's q99 adequacy designation and CI) "
        f"is `ma7_run`'s own, seeded by `ma7_run.REPORT_SEED = {report.ma7_seed}` "
        "when M-A7's report was built -- reused verbatim, never re-seeded here.",
        f"- gradient fit window: `{report.gradient_window.start.isoformat()}` -> "
        f"`{report.gradient_window.end.isoformat()}`",
        f"- station count classified: {len(report.classifications)}",
        *(f"- {line}" for line in report.provenance_lines),
        "",
    ]
    return lines


def _write_report(path: Path, report: Ma8Report) -> None:
    lines = [
        "# DHM precipitation — M-A8 elevation and regime structure",
        "",
        f"- generated: `{report.generated_at.isoformat()}`",
        "",
    ]
    lines += _group_b_relationship_section(report)
    lines += _group_a_section(report)
    lines += _cross_tabulation_section(report)
    lines += _band_split_section(report)
    lines += _gradient_section(report)
    lines += _same_elevation_section(report)
    lines += _inputs_and_seed_section(report)
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# production wiring
# --------------------------------------------------------------------------


def _classifications_from_ma7_report(
    ma7_report: Ma7Report,
) -> tuple[StationClassification, ...]:
    return tuple(
        StationClassification(
            station=station,
            group=ma7_report.station_resolution_group[station],
            band=ma7_report.bands_by_station[station],
            elev_m=ma7_report.station_elev_m[station],
        )
        for station in sorted(ma7_report.stations)
    )


def _station_magnitude_from_ma6_report(
    ma6_report: Ma6Report,
) -> dict[Station, StationMagnitudeCell | AbsentResult]:
    result: dict[Station, StationMagnitudeCell | AbsentResult] = {}
    for cell in ma6_report.station_magnitudes:
        if cell.kind != MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE:
            continue
        if cell.scale != _MAGNITUDE_SCALE:
            continue
        station = (
            cell.station
            if isinstance(cell, StationMagnitudeCell)
            else Station(cell.label)
        )
        result[station] = cell
    return result


def _pyramid_rr_at(
    pyramid_dir: Path, params: DhmPrecipParams
) -> tuple[dict[Station, pl.DataFrame], dict[Station, pl.DataFrame]]:
    rr_by_station: dict[Station, pl.DataFrame] = {}
    at_by_station: dict[Station, pl.DataFrame] = {}
    for transect_station in RR_TRANSECT_STATIONS:
        rr_result = load_pyramid_lvl1_csv(
            pyramid_dir / transect_station.csv_filename,
            station=transect_station.pyramid_station,
            params=params,
        )
        at_result = load_pyramid_lvl1_at_csv(
            pyramid_dir / transect_station.csv_filename,
            station=transect_station.pyramid_station,
        )
        rr_by_station[transect_station.pyramid_station] = rr_result.retained
        at_by_station[transect_station.pyramid_station] = at_result.retained
    return rr_by_station, at_by_station


def _production_inputs(
    *,
    precip_data_root: Path = DEFAULT_PRECIP_DATA_ROOT,
    t2m_data_root: Path = DEFAULT_T2M_DATA_ROOT,
    pyramid_dir: Path = DEFAULT_PYRAMID_DIR,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> Ma8Inputs:
    """Wires M-A6's and M-A7's own runner functions, plus T2's own Pyramid
    RR/AT reads, into one `Ma8Inputs` bundle (D9: consumes existing
    substrates, builds no third one)."""
    ma6_inputs = _ma6_production_inputs(
        precip_data_root=precip_data_root,
        t2m_data_root=t2m_data_root,
        pyramid_dir=pyramid_dir,
        params=params,
    )
    ma6_report = run_ma6_comparison(ma6_inputs)

    ma7_inputs = _ma7_production_inputs(params=params)
    ma7_report = run_ma7_report(
        inputs=ma7_inputs, rng=random.Random(MA7_REPORT_SEED), seed=MA7_REPORT_SEED
    )

    classifications = _classifications_from_ma7_report(ma7_report)
    station_magnitude = _station_magnitude_from_ma6_report(ma6_report)
    station_intensity = {
        station: ma7_report.station_intensities[station, _HEADLINE_SEASON]
        for station in ma7_report.stations
        if (station, _HEADLINE_SEASON) in ma7_report.station_intensities
    }

    rr_by_station, at_by_station = _pyramid_rr_at(pyramid_dir, params)

    provenance_lines = (
        f"M-A6 precip extraction identity: `{ma6_report.precip_extraction_identity}`",
        f"M-A6 t2m extraction identity: `{ma6_report.t2m_extraction_identity}`",
        *ma7_report.provenance_lines,
        f"Pyramid directory: `{pyramid_dir}`",
    )
    return Ma8Inputs(
        classifications=classifications,
        station_magnitude=station_magnitude,
        station_intensity=station_intensity,
        ma7_seed=MA7_REPORT_SEED,
        rr_transect_stations=RR_TRANSECT_STATIONS,
        rr_by_station=rr_by_station,
        at_by_station=at_by_station,
        provenance_lines=provenance_lines,
        params=params,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory for the report"
    )
    args = parser.parse_args(argv)

    try:
        inputs = _production_inputs()
        report = run_ma8_report(inputs=inputs)
    except (
        DhmPrecipLoaderError,
        Era5AcquisitionError,
        PyramidLoaderError,
        PrecipBundleIdentityMismatchError,
        Ma8InputsConsistencyError,
        OSError,
    ) as exc:
        log.error("ma8_run.cli.failed", error=str(exc), error_type=type(exc).__name__)
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / "ma8_report.md"
    _write_report(out_path, report)
    log.info("ma8_run.cli.complete", out=str(out_path))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
