"""Plan 182 (M-A10) D9 — the verdict rule.

**Evaluated as an ORDERED sequence of gates. The first gate that fires
decides; no later gate can overturn it.** This ordering is what removes the
round-2 contradiction (a case satisfying both "refuted" and "INDETERMINATE"
at once) and the round-2 undefined "toward" direction for antipodal peaks
(`circular.moves_toward` never needs a signed direction).

Gate 0 (adequacy, D5): small-sample, insufficient disjoint-period data,
  non-stationary window, or too-wide bootstrap peak spread -> INDETERMINATE.
Gate 1 (matched-resolution, D7.1): DHM@matched-resolution vs Pyramid disagree
  by more than the circular threshold -> INDETERMINATE (sub-threshold counts
  are not the explanation).
Gate 2 (ablation, D7.2): circular movement of the DHM peak between all-values
  and matched-resolution classifies H1 SUPPORTED / REFUTED / INDETERMINATE.

Per-station first, then synthesis (D9): disagreement between stations'
verdicts is INDETERMINATE for the group, reported as the finding, never
averaged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from scripts.dhm_precip.circular import circ_dist_hours, moves_toward
from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.params import DhmPrecipParams

Gate = Literal["adequacy", "matched_resolution", "ablation"]


class Verdict(StrEnum):
    H1_SUPPORTED = "H1_SUPPORTED"
    H1_REFUTED = "H1_REFUTED"
    INDETERMINATE = "INDETERMINATE"


class EvidenceFailure(StrEnum):
    """A window whose evidence could not be computed at all — a DATA state,
    never a bug. The caller (`coloc_adjudication`) maps the typed
    conditions raised by `stats_coloc`/`coloc_bootstrap` (an all-zero
    ladder rung, an empty paired population, nothing to resample) onto
    these, so gate 0 can return INDETERMINATE instead of the pipeline
    crashing partway through an adjudication."""

    INSUFFICIENT_SIGNAL = "insufficient_signal"
    INSUFFICIENT_COMMON_DATA = "insufficient_common_data"


class IndeterminateReason(StrEnum):
    ADEQUACY_INSUFFICIENT_SIGNAL = "adequacy_insufficient_signal"
    ADEQUACY_INSUFFICIENT_COMMON_DATA = "adequacy_insufficient_common_data"
    ADEQUACY_SMALL_SAMPLE = "adequacy_small_sample"
    ADEQUACY_INSUFFICIENT_DISJOINT_DATA = "adequacy_insufficient_disjoint_data"
    ADEQUACY_NONSTATIONARY = "adequacy_nonstationary"
    ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE = "adequacy_bootstrap_spread_too_wide"
    MATCHED_RESOLUTION_DISAGREEMENT = "matched_resolution_disagreement"
    ABLATION_AMBIGUOUS = "ablation_ambiguous"
    ABLATION_MOVED_AWAY = "ablation_moved_away"
    STATION_DISAGREEMENT = "station_disagreement"


class MissingPeakEvidenceError(ValueError):
    """A `StationVerdictInputs` declared no evidence failure but did not
    carry all three peak hours. Every peak is required to reach gates 1-2,
    so a `None` peak without a declared failure is a caller bug, not a data
    state — surfaced at construction rather than as an obscure `None`
    comparison inside the gate sequence."""


@dataclass(frozen=True, kw_only=True, slots=True)
class StationVerdictInputs:
    """One station's pre-computed evidence, ready for the gate sequence.

    **D11 — this is the FULL-RECORD window's evidence.** The overlap window
    is corroboration and never reaches this type. Computing the peaks
    (`stats_coloc.peak_hour`) and the D12 Pyramid disjoint-period difference
    is the CALLER's job — this type is the seam between that
    evidence-gathering and the pure decision rule below."""

    station: Station
    evidence_failure: EvidenceFailure | None = None
    """Set when the adjudicated window's evidence could not be computed at
    all; gate 0 maps it straight to INDETERMINATE. When set, the peak
    fields below are legitimately `None`."""
    season_year_count: int
    """D5/D11 — the ADJUDICATED (full-record) window's season-year count,
    the smaller of the two networks'. D11: 'Both sides clear the 5-season
    threshold comfortably, so a decisive verdict is reachable.'"""
    pyramid_disjoint_period_data_sufficient: bool
    """D12 — whether BOTH sides of
    `params.coloc_pyramid_stationarity_split_year` had a usable PYRAMID
    peak. The split is Pyramid's, never DHM's: DHM has no pre-2020 data at
    all, so a DHM split makes the check vacuous and would license a
    non-contemporaneous comparison across an unexamined phase shift."""
    pyramid_disjoint_period_peak_diff_hours: float
    """D12 — only meaningful when
    `pyramid_disjoint_period_data_sufficient` is `True` (that gate fires
    first otherwise)."""
    bootstrap_spread_hours: float
    """D5 — the circular bootstrap spread of the peak hour across monsoon
    season-years, computed on the SAME population the peaks below come
    from."""
    dhm_peak_all_hour: float | None = None
    dhm_peak_matched_resolution_hour: float | None = None
    pyramid_peak_hour: float | None = None

    def __post_init__(self) -> None:
        if self.evidence_failure is None and (
            self.dhm_peak_all_hour is None
            or self.dhm_peak_matched_resolution_hour is None
            or self.pyramid_peak_hour is None
        ):
            raise MissingPeakEvidenceError(
                f"{self.station!r}: all three peak hours are required unless "
                "an evidence_failure is declared"
            )


_EVIDENCE_FAILURE_REASONS: dict[EvidenceFailure, IndeterminateReason] = {
    EvidenceFailure.INSUFFICIENT_SIGNAL: (
        IndeterminateReason.ADEQUACY_INSUFFICIENT_SIGNAL
    ),
    EvidenceFailure.INSUFFICIENT_COMMON_DATA: (
        IndeterminateReason.ADEQUACY_INSUFFICIENT_COMMON_DATA
    ),
}


@dataclass(frozen=True, kw_only=True, slots=True)
class StationVerdict:
    station: Station
    verdict: Verdict
    reason: IndeterminateReason | None
    gate_stopped_at: Gate
    matched_resolution_diff_hours: float | None
    """`None` only when gate 0 stopped the sequence before it was computed."""
    ablation_movement_hours: float | None
    """`None` only when gate 0 or gate 1 stopped the sequence first."""
    moved_toward_pyramid: bool
    """D9 — whether the ablation moved the DHM peak TOWARD the Pyramid peak
    (circular-distance reduction, never a signed direction). `False` when
    gate 0 or gate 1 stopped the sequence before ablation movement was
    computed."""


def evaluate_station_verdict(
    inputs: StationVerdictInputs, params: DhmPrecipParams
) -> StationVerdict:
    # --- Gate 0: adequacy (D5/D11/D12), on the ADJUDICATED window ---
    if inputs.evidence_failure is not None:
        return StationVerdict(
            station=inputs.station,
            verdict=Verdict.INDETERMINATE,
            reason=_EVIDENCE_FAILURE_REASONS[inputs.evidence_failure],
            gate_stopped_at="adequacy",
            matched_resolution_diff_hours=None,
            ablation_movement_hours=None,
            moved_toward_pyramid=False,
        )
    if inputs.season_year_count < params.coloc_min_season_years_for_adequacy:
        return StationVerdict(
            station=inputs.station,
            verdict=Verdict.INDETERMINATE,
            reason=IndeterminateReason.ADEQUACY_SMALL_SAMPLE,
            gate_stopped_at="adequacy",
            matched_resolution_diff_hours=None,
            ablation_movement_hours=None,
            moved_toward_pyramid=False,
        )
    if not inputs.pyramid_disjoint_period_data_sufficient:
        return StationVerdict(
            station=inputs.station,
            verdict=Verdict.INDETERMINATE,
            reason=IndeterminateReason.ADEQUACY_INSUFFICIENT_DISJOINT_DATA,
            gate_stopped_at="adequacy",
            matched_resolution_diff_hours=None,
            ablation_movement_hours=None,
            moved_toward_pyramid=False,
        )
    if (
        inputs.pyramid_disjoint_period_peak_diff_hours
        > params.coloc_stationarity_max_circular_diff_hours
    ):
        return StationVerdict(
            station=inputs.station,
            verdict=Verdict.INDETERMINATE,
            reason=IndeterminateReason.ADEQUACY_NONSTATIONARY,
            gate_stopped_at="adequacy",
            matched_resolution_diff_hours=None,
            ablation_movement_hours=None,
            moved_toward_pyramid=False,
        )
    if inputs.bootstrap_spread_hours > params.coloc_bootstrap_adequate_max_spread_hours:
        return StationVerdict(
            station=inputs.station,
            verdict=Verdict.INDETERMINATE,
            reason=IndeterminateReason.ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE,
            gate_stopped_at="adequacy",
            matched_resolution_diff_hours=None,
            ablation_movement_hours=None,
            moved_toward_pyramid=False,
        )

    # Past gate 0 without an evidence failure, `__post_init__` already
    # guarantees all three peaks are present; re-stated here so the gates
    # below are type-safe without relying on `assert` (stripped under -O).
    if (
        inputs.dhm_peak_all_hour is None
        or inputs.dhm_peak_matched_resolution_hour is None
        or inputs.pyramid_peak_hour is None
    ):
        raise MissingPeakEvidenceError(
            f"{inputs.station!r}: reached the matched-resolution gate with a "
            "missing peak hour and no declared evidence failure"
        )

    # --- Gate 1: matched-resolution agreement (D7.1) ---
    matched_diff = circ_dist_hours(
        inputs.dhm_peak_matched_resolution_hour, inputs.pyramid_peak_hour
    )
    if matched_diff > params.coloc_matched_resolution_max_circular_diff_hours:
        return StationVerdict(
            station=inputs.station,
            verdict=Verdict.INDETERMINATE,
            reason=IndeterminateReason.MATCHED_RESOLUTION_DISAGREEMENT,
            gate_stopped_at="matched_resolution",
            matched_resolution_diff_hours=matched_diff,
            ablation_movement_hours=None,
            moved_toward_pyramid=False,
        )

    # --- Gate 2: ablation movement (D7.2) ---
    movement = circ_dist_hours(
        inputs.dhm_peak_all_hour, inputs.dhm_peak_matched_resolution_hour
    )
    toward = moves_toward(
        before=inputs.dhm_peak_all_hour,
        after=inputs.dhm_peak_matched_resolution_hour,
        target=inputs.pyramid_peak_hour,
    )
    verdict: Verdict
    reason: IndeterminateReason | None
    if movement >= params.coloc_ablation_supported_min_hours:
        # D9 — "'Toward' is defined as a REDUCTION IN CIRCULAR DISTANCE to
        # the Pyramid peak". A large movement that does NOT reduce the
        # distance to the independent instrument is not evidence that the
        # sub-threshold counts carried a spurious phase: H1 is about the
        # ablation bringing DHM INTO agreement, so movement away (or
        # equidistant) is INDETERMINATE, never "supported".
        if toward:
            verdict, reason = Verdict.H1_SUPPORTED, None
        else:
            verdict, reason = (
                Verdict.INDETERMINATE,
                IndeterminateReason.ABLATION_MOVED_AWAY,
            )
    elif movement < params.coloc_ablation_refuted_max_hours:
        verdict, reason = Verdict.H1_REFUTED, None
    else:
        verdict, reason = Verdict.INDETERMINATE, IndeterminateReason.ABLATION_AMBIGUOUS

    return StationVerdict(
        station=inputs.station,
        verdict=verdict,
        reason=reason,
        gate_stopped_at="ablation",
        matched_resolution_diff_hours=matched_diff,
        ablation_movement_hours=movement,
        moved_toward_pyramid=toward,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class SynthesisVerdict:
    verdict: Verdict
    reason: IndeterminateReason | None
    station_verdicts: tuple[StationVerdict, ...]


class DuplicateStationVerdictError(ValueError):
    """`synthesize_verdict` received the same station's verdict more than
    once — a duplicated station can manufacture a decisive Group A verdict
    while the second REQUIRED station's evidence never actually
    contributed."""


class UnregisteredStationVerdictError(ValueError):
    """`synthesize_verdict` did not receive EXACTLY the two registered
    co-located DHM stations (`coloc_pairs.COLOCATED_PAIRS`) — either an
    unexpected station was included, or a required one is missing. D9's
    synthesis is defined over the group's two stations, never an ad hoc
    subset or superset."""


def synthesize_verdict(
    station_verdicts: Sequence[StationVerdict],
) -> SynthesisVerdict:
    """D9 — 'Disagreement => INDETERMINATE for Group A, and the disagreement
    is itself reported as the finding, never averaged.' Requires EXACTLY
    the two registered co-located DHM stations, each exactly once."""
    stations = [v.station for v in station_verdicts]
    if len(stations) != len(set(stations)):
        raise DuplicateStationVerdictError(
            f"duplicate station verdict(s) among {stations} — synthesis "
            "requires each registered station's verdict exactly once"
        )
    expected = {pair.dhm_station for pair in COLOCATED_PAIRS}
    got = set(stations)
    if got != expected:
        raise UnregisteredStationVerdictError(
            f"expected exactly the registered stations {sorted(expected)}, "
            f"got {sorted(got)} (missing {sorted(expected - got)}, "
            f"unexpected {sorted(got - expected)})"
        )
    verdicts = {v.verdict for v in station_verdicts}
    if len(verdicts) > 1:
        return SynthesisVerdict(
            verdict=Verdict.INDETERMINATE,
            reason=IndeterminateReason.STATION_DISAGREEMENT,
            station_verdicts=tuple(station_verdicts),
        )
    (only,) = verdicts
    reason: IndeterminateReason | None = None
    if only == Verdict.INDETERMINATE:
        reasons = {v.reason for v in station_verdicts}
        reason = next(iter(reasons)) if len(reasons) == 1 else None
    return SynthesisVerdict(
        verdict=only, reason=reason, station_verdicts=tuple(station_verdicts)
    )
