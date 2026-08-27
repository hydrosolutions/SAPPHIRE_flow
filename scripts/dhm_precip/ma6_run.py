#!/usr/bin/env python3
# ruff: noqa: T201
"""Plan 184 (M-A6) task T6 — the report and Exit compliance.

One runner, following the M-A10 precedent (`coloc_run.py`): a tested,
loader-agnostic core (`run_ma6_comparison`) taking an injected `Ma6Inputs`
bundle (CLAUDE.md dependency injection — no I/O in business logic), and
`main()` wiring the real T1-T5 production paths (`ma6_pairs`,
`ma6_estimands`, `ma6_representativeness`, `ma6_mass_fraction`,
`ma6_lapse_check`) into that same bundle. This module computes NOTHING new
— every number is T1-T5's own, read here and rendered.

**Exit 2 is enforced STRUCTURALLY, not by convention (T6 P4).** A magnitude
value is unrenderable without its retained-hour `n` and its sub-freezing
mass fraction: `StationMagnitudeCell`/`BandMagnitudeCell` store the SOURCE
estimand and mass-fraction objects (never independently-suppliable numbers)
and `__post_init__` verifies, by OBJECT IDENTITY, that the mass fraction was
built from the exact same subset the magnitude was — the same discipline
T1/T3/T5 already apply to every other cross-object reference in this track,
extended one level up to the render boundary. Identity (`is`), not equality,
is the right check: `PairedRetainedSubset` carries a polars `DataFrame`,
whose `==` performs an elementwise comparison, never yields a scalar bool,
so a dataclass `==` across two subsets would raise, not compare.

**A refusal renders, it never vanishes (T6 P3).** Every per-(station, kind,
scale) computation is attempted; `EmptySubsetError`/`ZeroMassError`/
`ZeroClassifiableMassError` are caught AT THE POINT the value would be
built and turned into an explicit `AbsentResult` row carrying the caught
reason — never a dropped row, never a silent 0.0.

**Both sensitivities render without comparison (T6 P6, D4).** No spread
column, no ordering beyond the fixed axis order `sensitivity_combinations()`
already returns, no wording implying one matters more.

**Writes ONLY under `--out` (T6 P5).** Folding results into
`docs/design/dhm-precipitation-milestones.md` is a human step; this runner
never touches a tracked file.

Usage:
    uv run python scripts/dhm_precip/ma6_run.py --out <dir>

Environment:
    DHM_PRECIP_XLSX       path to the source workbook (required)
    DHM_PRECIP_ERA5_ROOT  root under which `era5_land/` (precipitation +
                          orography) lives (default `data/dhm_precip`)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import structlog  # noqa: E402

from scripts.dhm_precip import qc_mask  # noqa: E402
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_request import DATASET_ID  # noqa: E402
from scripts.dhm_precip.loader import DhmPrecipLoaderError  # noqa: E402
from scripts.dhm_precip.ma6_estimands import (  # noqa: E402
    BandMember,
    BandMemberEstimand,
    CategoricalScores,
    ConditionalAccumulatedDifference,
    ElevationBand,
    ElevationBandEstimand,
    EmptySubsetError,
    assign_elevation_band,
    categorical_scores,
    conditional_accumulated_difference,
    joint_wet_hour_conditional_intensity_bias,
    joint_wet_scale_subset,
    matched_hour_mean_difference,
    scale_subset,
    wet_hour_conditional_intensity_bias,
    wet_scale_subset,
)
from scripts.dhm_precip.ma6_lapse_check import (  # noqa: E402
    DEFAULT_PRECIP_DATA_ROOT,
    DEFAULT_PYRAMID_DIR,
    DEFAULT_T2M_DATA_ROOT,
    STANDARD_LAPSE_RATE_DEGC_PER_KM,
    GaugeToGaugeDiagnostic,
    TransectStationResult,
    build_transect_report,
)
from scripts.dhm_precip.ma6_mass_fraction import (  # noqa: E402
    PRIMARY_LAPSE_RATE_DEGC_PER_KM,
    PRIMARY_THRESHOLD_DEGC,
    ElevationBandMassFraction,
    StationElevationInputs,
    SubFreezingMassFraction,
    ZeroClassifiableMassError,
    ZeroMassError,
    build_sub_freezing_mass_fraction,
    discover_and_load_t2m_frames,
    load_dhm_station_elevations,
    sub_freezing_mass_fraction_sensitivity,
)
from scripts.dhm_precip.ma6_pairs import (  # noqa: E402
    GaugeMaskedPopulation,
    PairedSeries,
    Scale,
    build_paired_population,
    discover_precip_bundle,
    load_gauge_masked_population,
)
from scripts.dhm_precip.ma6_representativeness import (  # noqa: E402
    KHUMALTAR,
    KIRTIPUR,
    ElevationMismatchCovariate,
    NeighbourCellStat,
    WithinCellPairResult,
    compute_neighbour_cell_variability,
    compute_within_cell_pair,
    read_elevation_mismatch_covariates,
    read_operator_sensitivity_envelope,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams  # noqa: E402
from scripts.dhm_precip.pyramid_loader import PyramidLoaderError  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import polars as pl

    from scripts.dhm_precip.domain_types import Station

log = structlog.get_logger(__name__)


# --- Exit 2's structural guard ---


class MagnitudeMassFractionMismatchError(ValueError):
    """A `StationMagnitudeCell`/`BandMagnitudeCell` was given a magnitude
    estimand and a mass fraction that were not built from the SAME subset
    object (T6 P4, Exit 2). Checked by object identity — see module
    docstring for why `is`, not `==`, is the correct check here."""


class Ma6InputsConsistencyError(ValueError):
    """`Ma6Inputs` was given collections that do not agree on which
    stations they cover — e.g. a paired station absent from the t2m or
    elevation tables. Caught here, at construction, rather than as a
    confusing `KeyError` deep inside a per-station loop."""


class PrecipBundleIdentityMismatchError(ValueError):
    """Production wiring only: `load_dhm_station_elevations` and
    `discover_precip_bundle` each independently resolve 'the highest `NNNN`
    whose manifest validates' (P2/P6) — reading `station_grid_elevation.csv`
    and `series_nearest.nc` respectively. Both calls are pure and
    deterministic within one process, so this should never fire without a
    concurrent publish racing this run; it exists as a checked assumption,
    not a silently-trusted one."""


# --- T6 P1/P2's per-cell kinds ---


class MagnitudeKind(StrEnum):
    """D1's four magnitude kinds (T5 pin 1) — the ONLY estimand kinds Exit
    2's n+mass-fraction pairing applies to. Categorical scores (table c)
    are dimensionless skill scores, not magnitudes, and carry no mass
    fraction (D12's own `RetentionConditionality` label covers them
    instead)."""

    MATCHED_HOUR_MEAN_DIFFERENCE = "matched_hour_mean_difference"
    WET_HOUR_CONDITIONAL_INTENSITY_BIAS_GAUGE_ALONE = (
        "wet_hour_conditional_intensity_bias:gauge_alone"
    )
    WET_HOUR_CONDITIONAL_INTENSITY_BIAS_JOINT = (
        "wet_hour_conditional_intensity_bias:joint"
    )
    CONDITIONAL_ACCUMULATED_DIFFERENCE = "conditional_accumulated_difference"


_MAGNITUDE_SCALES_BY_KIND: dict[MagnitudeKind, tuple[Scale, ...]] = {
    MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE: (Scale.JJAS, Scale.DJF),
    MagnitudeKind.WET_HOUR_CONDITIONAL_INTENSITY_BIAS_GAUGE_ALONE: (
        Scale.JJAS,
        Scale.DJF,
    ),
    MagnitudeKind.WET_HOUR_CONDITIONAL_INTENSITY_BIAS_JOINT: (Scale.JJAS, Scale.DJF),
    MagnitudeKind.CONDITIONAL_ACCUMULATED_DIFFERENCE: (
        Scale.JJAS,
        Scale.DJF,
        Scale.DAILY,
        Scale.MONTHLY,
    ),
}

_UNIT_MM_PER_H = "mm/h"
_UNIT_MM = "mm"


@dataclass(frozen=True, kw_only=True, slots=True)
class AbsentResult:
    """T6 P3 — a refusal RENDERS. Carries the same identity a present
    result would (kind/scale/label) plus the caught refusal's own reason,
    so the row still appears rather than being silently dropped. Covers
    every table this runner refuses a cell in — magnitudes, bands,
    categorical scores and sensitivities alike — hence `kind: str` rather
    than the narrower `MagnitudeKind` (categorical/sensitivity rows are not
    magnitudes, T5/D12)."""

    kind: str
    scale: Scale
    label: str
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class StationMagnitudeCell:
    """Exit 2 / T6 P4 — pairs ONE per-station magnitude estimand with the
    `SubFreezingMassFraction` built from that estimand's OWN subset.
    Unconstructible with a mismatched pair: `__post_init__` verifies
    `mass_fraction.subset is estimand.subset` (module docstring)."""

    kind: MagnitudeKind
    estimand: BandMemberEstimand
    mass_fraction: SubFreezingMassFraction

    def __post_init__(self) -> None:
        if self.mass_fraction.subset is not self.estimand.subset:
            raise MagnitudeMassFractionMismatchError(
                f"{self.kind} at {self.estimand.station!r}/{self.estimand.scale}: "
                "mass_fraction was built from a DIFFERENT subset than its "
                "magnitude estimand — Exit 2 requires both to share exactly "
                "one subset object, never merely an equal n"
            )

    @property
    def station(self) -> Station:
        return self.estimand.station

    @property
    def scale(self) -> Scale:
        return self.estimand.scale

    @property
    def n(self) -> int:
        return self.estimand.n

    @property
    def value(self) -> float:
        """Reuses `BandMember`'s own type-dispatch (never re-implemented) —
        the same value a band average is built from, so table (a)'s
        per-station figure and table (b)'s band mean are provably the same
        quantity at different aggregation levels."""
        return BandMember(estimand=self.estimand).value

    @property
    def unit(self) -> str:
        return (
            _UNIT_MM
            if isinstance(self.estimand, ConditionalAccumulatedDifference)
            else _UNIT_MM_PER_H
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class BandMagnitudeCell:
    """The band-level analogue of `StationMagnitudeCell` (Exit 2 / T6 P4).
    `__post_init__` verifies every member station lines up between
    `estimand_band` and `mass_fraction_band`, and that each pair shares the
    same underlying subset by identity — the same check `StationMagnitudeCell`
    makes, applied member-by-member."""

    kind: MagnitudeKind
    estimand_band: ElevationBandEstimand
    mass_fraction_band: ElevationBandMassFraction

    def __post_init__(self) -> None:
        est_stations = tuple(m.station for m in self.estimand_band.members)
        mf_stations = tuple(m.station for m in self.mass_fraction_band.members)
        if est_stations != mf_stations:
            raise MagnitudeMassFractionMismatchError(
                f"{self.kind} at {self.estimand_band.band}: estimand_band "
                f"members {est_stations} do not match mass_fraction_band "
                f"members {mf_stations}"
            )
        est_subsets = tuple(m.estimand.subset for m in self.estimand_band.members)
        mf_subsets = tuple(m.subset for m in self.mass_fraction_band.members)
        if any(a is not b for a, b in zip(est_subsets, mf_subsets, strict=True)):
            raise MagnitudeMassFractionMismatchError(
                f"{self.kind} at {self.estimand_band.band}: a member's mass "
                "fraction was built from a DIFFERENT subset than its "
                "magnitude estimand"
            )

    @property
    def band(self) -> ElevationBand:
        return self.estimand_band.band

    @property
    def scale(self) -> Scale:
        return self.estimand_band.scale

    @property
    def station_count(self) -> int:
        return self.estimand_band.station_count

    @property
    def member_ns(self) -> tuple[int, ...]:
        return self.estimand_band.member_ns

    @property
    def mean_value(self) -> float:
        return self.estimand_band.mean_value

    @property
    def mean_mass_fraction(self) -> float:
        return self.mass_fraction_band.mean_sub_freezing_mass_fraction

    @property
    def mean_unclassifiable_mass_share(self) -> float:
        return self.mass_fraction_band.mean_unclassifiable_mass_share

    @property
    def unit(self) -> str:
        first = self.estimand_band.members[0].estimand
        return (
            _UNIT_MM
            if isinstance(first, ConditionalAccumulatedDifference)
            else _UNIT_MM_PER_H
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class StationSensitivityRow:
    """D4/D14/T5 pin 3 — the FIVE `sensitivity_combinations()` results for
    one station's conditional-accumulated-difference magnitude at one
    scale (the same estimand kind D4's own delivered table used). Rendered
    without comparison (T6 P6) — no field here ranks the five against each
    other."""

    station: Station
    scale: Scale
    fractions: tuple[SubFreezingMassFraction, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class RetentionRow:
    """Table (d), Exit 4/5 — one station's whole-record gauge exposure and
    its JJAS retained fraction (D8/D11's own metric, reused verbatim via
    `qc_mask._jjas_retained_fraction` — never re-derived)."""

    station: Station
    elevation_band: ElevationBand
    n_gauge_retained_whole_record: int
    jjas_retained_fraction: float | None


# --- the injected input bundle (CLAUDE.md dependency injection) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma6Inputs:
    """Everything T1-T5 already computed, bundled for T6 — the single
    injection point `run_ma6_comparison` takes, so tests construct this
    directly (synthetic, in-memory) and never touch disk, mirroring
    `coloc_run.DhmRetainedProvider`'s role one level up (one bundle instead
    of a per-station callback, since T6 aggregates across all 26 stations
    at once)."""

    gauge_population: GaugeMaskedPopulation
    paired: dict[Station, PairedSeries]
    t2m_by_station: dict[Station, pl.DataFrame]
    elevations: dict[Station, StationElevationInputs]
    precip_extraction_identity: str
    t2m_extraction_identity: str
    operator_sensitivity: pl.DataFrame
    elevation_mismatch: tuple[ElevationMismatchCovariate, ...]
    neighbour_cell_stats: tuple[NeighbourCellStat, ...]
    lapse_transect: tuple[TransectStationResult, ...]
    lapse_gauge_diagnostic: GaugeToGaugeDiagnostic | None
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        gauge_stations = set(self.gauge_population.by_station)
        paired_stations = set(self.paired)
        if not paired_stations <= gauge_stations:
            raise Ma6InputsConsistencyError(
                f"paired carries station(s) absent from gauge_population: "
                f"{sorted(str(s) for s in paired_stations - gauge_stations)}"
            )
        missing_t2m = paired_stations - set(self.t2m_by_station)
        if missing_t2m:
            raise Ma6InputsConsistencyError(
                f"paired carries station(s) absent from t2m_by_station: "
                f"{sorted(str(s) for s in missing_t2m)}"
            )
        missing_elev = paired_stations - set(self.elevations)
        if missing_elev:
            raise Ma6InputsConsistencyError(
                f"paired carries station(s) absent from elevations: "
                f"{sorted(str(s) for s in missing_elev)}"
            )


# --- per-station builders ---


def _build_magnitude_cell(
    kind: MagnitudeKind, estimand: BandMemberEstimand, label: str, inputs: Ma6Inputs
) -> StationMagnitudeCell | AbsentResult:
    """The ONE place a `SubFreezingMassFraction` is built and forced for a
    magnitude cell — forcing `.sub_freezing_mass_fraction`/
    `.unclassifiable_mass_share` HERE (not left lazy) means a
    `ZeroMassError`/`ZeroClassifiableMassError` is caught at BUILD time, so
    rendering later can never crash on an unforced property (T6 P3)."""
    try:
        mass_fraction = build_sub_freezing_mass_fraction(
            estimand,
            t2m_by_station=inputs.t2m_by_station,
            elevations_by_station=inputs.elevations,
        )
        _ = mass_fraction.sub_freezing_mass_fraction
        _ = mass_fraction.unclassifiable_mass_share
    except (ZeroMassError, ZeroClassifiableMassError) as exc:
        return AbsentResult(
            kind=str(kind), scale=estimand.scale, label=label, reason=str(exc)
        )
    return StationMagnitudeCell(
        kind=kind, estimand=estimand, mass_fraction=mass_fraction
    )


def _matched_hour_cell(
    station: Station, paired: PairedSeries, scale: Scale, inputs: Ma6Inputs
) -> StationMagnitudeCell | AbsentResult:
    kind = MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE
    try:
        sub = scale_subset(paired, scale=scale, params=inputs.params)
        estimand = matched_hour_mean_difference(sub)
    except EmptySubsetError as exc:
        return AbsentResult(
            kind=str(kind), scale=scale, label=str(station), reason=str(exc)
        )
    return _build_magnitude_cell(kind, estimand, str(station), inputs)


def _wet_hour_cells(
    station: Station, paired: PairedSeries, scale: Scale, inputs: Ma6Inputs
) -> list[StationMagnitudeCell | AbsentResult]:
    """gauge-alone and joint are built INDEPENDENTLY (never via the coupled
    `WetHourConditionalIntensityBiasComparison`, whose `__post_init__`
    forces BOTH eagerly) — joint is a NESTED subset of gauge-alone and can
    legitimately be empty while gauge-alone is not (e.g. gauge-wet hours
    ERA5 never called wet), and T6 P3 requires each to refuse
    independently, not as an all-or-nothing pair."""
    cells: list[StationMagnitudeCell | AbsentResult] = []

    gauge_kind = MagnitudeKind.WET_HOUR_CONDITIONAL_INTENSITY_BIAS_GAUGE_ALONE
    try:
        wet_sub = wet_scale_subset(paired, scale=scale, params=inputs.params)
        gauge_alone = wet_hour_conditional_intensity_bias(wet_sub)
    except EmptySubsetError as exc:
        cells.append(
            AbsentResult(
                kind=str(gauge_kind), scale=scale, label=str(station), reason=str(exc)
            )
        )
    else:
        cells.append(
            _build_magnitude_cell(gauge_kind, gauge_alone, str(station), inputs)
        )

    joint_kind = MagnitudeKind.WET_HOUR_CONDITIONAL_INTENSITY_BIAS_JOINT
    try:
        joint_sub = joint_wet_scale_subset(paired, scale=scale, params=inputs.params)
        joint = joint_wet_hour_conditional_intensity_bias(joint_sub)
    except EmptySubsetError as exc:
        cells.append(
            AbsentResult(
                kind=str(joint_kind), scale=scale, label=str(station), reason=str(exc)
            )
        )
    else:
        cells.append(_build_magnitude_cell(joint_kind, joint, str(station), inputs))

    return cells


def _accumulated_cell(
    station: Station, paired: PairedSeries, scale: Scale, inputs: Ma6Inputs
) -> tuple[
    StationMagnitudeCell | AbsentResult, ConditionalAccumulatedDifference | None
]:
    """Returns the rendered cell AND (when present) the underlying
    `ConditionalAccumulatedDifference` estimand itself, so callers building
    categorical scores (DAILY/MONTHLY) or the D4/D14 sensitivity appendix
    reuse the SAME object rather than recomputing an equal-but-different
    one."""
    kind = MagnitudeKind.CONDITIONAL_ACCUMULATED_DIFFERENCE
    try:
        sub = scale_subset(paired, scale=scale, params=inputs.params)
        estimand = conditional_accumulated_difference(sub)
    except EmptySubsetError as exc:
        return (
            AbsentResult(
                kind=str(kind), scale=scale, label=str(station), reason=str(exc)
            ),
            None,
        )
    cell = _build_magnitude_cell(kind, estimand, str(station), inputs)
    return cell, (estimand if isinstance(cell, StationMagnitudeCell) else None)


def _categorical_row(
    station: Station,
    scale: Scale,
    accumulated: ConditionalAccumulatedDifference | None,
    inputs: Ma6Inputs,
) -> CategoricalScores | AbsentResult:
    """D12, table (c) — reuses the SAME `accumulated` object table (a)'s
    accumulated-difference cell for this station/scale was built from."""
    if accumulated is None:
        return AbsentResult(
            kind="categorical_scores",
            scale=scale,
            label=str(station),
            reason="conditional accumulated difference subset was empty — "
            "no periods to classify",
        )
    try:
        return categorical_scores(accumulated, params=inputs.params)
    except EmptySubsetError as exc:
        return AbsentResult(
            kind="categorical_scores", scale=scale, label=str(station), reason=str(exc)
        )


def _sensitivity_row(
    station: Station,
    scale: Scale,
    accumulated: ConditionalAccumulatedDifference | None,
    inputs: Ma6Inputs,
) -> StationSensitivityRow | AbsentResult:
    if accumulated is None:
        return AbsentResult(
            kind="sensitivity",
            scale=scale,
            label=str(station),
            reason="conditional accumulated difference subset was empty — "
            "no sensitivity is computable",
        )
    try:
        fractions = sub_freezing_mass_fraction_sensitivity(
            accumulated,
            t2m_by_station=inputs.t2m_by_station,
            elevations_by_station=inputs.elevations,
        )
        for fraction in fractions:
            _ = fraction.sub_freezing_mass_fraction
            _ = fraction.unclassifiable_mass_share
    except (ZeroMassError, ZeroClassifiableMassError) as exc:
        return AbsentResult(
            kind="sensitivity", scale=scale, label=str(station), reason=str(exc)
        )
    return StationSensitivityRow(station=station, scale=scale, fractions=fractions)


def _station_results(
    station: Station, paired: PairedSeries, inputs: Ma6Inputs
) -> tuple[
    tuple[StationMagnitudeCell | AbsentResult, ...],
    tuple[CategoricalScores | AbsentResult, ...],
    tuple[StationSensitivityRow | AbsentResult, ...],
]:
    magnitudes: list[StationMagnitudeCell | AbsentResult] = []
    categorical: list[CategoricalScores | AbsentResult] = []
    sensitivities: list[StationSensitivityRow | AbsentResult] = []

    for scale in (Scale.JJAS, Scale.DJF):
        magnitudes.append(_matched_hour_cell(station, paired, scale, inputs))
        magnitudes += _wet_hour_cells(station, paired, scale, inputs)
        accumulated_cell, accumulated_estimand = _accumulated_cell(
            station, paired, scale, inputs
        )
        magnitudes.append(accumulated_cell)
        sensitivities.append(
            _sensitivity_row(station, scale, accumulated_estimand, inputs)
        )

    for scale in (Scale.DAILY, Scale.MONTHLY):
        accumulated_cell, accumulated_estimand = _accumulated_cell(
            station, paired, scale, inputs
        )
        magnitudes.append(accumulated_cell)
        categorical.append(
            _categorical_row(station, scale, accumulated_estimand, inputs)
        )

    return tuple(magnitudes), tuple(categorical), tuple(sensitivities)


def _build_band_magnitudes(
    station_cells: tuple[StationMagnitudeCell | AbsentResult, ...],
    station_elev_m: Mapping[Station, float],
) -> tuple[BandMagnitudeCell | AbsentResult, ...]:
    """D4a/D5a, T6 P2 table (b) — the band value is the unweighted mean of
    its PRESENT member stations (D13: stratify by retention, never filter
    on it — a station whose own cell is absent simply is not averaged in,
    same as a D11 exclusion already would remove it)."""
    results: list[BandMagnitudeCell | AbsentResult] = []
    for kind, scales in _MAGNITUDE_SCALES_BY_KIND.items():
        for scale in scales:
            for band in ElevationBand:
                present = [
                    cell
                    for cell in station_cells
                    if isinstance(cell, StationMagnitudeCell)
                    and cell.kind == kind
                    and cell.scale == scale
                    and assign_elevation_band(station_elev_m[cell.station]) == band
                ]
                if not present:
                    results.append(
                        AbsentResult(
                            kind=str(kind),
                            scale=scale,
                            label=str(band),
                            reason="zero member stations with a computable "
                            "magnitude in this band/scale",
                        )
                    )
                    continue
                estimand_band = ElevationBandEstimand(
                    band=band,
                    members=tuple(
                        BandMember(estimand=cell.estimand) for cell in present
                    ),
                    station_elev_m=station_elev_m,
                )
                mass_fraction_band = ElevationBandMassFraction(
                    band=band,
                    members=tuple(cell.mass_fraction for cell in present),
                    station_elev_m=station_elev_m,
                )
                results.append(
                    BandMagnitudeCell(
                        kind=kind,
                        estimand_band=estimand_band,
                        mass_fraction_band=mass_fraction_band,
                    )
                )
    return tuple(results)


# --- the tested, loader-agnostic core ---


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma6Report:
    generated_at: datetime
    params: DhmPrecipParams

    station_magnitudes: tuple[StationMagnitudeCell | AbsentResult, ...]
    band_magnitudes: tuple[BandMagnitudeCell | AbsentResult, ...]
    categorical: tuple[CategoricalScores | AbsentResult, ...]
    sensitivities: tuple[StationSensitivityRow | AbsentResult, ...]

    retention: tuple[RetentionRow, ...]
    exclusion_list: tuple[qc_mask.ExclusionListEntry, ...]

    operator_sensitivity_row_count: int
    operator_sensitivity_columns: tuple[str, ...]
    elevation_mismatch: tuple[ElevationMismatchCovariate, ...]
    neighbour_cell_stats: tuple[NeighbourCellStat, ...]

    within_cell_pair: WithinCellPairResult

    lapse_transect: tuple[TransectStationResult, ...]
    lapse_gauge_diagnostic: GaugeToGaugeDiagnostic | None

    precip_extraction_identity: str
    t2m_extraction_identity: str
    era5_land_dataset_id: str


def run_ma6_comparison(
    inputs: Ma6Inputs, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> Ma6Report:
    """The pure, testable core (CLAUDE.md dependency injection — no I/O):
    every table's content, computed from `inputs` alone."""
    magnitudes: list[StationMagnitudeCell | AbsentResult] = []
    categorical: list[CategoricalScores | AbsentResult] = []
    sensitivities: list[StationSensitivityRow | AbsentResult] = []
    for station in sorted(inputs.paired):
        station_magnitudes, station_categorical, station_sensitivities = (
            _station_results(station, inputs.paired[station], inputs)
        )
        magnitudes += station_magnitudes
        categorical += station_categorical
        sensitivities += station_sensitivities

    station_elev_m = {s: e.station_elev_m for s, e in inputs.elevations.items()}
    band_magnitudes = _build_band_magnitudes(tuple(magnitudes), station_elev_m)

    retention = tuple(
        RetentionRow(
            station=station,
            elevation_band=assign_elevation_band(station_elev_m[station]),
            n_gauge_retained_whole_record=series.frame.height,
            # D8/D11's own retention metric, reused verbatim rather than
            # re-derived (this track's own rule) — qc_mask has no public
            # per-station wrapper, only the exclusion-list-building
            # `build_exclusion_list`, which discards the retained station's
            # own fraction.
            jjas_retained_fraction=qc_mask._jjas_retained_fraction(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                inputs.gauge_population.accounting, station
            ),
        )
        for station, series in sorted(inputs.gauge_population.by_station.items())
    )

    within_cell_pair = compute_within_cell_pair(
        inputs.gauge_population,
        inputs.elevation_mismatch,
        station_a=KIRTIPUR,
        station_b=KHUMALTAR,
    )

    return Ma6Report(
        generated_at=clock(),
        params=inputs.params,
        station_magnitudes=tuple(magnitudes),
        band_magnitudes=band_magnitudes,
        categorical=tuple(categorical),
        sensitivities=tuple(sensitivities),
        retention=retention,
        exclusion_list=inputs.gauge_population.excluded,
        operator_sensitivity_row_count=inputs.operator_sensitivity.height,
        operator_sensitivity_columns=tuple(inputs.operator_sensitivity.columns),
        elevation_mismatch=inputs.elevation_mismatch,
        neighbour_cell_stats=inputs.neighbour_cell_stats,
        within_cell_pair=within_cell_pair,
        lapse_transect=inputs.lapse_transect,
        lapse_gauge_diagnostic=inputs.lapse_gauge_diagnostic,
        precip_extraction_identity=inputs.precip_extraction_identity,
        t2m_extraction_identity=inputs.t2m_extraction_identity,
        era5_land_dataset_id=DATASET_ID,
    )


# --- rendering ---


def _format_magnitude_value(cell: StationMagnitudeCell) -> str:
    """Exit 2 — value, n and the PRIMARY mass fraction glued into ONE
    string, so they occupy one literal markdown table cell and cannot be
    quoted apart without deliberately editing the string (D4's own
    mitigation, module docstring)."""
    mf = cell.mass_fraction
    return (
        f"{cell.value:+.4f} {cell.unit} (n={cell.n:,}; sub-freezing mass "
        f"frac @{mf.threshold_degc:g}°C/{mf.lapse_rate_degc_per_km:g}°C/km="
        f"{mf.sub_freezing_mass_fraction:.4f}; unclassifiable="
        f"{mf.unclassifiable_mass_share:.4f})"
    )


def _format_band_value(cell: BandMagnitudeCell) -> str:
    return (
        f"{cell.mean_value:+.4f} {cell.unit} (stations={cell.station_count}; "
        f"member n={cell.member_ns}; mean sub-freezing mass frac "
        f"@{PRIMARY_THRESHOLD_DEGC:g}°C/{PRIMARY_LAPSE_RATE_DEGC_PER_KM:g}°C/km="
        f"{cell.mean_mass_fraction:.4f}; mean unclassifiable="
        f"{cell.mean_unclassifiable_mass_share:.4f})"
    )


def _method_lines(report: Ma6Report) -> list[str]:
    return [
        "## Method and signing (Exit 3, D6, D13)",
        "",
        "- **D6 — every magnitude below is signed.** Undercatch is a "
        "property of catch EFFICIENCY: for a correctly-functioning gauge, "
        "catch <= true precipitation. It is **never** a bound on the "
        "reported post-QC total — Plan 173's QC is a physical-impossibility "
        "gate (`value_max = 200.0 mm/h`, deliberately unreachable rather "
        "than discriminating), not an outlier filter, so a single spurious "
        "reading can push a total ABOVE true precipitation.",
        "- **D13 — retention is a stratum, never a filter.** No aggregate "
        "below is scaled to a full period and no completeness threshold is "
        "applied anywhere; every magnitude carries its OWN retained-hour "
        "`n`, computed live from the exact subset it was built from.",
        "- **D4/Exit 2 — every magnitude cell carries its `n` and its "
        "PRIMARY (1.5°C / 6.5°C/km) sub-freezing mass fraction "
        "TOGETHER, in the same table cell.** The fraction ANNOTATES; it "
        "never gates, adjusts or corrects anything reported here.",
        "- **D1 (amended) — both wet-hour conditionings are reported.** "
        "gauge-alone conditions on the GAUGE being wet (partly measures "
        "ERA5 detection failure); joint conditions on BOTH sides being wet "
        "(doubly conditional). `joint` is a NESTED subset of `gauge_alone`, "
        "never an orthogonal component — their difference is not a "
        "decomposition.",
        "- **T6 P3 — a refusal renders.** A cell that legitimately cannot "
        "be computed (empty subset, zero mass, zero classifiable mass) "
        "appears below as `ABSENT` with its reason, never as a blank, a "
        "zero or a dropped row.",
        "",
        f"- generated: `{report.generated_at.isoformat()}`",
        "",
    ]


def _identities_lines(report: Ma6Report) -> list[str]:
    """Table (h), Exit 9 — every consumed identity, resolved by the highest "
    valid `NNNN` (P2/P6), never a run-numbered path or an identity glob."""
    return [
        "### (h) Consumed identities (Exit 9)",
        "",
        f"- ERA5-Land product: `{report.era5_land_dataset_id}`",
        f"- precipitation extraction bundle identity: "
        f"`{report.precip_extraction_identity}`",
        f"- t2m extraction bundle identity: `{report.t2m_extraction_identity}`",
        "",
        "Both bundle identities were resolved as the highest `NNNN` whose "
        "manifest validates (`discover_precip_bundle`/`discover_t2m_bundle`) "
        "— never a run-numbered path, never a glob on identity (an identity "
        "is a label, not a lookup key: the same identity may legitimately "
        "cover different payloads across publishes).",
        "",
    ]


def _station_magnitude_table(report: Ma6Report) -> list[str]:
    lines = [
        "### (a) Per-station magnitudes at the D3 scales (Exit 1)",
        "",
        "| station | scale | estimand | value |",
        "|---|---|---|---|",
    ]
    for cell in report.station_magnitudes:
        if isinstance(cell, AbsentResult):
            lines.append(
                f"| {cell.label} | {cell.scale} | {cell.kind} "
                f"| ABSENT — {cell.reason} |"
            )
        else:
            lines.append(
                f"| {cell.station} | {cell.scale} | {cell.kind} | "
                f"{_format_magnitude_value(cell)} |"
            )
    lines.append("")
    return lines


def _band_magnitude_table(report: Ma6Report) -> list[str]:
    lines = [
        "### (b) Per-band magnitudes with station counts (D4a/D5a, Exit 1)",
        "",
        "Band value = the UNWEIGHTED MEAN of its present member stations' "
        "own values (D5a) — never a pool of raw hours across stations.",
        "",
        "| band | scale | estimand | value |",
        "|---|---|---|---|",
    ]
    for cell in report.band_magnitudes:
        if isinstance(cell, AbsentResult):
            lines.append(
                f"| {cell.label} | {cell.scale} | {cell.kind} "
                f"| ABSENT — {cell.reason} |"
            )
        else:
            lines.append(
                f"| {cell.band} | {cell.scale} | {cell.kind} "
                f"| {_format_band_value(cell)} |"
            )
    lines.append("")
    return lines


def _sensitivity_table(report: Ma6Report) -> list[str]:
    """D4/D14/T5 pin 3, T6 P6 — the five combinations, one axis at a time,
    rendered side by side with NO spread/delta column and no ordering
    beyond `sensitivity_combinations()`'s own fixed order."""
    lines = [
        "### Sub-freezing mass-fraction sensitivity (D4/D14, one axis at a "
        "time — never compared, T6 P6)",
        "",
        "Basis: each station's own JJAS/DJF conditional-accumulated-"
        "difference magnitude (the same estimand D4's delivered table "
        "used). `n` is shared across all five combinations (same subset).",
        "",
        "| station | scale | n | (0.0°C, 6.5) | (1.5°C, 5.0) | "
        "(1.5°C, 6.5) [primary] | (1.5°C, 9.8) | (2.0°C, 6.5) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report.sensitivities:
        if isinstance(row, AbsentResult):
            lines.append(
                f"| {row.label} | {row.scale} | — | ABSENT — {row.reason} | | | | |"
            )
            continue
        values = " | ".join(
            f"{f.sub_freezing_mass_fraction:.4f}" for f in row.fractions
        )
        lines.append(
            f"| {row.station} | {row.scale} | {row.fractions[0].n:,} | {values} |"
        )
    lines.append("")
    return lines


def _categorical_table(report: Ma6Report) -> list[str]:
    lines = [
        "### (c) Categorical scores at daily and monthly grain (D12, Exit 1)",
        "",
        "Reportable only as CONDITIONAL-ON-RETENTION estimands (Rule 1). "
        "JJAS/DJF grain is refused, not computed, elsewhere (D12's vacuity "
        "trap) — this table is DAILY/MONTHLY only.",
        "",
        "| station | scale | n periods | n hours | POD | FAR | CSI |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.categorical:
        if isinstance(row, AbsentResult):
            lines.append(
                f"| {row.label} | {row.scale} | ABSENT — {row.reason} | | | | |"
            )
        else:
            lines.append(
                f"| {row.station} | {row.scale} | {row.n_periods} | {row.n_hours} | "
                f"{row.pod:.3f} | {row.far:.3f} | {row.csi:.3f} |"
            )
    lines.append("")
    return lines


def _retention_table(report: Ma6Report) -> list[str]:
    lines = [
        "### (d) Retention stratification (Exit 4)",
        "",
        "Retention is a STRATUM here, never a filter (D13): no aggregate "
        "above is scaled to this fraction, and no completeness threshold "
        "is applied.",
        "",
        "| station | elevation band | n gauge-retained (whole record) | "
        "JJAS retained fraction |",
        "|---|---|---|---|",
    ]
    for row in report.retention:
        fraction = (
            f"{row.jjas_retained_fraction:.4f}"
            if row.jjas_retained_fraction is not None
            else "n/a (no observed JJAS hours)"
        )
        lines.append(
            f"| {row.station} | {row.elevation_band} | "
            f"{row.n_gauge_retained_whole_record:,} | {fraction} |"
        )
    lines.append("")

    lines += ["#### D11 exclusion list (Exit 5)", ""]
    if not report.exclusion_list:
        scored: list[tuple[float, RetentionRow]] = [
            (row.jjas_retained_fraction, row)
            for row in report.retention
            if row.jjas_retained_fraction is not None
        ]
        worst = min(scored, key=lambda item: item[0]) if scored else None
        worst_note = (
            f" (worst measured JJAS retention: {worst[1].station} at {worst[0]:.4f})"
            if worst is not None
            else ""
        )
        lines += [
            "**Empty** — Plan 173's M-A6 exclusion predicate removed zero "
            f"stations{worst_note}. This is a MEASURED result, not a skipped "
            "step: the 0.50 minimum-JJAS-retained-fraction floor never binds "
            "on the current delivery.",
            "",
        ]
    else:
        lines += ["| station | JJAS retained fraction | reason |", "|---|---|---|"]
        for entry in report.exclusion_list:
            fraction = (
                f"{entry.jjas_retained_fraction:.4f}"
                if entry.jjas_retained_fraction is not None
                else "n/a"
            )
            lines.append(f"| {entry.station} | {fraction} | {entry.reason.value} |")
        lines.append("")
    return lines


def _representativeness_lines(report: Ma6Report) -> list[str]:
    lines = [
        "### (e) Representativeness — characterised, not decomposed (D5, Exit 6)",
        "",
        "One point gauge against one ~110 km² ERA5-Land cell cannot "
        "empirically separate grid representativeness error from model "
        "error (D5). Three characterisations follow; none decomposes the "
        "other.",
        "",
        "#### Operator-sensitivity envelope (D9)",
        "",
        f"Published `operator_sensitivity.csv`, read AS PUBLISHED, "
        f"structurally validated only (columns present; `statistic` "
        f"domain and `sign_agreement_fraction`'s legitimate `STATION`-row "
        f"nulls are never asserted, D9): "
        f"{report.operator_sensitivity_row_count:,} rows, columns "
        f"{list(report.operator_sensitivity_columns)}. The full table is "
        "not reproduced here — see table (h) for the bundle identity it "
        "was published under.",
        "",
        "#### Station-to-grid elevation mismatch (D7 — datum-unreconciled, "
        "descriptive covariate only, never a phase explanation)",
        "",
        "| station | station elev (m) | orography elev (m) | mismatch (m) | "
        "datum reconciled | stations in cell |",
        "|---|---|---|---|---|---|",
    ]
    for row in sorted(report.elevation_mismatch, key=lambda r: r.station):
        lines.append(
            f"| {row.station} | {row.station_elev_m:.1f} | "
            f"{row.orography_elev_m:.1f} | {row.elev_mismatch_m:+.1f} | "
            f"{row.datum_reconciled.value} | {row.stations_in_cell} |"
        )
    lines += [
        "",
        "#### Neighbouring-cell variability (Chebyshev-8 stencil, period-"
        "total precipitation, range and population CV)",
        "",
        "| station | assigned total (mm) | n neighbours | range (mm) | CV |",
        "|---|---|---|---|---|",
    ]
    for row in sorted(report.neighbour_cell_stats, key=lambda r: r.station):
        cv = f"{row.neighbour_cv:.4f}" if row.neighbour_cv is not None else "n/a"
        lines.append(
            f"| {row.station} | {row.assigned_total_mm:.1f} | "
            f"{row.n_neighbours} | {row.neighbour_range_mm:.1f} | {cv} |"
        )
    lines.append("")
    return lines


def _within_cell_table(report: Ma6Report) -> list[str]:
    """Exit 7 asks for the common-retained-hour count and each station's
    OWN exposure — both are RAW COUNTS. No percentage is computed here: a
    fraction against the whole possible hourly axis (D8's own 93.12 % /
    92.77 % / 90.06 %) needs that axis length as a denominator, which
    `WithinCellPairResult` does not carry (D8 measured it once, upstream, in
    T1's substrate) — and `n_common_retained / n_*_gauge_retained` is a
    DIFFERENT quantity (an overlap ratio between the two stations' own
    retained sets, not either one's retention against the full record).
    Rendering that ratio under a 'retention' label would misrepresent it."""
    pair = report.within_cell_pair
    lines = [
        "### (f) Within-cell pair — Kirtipur/Khumaltar (D8, Exit 7)",
        "",
        "DESCRIPTIVE ONLY — no lower-bound claim. n = 1 pair, one valley, "
        "one separation (4.33 km), never a network-wide estimate.",
        "",
        f"- shared cell: `{pair.shared_cell_id}`",
        f"- {pair.station_a} own gauge-retained exposure (whole record): "
        f"{pair.n_a_gauge_retained:,} h",
        f"- {pair.station_b} own gauge-retained exposure (whole record): "
        f"{pair.n_b_gauge_retained:,} h",
        f"- common-retained hours: {pair.n_common_retained:,}",
    ]
    if pair.sum_a_mm is not None and pair.sum_b_mm is not None:
        lines.append(f"- {pair.station_a} total: {pair.sum_a_mm:.1f} mm")
        lines.append(f"- {pair.station_b} total: {pair.sum_b_mm:.1f} mm")
    if pair.accumulated_difference_mm is not None:
        lines.append(
            f"- accumulated difference (a − b): "
            f"{pair.accumulated_difference_mm:+.1f} mm"
        )
    else:
        lines.append("- accumulated difference: n/a — zero common-retained hours")
    if pair.mean_difference_mm_per_h is not None:
        lines.append(f"- mean difference: {pair.mean_difference_mm_per_h:+.4f} mm/h")
    lines.append("")
    return lines


def _lapse_transect_table(report: Ma6Report) -> list[str]:
    lines = [
        "### (g) D14 lapse-rate transect and its Pyramid check (Exit 8)",
        "",
        f"Standard rate: {STANDARD_LAPSE_RATE_DEGC_PER_KM} °C/km, never "
        "fitted or tuned to this check (D14). If the check fails, the "
        "reported uncertainty on the mass-fraction column widens; the rate "
        "is never refitted.",
        "",
        "| Pyramid station | station elev (m) | orography (m) | "
        "correction (°C) | ERA5 raw | ERA5 corrected | Pyramid | "
        "discrepancy | n(ERA5) | n(Pyramid) | n(paired) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.lapse_transect:
        lines.append(
            f"| {r.pyramid_station} | {r.station_elevation_m:.0f} | "
            f"{r.orography_elev_m:.1f} | {r.lapse_correction_degc:+.2f} | "
            f"{r.era5_raw_hour_equalised_degc:.2f} | "
            f"{r.era5_corrected_hour_equalised_degc:.2f} | "
            f"{r.pyramid_hour_equalised_degc:.2f} | {r.discrepancy_degc:+.2f} "
            f"| {r.n_era5_hours} | {r.n_pyramid_retained} | {r.n_paired} |"
        )
    lines.append("")
    if report.lapse_gauge_diagnostic is not None:
        diag = report.lapse_gauge_diagnostic
        lines += [
            "AWS1/AWS4 shared-cell gauge-to-gauge diagnostic (observed, "
            "never a candidate replacement rate, D14): common-retained "
            f"{diag.n_common_retained:,} h, means {diag.mean_a_degc:.2f} vs "
            f"{diag.mean_b_degc:.2f} °C.",
            "",
        ]
    return lines


def _write_report(path: Path, report: Ma6Report) -> None:
    lines = [
        "# DHM precipitation — M-A6 gauge vs ERA5-Land comparison",
        "",
    ]
    lines += _method_lines(report)
    lines += _identities_lines(report)
    lines += _station_magnitude_table(report)
    lines += _sensitivity_table(report)
    lines += _band_magnitude_table(report)
    lines += _categorical_table(report)
    lines += _retention_table(report)
    lines += _representativeness_lines(report)
    lines += _within_cell_table(report)
    lines += _lapse_transect_table(report)
    lines += [
        "## Non-goals",
        "",
        "Not an exit condition here: any statement about whether ERA5-Land "
        "is fit to force a hydrological model (M-A8/M-DEC), and any "
        "correction, adjustment or downscaling design.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n")


# --- production wiring ---


def _production_inputs(
    *,
    precip_data_root: Path,
    t2m_data_root: Path,
    pyramid_dir: Path,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> Ma6Inputs:
    """Wires T1-T5's real production paths into one `Ma6Inputs` bundle —
    the only place in this module that touches disk."""
    gauge_population = load_gauge_masked_population(params=params)

    elevations, elevations_identity = load_dhm_station_elevations(precip_data_root)
    bundle_dir, manifest = discover_precip_bundle(precip_data_root)
    if elevations_identity != manifest.extraction_identity:
        raise PrecipBundleIdentityMismatchError(
            f"station_grid_elevation.csv was read from bundle "
            f"{elevations_identity!r} but series_nearest.nc discovery "
            f"resolved to {manifest.extraction_identity!r}"
        )
    paired = build_paired_population(gauge_population, bundle_dir)
    operator_sensitivity = read_operator_sensitivity_envelope(bundle_dir, manifest)
    elevation_mismatch = read_elevation_mismatch_covariates(bundle_dir, manifest)
    neighbour_cell_stats = compute_neighbour_cell_variability(
        elevation_mismatch, data_root=precip_data_root
    )

    t2m_by_station, t2m_identity = discover_and_load_t2m_frames(t2m_data_root)

    lapse_transect, lapse_gauge_diagnostic = build_transect_report(
        precip_data_root=precip_data_root,
        t2m_data_root=t2m_data_root,
        pyramid_dir=pyramid_dir,
    )

    return Ma6Inputs(
        gauge_population=gauge_population,
        paired=paired,
        t2m_by_station=t2m_by_station,
        elevations=elevations,
        precip_extraction_identity=manifest.extraction_identity,
        t2m_extraction_identity=t2m_identity,
        operator_sensitivity=operator_sensitivity,
        elevation_mismatch=elevation_mismatch,
        neighbour_cell_stats=neighbour_cell_stats,
        lapse_transect=lapse_transect,
        lapse_gauge_diagnostic=lapse_gauge_diagnostic,
        params=params,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ma6_run", description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory for the report"
    )
    parser.add_argument("--precip-data-root", type=Path, default=None)
    parser.add_argument("--t2m-data-root", type=Path, default=DEFAULT_T2M_DATA_ROOT)
    parser.add_argument("--pyramid-dir", type=Path, default=DEFAULT_PYRAMID_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    precip_data_root: Path = (
        args.precip_data_root
        if args.precip_data_root is not None
        else Path(os.environ.get("DHM_PRECIP_ERA5_ROOT", str(DEFAULT_PRECIP_DATA_ROOT)))
    )

    try:
        inputs = _production_inputs(
            precip_data_root=precip_data_root,
            t2m_data_root=args.t2m_data_root,
            pyramid_dir=args.pyramid_dir,
        )
        report = run_ma6_comparison(inputs)
    except (
        DhmPrecipLoaderError,
        Era5AcquisitionError,
        PyramidLoaderError,
        StationSetMismatchError,
        PrecipBundleIdentityMismatchError,
        OSError,
    ) as exc:
        log.error("ma6_run.cli.failed", error=str(exc), error_type=type(exc).__name__)
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / "ma6_report.md"
    _write_report(out_path, report)
    log.info("ma6_run.cli.complete", out=str(out_path))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
