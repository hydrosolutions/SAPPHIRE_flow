"""Plan 182 (M-A10) D9 — the verdict rule: an ORDERED sequence of gates.

Round 3 finding this locks: "the round-2 version was CONTRADICTORY" (a case
could satisfy both 'refuted' and 'INDETERMINATE' at once) and left 'toward'
undefined for antipodal peaks. The fix is gates evaluated IN ORDER, the FIRST
gate that fires decides, and no later gate can overturn it.

Imports are guarded so a not-yet-implemented module fails these tests as a
genuine RED assertion, never a collection-time ImportError.
"""

from __future__ import annotations

import pytest

try:
    from scripts.dhm_precip.coloc_verdict import (
        DuplicateStationVerdictError,
        IndeterminateReason,
        StationVerdictInputs,
        UnregisteredStationVerdictError,
        Verdict,
        evaluate_station_verdict,
        synthesize_verdict,
    )
except ImportError:
    DuplicateStationVerdictError = None  # type: ignore[assignment]
    IndeterminateReason = None  # type: ignore[assignment]
    StationVerdictInputs = None  # type: ignore[assignment]
    UnregisteredStationVerdictError = None  # type: ignore[assignment]
    Verdict = None  # type: ignore[assignment]
    evaluate_station_verdict = None  # type: ignore[assignment]
    synthesize_verdict = None  # type: ignore[assignment]

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

_STATION = Station("Lukla Airport")
_LUKLA = Station("Lukla Airport")
_SYANGBOCHE = Station("Syangboche Airport")


def _inputs(
    *,
    station: Station = _STATION,
    season_year_count: int = 6,
    pyramid_disjoint_period_data_sufficient: bool = True,
    pyramid_disjoint_period_peak_diff_hours: float = 1.0,
    bootstrap_spread_hours: float = 1.0,
    dhm_peak_all_hour: float = 2.0,
    dhm_peak_matched_resolution_hour: float = 2.0,
    pyramid_peak_hour: float = 2.0,
) -> object:
    assert StationVerdictInputs is not None, "StationVerdictInputs not implemented yet"
    return StationVerdictInputs(
        station=station,
        season_year_count=season_year_count,
        pyramid_disjoint_period_data_sufficient=pyramid_disjoint_period_data_sufficient,
        pyramid_disjoint_period_peak_diff_hours=pyramid_disjoint_period_peak_diff_hours,
        bootstrap_spread_hours=bootstrap_spread_hours,
        dhm_peak_all_hour=dhm_peak_all_hour,
        dhm_peak_matched_resolution_hour=dhm_peak_matched_resolution_hour,
        pyramid_peak_hour=pyramid_peak_hour,
    )


class TestAdequacyGateStopsFirst:
    def test_small_sample_is_indeterminate_regardless_of_downstream_signal(
        self,
    ) -> None:
        """Gate 0 (D5/D9): fewer than 5 season-years cannot on its own
        establish adequacy. This must fire BEFORE the ablation gate is even
        evaluated — even when the ablation movement (10h) would, on its own,
        satisfy the 'H1 supported' threshold."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            season_year_count=3,
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=12.0,  # 10h movement
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ADEQUACY_SMALL_SAMPLE
        assert result.gate_stopped_at == "adequacy"

    def test_nonstationary_disjoint_periods_is_indeterminate(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            season_year_count=6, pyramid_disjoint_period_peak_diff_hours=6.0
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ADEQUACY_NONSTATIONARY
        assert result.gate_stopped_at == "adequacy"

    def test_insufficient_disjoint_data_is_indeterminate_regardless_of_diff(
        self,
    ) -> None:
        """The real DHM source record spans only 2020-2025 in its entirety
        (`docs/design/dhm-precipitation-vision.md:20`), so a station's own
        full record can legitimately fail to straddle
        `coloc_dhm_stationarity_split_year` — the caller sets
        `pyramid_disjoint_period_data_sufficient=False` rather than crash, and this
        gate must fire BEFORE the (meaningless) diff value is even
        consulted, exactly like the small-sample gate above."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            pyramid_disjoint_period_data_sufficient=False,
            pyramid_disjoint_period_peak_diff_hours=0.0,  # would otherwise pass fine
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=12.0,  # 10h movement
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ADEQUACY_INSUFFICIENT_DISJOINT_DATA
        assert result.gate_stopped_at == "adequacy"

    def test_wide_bootstrap_spread_is_indeterminate_regardless_of_downstream_signal(
        self,
    ) -> None:
        """D5: 'If the circular bootstrap spread on the peak hour exceeds
        ±2h, the overlap window cannot support a phase verdict.' A wide
        spread (12h) must fire this gate even when everything else (5
        season-years, stationary, matched-resolution agreement, large
        ablation movement) would otherwise cleanly support H1 — this locks
        the finding that spread was computed but never gated on."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            season_year_count=5,
            bootstrap_spread_hours=12.0,
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=7.0,  # 5h movement -> supported
            pyramid_peak_hour=7.0,  # agrees at matched resolution
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE
        assert result.gate_stopped_at == "adequacy"


class TestMatchedResolutionGate:
    def test_disagreement_is_indeterminate_even_with_large_ablation_movement(
        self,
    ) -> None:
        """Gate 1 (D7.1/D9): if DHM@matched-resolution and Pyramid disagree by
        more than 4h circularly, sub-threshold counts are NOT the
        explanation — INDETERMINATE, regardless of how large the ablation
        movement (all-values vs matched-resolution) was. This is the 'no
        later gate can overturn it' rule: a movement of 10h (which alone
        would satisfy 'H1 supported') must NOT flip this back to supported."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            season_year_count=6,
            pyramid_disjoint_period_peak_diff_hours=1.0,
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=12.0,  # 10h ablation movement
            pyramid_peak_hour=18.0,  # |12 - 18| = 6h > 4h gate
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.MATCHED_RESOLUTION_DISAGREEMENT
        assert result.gate_stopped_at == "matched_resolution"


class TestAblationGate:
    def test_large_movement_supports_h1(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=7.0,  # 5h movement >= 4h
            pyramid_peak_hour=7.0,  # agrees at matched resolution
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.H1_SUPPORTED
        assert result.gate_stopped_at == "ablation"
        assert result.ablation_movement_hours == 5.0

    def test_small_movement_refutes_h1(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=3.0,  # 1h movement < 2h
            pyramid_peak_hour=3.0,
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.H1_REFUTED
        assert result.gate_stopped_at == "ablation"
        assert result.ablation_movement_hours == 1.0

    def test_ambiguous_movement_band_is_indeterminate(self) -> None:
        """The round-2 contradiction this locks: a movement value that
        cannot cleanly fall in EITHER the supported or refuted band must
        resolve to exactly one outcome (INDETERMINATE), never both."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=5.0,  # 3h movement: 2h <= x < 4h
            pyramid_peak_hour=5.0,
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ABLATION_AMBIGUOUS
        assert result.gate_stopped_at == "ablation"

    def test_large_movement_away_from_pyramid_is_indeterminate(self) -> None:
        """D9: '"Toward" is defined as a REDUCTION IN CIRCULAR DISTANCE to
        the Pyramid peak'. Counterexample the pre-fix code got wrong: the
        all-values DHM peak ALREADY coincides with Pyramid (2 == 2) and the
        ablation moves it to 6 — exactly 4h of movement, which satisfies
        gate 2's 'supported' threshold, and exactly 4h of matched-resolution
        disagreement, which passes gate 1 (`> 4.0` is false). The ablation
        moved the peak AWAY (circular distance 0h -> 4h), so the
        sub-threshold counts cannot be what carried a spurious phase: H1
        is NOT supported by this evidence."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        inputs = _inputs(
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=6.0,
            pyramid_peak_hour=2.0,
        )
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.moved_toward_pyramid is False
        assert result.ablation_movement_hours == 4.0
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ABLATION_MOVED_AWAY
        assert result.gate_stopped_at == "ablation"


class TestSynthesis:
    """D9's synthesis is defined over EXACTLY the two registered co-located
    DHM stations (`coloc_pairs.COLOCATED_PAIRS`: Lukla Airport, Syangboche
    Airport) — never a duplicated station standing in for the second, and
    never an ad hoc set. Every test below uses the two REAL distinct
    stations, not the same station twice."""

    def test_agreeing_stations_synthesize_to_the_shared_verdict(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        assert synthesize_verdict is not None, "synthesize_verdict not implemented yet"
        a = evaluate_station_verdict(
            _inputs(
                station=_LUKLA,
                dhm_peak_all_hour=2.0,
                dhm_peak_matched_resolution_hour=7.0,
                pyramid_peak_hour=7.0,
            ),
            DEFAULT_PARAMS,
        )
        b = evaluate_station_verdict(
            _inputs(
                station=_SYANGBOCHE,
                dhm_peak_all_hour=2.0,
                dhm_peak_matched_resolution_hour=7.0,
                pyramid_peak_hour=7.0,
            ),
            DEFAULT_PARAMS,
        )
        synthesis = synthesize_verdict([a, b])
        assert synthesis.verdict == Verdict.H1_SUPPORTED

    def test_disagreeing_stations_synthesize_to_indeterminate_not_averaged(
        self,
    ) -> None:
        """D9: 'Disagreement => INDETERMINATE for Group A, and the
        disagreement is itself reported as the finding, never averaged.'"""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        assert synthesize_verdict is not None, "synthesize_verdict not implemented yet"
        supported = evaluate_station_verdict(
            _inputs(
                station=_LUKLA,
                dhm_peak_all_hour=2.0,
                dhm_peak_matched_resolution_hour=7.0,
                pyramid_peak_hour=7.0,
            ),
            DEFAULT_PARAMS,
        )
        refuted = evaluate_station_verdict(
            _inputs(
                station=_SYANGBOCHE,
                dhm_peak_all_hour=2.0,
                dhm_peak_matched_resolution_hour=3.0,
                pyramid_peak_hour=3.0,
            ),
            DEFAULT_PARAMS,
        )
        synthesis = synthesize_verdict([supported, refuted])
        assert synthesis.verdict == Verdict.INDETERMINATE
        assert synthesis.reason == IndeterminateReason.STATION_DISAGREEMENT
        assert synthesis.station_verdicts == (supported, refuted)

    def test_rejects_the_same_station_supplied_twice(self) -> None:
        """Locks the finding: a duplicated station must never stand in for
        the second required station — it must raise, never silently
        synthesize a decisive Group A verdict from one station's evidence
        counted twice."""
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        assert synthesize_verdict is not None, "synthesize_verdict not implemented yet"
        assert DuplicateStationVerdictError is not None, (
            "DuplicateStationVerdictError not implemented yet"
        )
        supported_inputs = _inputs(
            station=_LUKLA,
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=7.0,
            pyramid_peak_hour=7.0,
        )
        a = evaluate_station_verdict(supported_inputs, DEFAULT_PARAMS)
        b = evaluate_station_verdict(supported_inputs, DEFAULT_PARAMS)
        with pytest.raises(DuplicateStationVerdictError):
            synthesize_verdict([a, b])

    def test_rejects_a_single_station_missing_the_second(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        assert synthesize_verdict is not None, "synthesize_verdict not implemented yet"
        assert UnregisteredStationVerdictError is not None, (
            "UnregisteredStationVerdictError not implemented yet"
        )
        only = evaluate_station_verdict(_inputs(station=_LUKLA), DEFAULT_PARAMS)
        with pytest.raises(UnregisteredStationVerdictError):
            synthesize_verdict([only])

    def test_rejects_an_unregistered_station(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        assert synthesize_verdict is not None, "synthesize_verdict not implemented yet"
        assert UnregisteredStationVerdictError is not None, (
            "UnregisteredStationVerdictError not implemented yet"
        )
        lukla = evaluate_station_verdict(_inputs(station=_LUKLA), DEFAULT_PARAMS)
        other = evaluate_station_verdict(
            _inputs(station=Station("Not A Registered Station")), DEFAULT_PARAMS
        )
        with pytest.raises(UnregisteredStationVerdictError):
            synthesize_verdict([lukla, other])
