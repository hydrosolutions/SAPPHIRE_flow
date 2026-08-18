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


class IndeterminateReason(StrEnum):
    ADEQUACY_SMALL_SAMPLE = "adequacy_small_sample"
    ADEQUACY_INSUFFICIENT_DISJOINT_DATA = "adequacy_insufficient_disjoint_data"
    ADEQUACY_NONSTATIONARY = "adequacy_nonstationary"
    ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE = "adequacy_bootstrap_spread_too_wide"
    MATCHED_RESOLUTION_DISAGREEMENT = "matched_resolution_disagreement"
    ABLATION_AMBIGUOUS = "ablation_ambiguous"
    ABLATION_MOVED_AWAY = "ablation_moved_away"
    STATION_DISAGREEMENT = "station_disagreement"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationVerdictInputs:
    """One station's pre-computed evidence, ready for the gate sequence.
    Computing `dhm_peak_all_hour` / `dhm_peak_matched_resolution_hour` /
    `pyramid_peak_hour` (`stats_coloc.peak_hour`) and
    `disjoint_period_peak_diff_hours` (pre-2020 vs 2020+ circular peak
    difference on the full record) is the CALLER's job — this type is the
    seam between that evidence-gathering and the pure decision rule below."""

    station: Station
    season_year_count: int
    """D5 — the overlap window's season-year count feeding the adequacy
    gate. Not the bootstrap `n_season_years` for a DIFFERENT window; the
    caller supplies whichever window it is evaluating."""
    disjoint_period_data_sufficient: bool
    """D5 — whether BOTH sides of `params.coloc_full_record_split_year`
    had at least one retained profile row for this station. The real DHM
    source record only spans 2020-2025 in its entirety, so a station whose
    own history doesn't straddle the split (or a synthetic/partial input)
    can legitimately fail this — the caller must set it `False` rather than
    let peak-hour extraction raise, and gate 0 maps `False` straight to
    INDETERMINATE, never a crash and never a silently-skipped stationarity
    check."""
    disjoint_period_peak_diff_hours: float
    """Only meaningful when `disjoint_period_data_sufficient` is `True` —
    ignored otherwise (that gate fires first)."""
    bootstrap_spread_hours: float
    """D5 — the circular bootstrap spread of the peak hour across monsoon
    season-years, on the SAME window `season_year_count` describes. 'If the
    circular bootstrap spread on the peak hour exceeds ±2h, the overlap
    window cannot support a phase verdict' — gated below, before the
    matched-resolution comparison ever runs."""
    dhm_peak_all_hour: float
    dhm_peak_matched_resolution_hour: float
    pyramid_peak_hour: float


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
    # --- Gate 0: adequacy (D5) ---
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
    if not inputs.disjoint_period_data_sufficient:
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
        inputs.disjoint_period_peak_diff_hours
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
