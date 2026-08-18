"""Plan 182 (M-A10) D9 — the verdict rule: an ORDERED sequence of gates.

Round 3 finding this locks: "the round-2 version was CONTRADICTORY" (a case
could satisfy both 'refuted' and 'INDETERMINATE' at once) and left 'toward'
undefined for antipodal peaks. The fix is gates evaluated IN ORDER, the FIRST
gate that fires decides, and no later gate can overturn it.

Imports are guarded so a not-yet-implemented module fails these tests as a
genuine RED assertion, never a collection-time ImportError.
"""

from __future__ import annotations

try:
    from scripts.dhm_precip.coloc_verdict import (
        IndeterminateReason,
        StationVerdictInputs,
        Verdict,
        evaluate_station_verdict,
        synthesize_verdict,
    )
except ImportError:
    IndeterminateReason = None  # type: ignore[assignment]
    StationVerdictInputs = None  # type: ignore[assignment]
    Verdict = None  # type: ignore[assignment]
    evaluate_station_verdict = None  # type: ignore[assignment]
    synthesize_verdict = None  # type: ignore[assignment]

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

_STATION = Station("Lukla Airport")


def _inputs(
    *,
    season_year_count: int = 6,
    disjoint_period_peak_diff_hours: float = 1.0,
    dhm_peak_all_hour: float = 2.0,
    dhm_peak_matched_resolution_hour: float = 2.0,
    pyramid_peak_hour: float = 2.0,
) -> object:
    assert StationVerdictInputs is not None, "StationVerdictInputs not implemented yet"
    return StationVerdictInputs(
        station=_STATION,
        season_year_count=season_year_count,
        disjoint_period_peak_diff_hours=disjoint_period_peak_diff_hours,
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
        inputs = _inputs(season_year_count=6, disjoint_period_peak_diff_hours=6.0)
        result = evaluate_station_verdict(inputs, DEFAULT_PARAMS)
        assert result.verdict == Verdict.INDETERMINATE
        assert result.reason == IndeterminateReason.ADEQUACY_NONSTATIONARY
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
            disjoint_period_peak_diff_hours=1.0,
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


class TestSynthesis:
    def test_agreeing_stations_synthesize_to_the_shared_verdict(self) -> None:
        assert evaluate_station_verdict is not None, (
            "evaluate_station_verdict not implemented yet"
        )
        assert synthesize_verdict is not None, "synthesize_verdict not implemented yet"
        supported_inputs = _inputs(
            dhm_peak_all_hour=2.0,
            dhm_peak_matched_resolution_hour=7.0,
            pyramid_peak_hour=7.0,
        )
        a = evaluate_station_verdict(supported_inputs, DEFAULT_PARAMS)
        b = evaluate_station_verdict(supported_inputs, DEFAULT_PARAMS)
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
                dhm_peak_all_hour=2.0,
                dhm_peak_matched_resolution_hour=7.0,
                pyramid_peak_hour=7.0,
            ),
            DEFAULT_PARAMS,
        )
        refuted = evaluate_station_verdict(
            _inputs(
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
