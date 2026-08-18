"""Plan 182 (M-A10) — end-to-end composition: D2 UTC->NPT reconciliation,
the D11 full-record adjudication (with the overlap as corroboration), the
threshold ladder, D3 pairing, the D5 bootstrap, D12's PYRAMID stationarity
check and the D9 verdict gates, over synthetic data.

Every frame here respects the REAL record spans: DHM 2020-2025
(`docs/design/dhm-precipitation-vision.md:20`), Pyramid Lukla 2005-2023.
No test fabricates a pre-2020 DHM year to make a stationarity split look
easy — D12 exists precisely because DHM has none.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.coloc_adjudication import (
    ColocWindow,
    WindowEvidence,
    WindowUnavailable,
    adjudicate_station,
)
from scripts.dhm_precip.coloc_verdict import (
    EvidenceFailure,
    IndeterminateReason,
    Verdict,
)
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from collections.abc import Callable

_DHM_STATION = Station("Lukla Airport")
_PYRAMID_STATION = Station("AWS3 Lukla")
_HOUR_OFFSET = DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset

# The REAL registry spans for this pair (`coloc_pairs.COLOCATED_PAIRS`).
_DHM_FULL_YEARS = list(range(2020, 2026))
_PYRAMID_FULL_YEARS = list(range(2005, 2024))
_OVERLAP_YEARS = [2021, 2022, 2023]
_DAYS = range(1, 7)


def _july_timestamps(years: list[int], days: range = _DAYS) -> list[datetime]:
    return [
        datetime(year, 7, day, hour)
        for year in years
        for day in days
        for hour in range(24)
    ]


def _frame(
    *,
    station: Station,
    timestamps: list[datetime],
    value_at: Callable[[datetime], float],
) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"station": str(station), "timestamp": ts, "value_mm": value_at(ts)}
            for ts in timestamps
        ]
    )


def _dhm_utc(
    years: list[int],
    value_at: Callable[[datetime], float],
    *,
    days: range = _DAYS,
) -> pl.DataFrame:
    return _frame(
        station=_DHM_STATION,
        timestamps=_july_timestamps(years, days),
        value_at=value_at,
    )


def _pyramid_npt(
    years: list[int],
    value_at: Callable[[datetime], float],
    *,
    days: range = _DAYS,
) -> pl.DataFrame:
    """Pyramid is NPT wall-clock. To be pairable with a DHM UTC frame the
    timestamps must be that frame's, shifted by the D2 offset."""
    return _frame(
        station=_PYRAMID_STATION,
        timestamps=[
            ts + timedelta(hours=_HOUR_OFFSET) for ts in _july_timestamps(years, days)
        ],
        value_at=value_at,
    )


def _peaks_at(hour: int) -> Callable[[datetime], float]:
    return lambda ts: 5.0 if ts.hour == hour else 0.0


def _adjudicate(
    *,
    dhm_full: pl.DataFrame,
    dhm_overlap: pl.DataFrame,
    pyramid_full: pl.DataFrame,
    pyramid_overlap: pl.DataFrame,
    seed: int,
):
    return adjudicate_station(
        dhm_station=_DHM_STATION,
        pyramid_station=_PYRAMID_STATION,
        dhm_full_record_retained=dhm_full,
        dhm_overlap_retained=dhm_overlap,
        pyramid_full_record_retained=pyramid_full,
        pyramid_overlap_retained=pyramid_overlap,
        rng=random.Random(seed),
        params=DEFAULT_PARAMS,
    )


def _agreeing_case(seed: int = 7):
    """The canonical agreeing case at the REAL spans: DHM peaks at UTC 8
    (-> NPT 14) across 2020-2025; Pyramid peaks at NPT 14 across
    2005-2023."""
    return _adjudicate(
        dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(8)),
        dhm_overlap=_dhm_utc(_OVERLAP_YEARS, _peaks_at(8)),
        pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(14)),
        pyramid_overlap=_pyramid_npt(_OVERLAP_YEARS, _peaks_at(14)),
        seed=seed,
    )


class TestFullRecordIsTheAdjudicatedWindow:
    """D11 — 'The verdict is adjudicated on the FULL RECORD. The overlap
    window is computed and reported as CORROBORATION and does not gate the
    verdict.'"""

    def test_a_thin_overlap_does_not_block_a_decisive_full_record_verdict(
        self,
    ) -> None:
        result = _agreeing_case()

        overlap = result.overlap
        assert isinstance(overlap, WindowEvidence)
        # The REAL Lukla overlap is 3 monsoons — below D5's 5-season floor,
        # which is exactly why gating the verdict on it made the
        # deliverable unreachable.
        assert overlap.season_year_count < (
            DEFAULT_PARAMS.coloc_min_season_years_for_adequacy
        )
        assert result.station_verdict.verdict == Verdict.H1_REFUTED

    def test_the_verdict_reads_the_full_record_evidence(self) -> None:
        result = _agreeing_case()
        full = result.full_record
        assert isinstance(full, WindowEvidence)
        assert full.window == ColocWindow.FULL_RECORD
        assert full.paired is False
        assert full.season_year_count >= (
            DEFAULT_PARAMS.coloc_min_season_years_for_adequacy
        )
        assert full.threshold_ladder_peaks[0.0] == 14
        assert full.threshold_ladder_peaks[0.2] == 14
        assert full.pyramid_peak_hour == 14
        assert result.station_verdict.gate_stopped_at == "ablation"

    def test_both_windows_carry_their_own_retention_and_profiles(self) -> None:
        result = _agreeing_case()
        full, overlap = result.full_record, result.overlap
        assert isinstance(full, WindowEvidence)
        assert isinstance(overlap, WindowEvidence)
        # Window-scoped, never the whole file's count: the Pyramid full
        # record spans 19 seasons, the overlap 3.
        assert full.n_pyramid_retained > overlap.n_pyramid_retained
        assert full.n_common_retained is None  # unpaired (D11)
        assert overlap.n_common_retained == overlap.n_pyramid_retained
        assert full.threshold_ladder_profiles[0.0].height == 24
        assert overlap.threshold_ladder_profiles[0.0].height == 24
        assert "n" in full.pyramid_profile.columns

    def test_wet_hour_fraction_is_paired_only(self) -> None:
        """D3 — 'a wet-hour fraction over differently-selected populations
        is not a comparison'. The unpaired full record therefore reports
        none at all."""
        result = _agreeing_case()
        full, overlap = result.full_record, result.overlap
        assert isinstance(full, WindowEvidence)
        assert isinstance(overlap, WindowEvidence)
        assert full.wet_hour_fraction is None
        assert overlap.wet_hour_fraction is not None

    def test_overlap_vs_full_record_agreement_is_reported(self) -> None:
        result = _agreeing_case()
        assert result.overlap_vs_full_record_peak_diff_hours == 0.0


class TestAdjudicateStationAppliesUtcToNptConversion:
    """Locks the D2 blocker: without the UTC->NPT conversion, DHM's UTC
    hour-of-day is compared directly against Pyramid's NPT hour-of-day as
    if they were the same clock."""

    def test_naive_unconverted_comparison_would_wrongly_refute_h1(self) -> None:
        """DHM peaks at UTC hour 2 (-> NPT ~8 once correctly converted);
        Pyramid peaks at NPT hour 23 — a real ~9h circular disagreement
        once both are on the same clock, mirroring the plan's own worked
        Lukla example. Naive raw-hour comparison sees `circ_dist(2, 23) ==
        3h`, UNDER the 4h gate-1 threshold, so a buggy implementation
        proceeds to the ablation gate (0h movement) and wrongly REFUTES
        H1. The correct conversion must stop at gate 1 instead."""
        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(2)),
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, _peaks_at(2)),
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(23)),
            pyramid_overlap=_pyramid_npt(_OVERLAP_YEARS, _peaks_at(23)),
            seed=3,
        )

        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.MATCHED_RESOLUTION_DISAGREEMENT
        )
        assert result.station_verdict.gate_stopped_at == "matched_resolution"


class TestOverlapWindowPairsBeforeComputingPeaks:
    """Locks the D3/D9 major finding for the window that CAN be paired: the
    overlap ladder and Pyramid peaks must come from the SAME common-retained
    population, never from each side's independently-selected one."""

    def test_dhm_only_outlier_days_absent_from_pyramid_do_not_move_the_peaks(
        self,
    ) -> None:
        common_days = range(1, 4)
        outlier_days = range(4, 6)
        outliers = set(_july_timestamps(_OVERLAP_YEARS, outlier_days))

        def dhm_value(ts: datetime) -> float:
            if ts in outliers:
                return 1000.0 if ts.hour == 3 else 0.0
            return 5.0 if ts.hour == 8 else 0.0

        dhm_overlap = _dhm_utc(
            _OVERLAP_YEARS, dhm_value, days=range(1, 6)
        )  # 3 common + 2 DHM-only days per season
        pyramid_overlap = _pyramid_npt(_OVERLAP_YEARS, _peaks_at(14), days=common_days)

        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(8)),
            dhm_overlap=dhm_overlap,
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(14)),
            pyramid_overlap=pyramid_overlap,
            seed=11,
        )

        overlap = result.overlap
        assert isinstance(overlap, WindowEvidence)
        assert overlap.n_common_retained == overlap.n_pyramid_retained
        # Computed on DHM's OWN retained population, hour 3's outlier-driven
        # mean would swamp hour 14's. On the paired population it never
        # enters at all.
        assert overlap.threshold_ladder_peaks[0.0] == 14
        assert overlap.threshold_ladder_peaks[0.2] == 14
        assert overlap.pyramid_peak_hour == 14

    def test_the_overlap_bootstrap_resamples_the_paired_population(self) -> None:
        """MAJOR — the bootstrap must resample the SAME population the
        window's peak came from. Here the DHM-only days carry a DIFFERENT
        peak hour every season; bootstrapping the unpaired population would
        report a wide spread around a peak that was never adjudicated."""
        common_days = range(1, 4)
        outlier_days = range(4, 6)
        outliers = set(_july_timestamps(_OVERLAP_YEARS, outlier_days))
        wandering = {2021: 1, 2022: 9, 2023: 17}

        def dhm_value(ts: datetime) -> float:
            if ts in outliers:
                return 50.0 if ts.hour == wandering[ts.year] else 0.0
            return 5.0 if ts.hour == 8 else 0.0

        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(8)),
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, dhm_value, days=range(1, 6)),
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(14)),
            pyramid_overlap=_pyramid_npt(
                _OVERLAP_YEARS, _peaks_at(14), days=common_days
            ),
            seed=23,
        )

        overlap = result.overlap
        assert isinstance(overlap, WindowEvidence)
        assert set(overlap.bootstrap.peak_hours) == {14}
        assert overlap.bootstrap.spread_hours == 0.0


class TestPyramidStationarityGate:
    """D12 — the disjoint-period split is PYRAMID's."""

    def test_a_pyramid_record_that_does_not_straddle_the_split_is_indeterminate(
        self,
    ) -> None:
        """A Pyramid record that ends before the split year has no
        post-split partition, so the check that licenses the
        non-contemporaneous full-record comparison cannot be made —
        INDETERMINATE, never a crash and never a silently skipped check.
        (15 pre-split seasons, so the small-sample gate is NOT what
        fires.)"""
        pre_split_only = list(range(2005, 2020))
        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(8)),
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, _peaks_at(8)),
            pyramid_full=_pyramid_npt(pre_split_only, _peaks_at(14)),
            pyramid_overlap=_pyramid_npt(_OVERLAP_YEARS, _peaks_at(14)),
            seed=5,
        )

        assert result.pyramid_stationarity.data_sufficient is False
        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.ADEQUACY_INSUFFICIENT_DISJOINT_DATA
        )
        assert result.station_verdict.gate_stopped_at == "adequacy"

    def test_a_shifted_pyramid_phase_fails_the_gate_while_dhm_looks_stable(
        self,
    ) -> None:
        split = DEFAULT_PARAMS.coloc_pyramid_stationarity_split_year

        def shifted(ts: datetime) -> float:
            peak = 2 if ts.year < split else 14
            return 5.0 if ts.hour == peak else 0.0

        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(8)),  # perfectly stable
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, _peaks_at(8)),
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, shifted),
            pyramid_overlap=_pyramid_npt(_OVERLAP_YEARS, _peaks_at(14)),
            seed=29,
        )

        assert result.dhm_stationarity.peak_diff_hours == 0.0  # vacuous, by design
        assert result.pyramid_stationarity.peak_diff_hours == 12.0
        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason == IndeterminateReason.ADEQUACY_NONSTATIONARY
        )


class TestBootstrapSpreadGate:
    def test_full_record_seasons_with_wildly_different_peaks_go_indeterminate(
        self,
    ) -> None:
        peak_by_year = {2020: 2, 2021: 8, 2022: 14, 2023: 20, 2024: 2, 2025: 8}

        def wandering(ts: datetime) -> float:
            return 5.0 if ts.hour == peak_by_year[ts.year] else 0.0

        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, wandering),
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, _peaks_at(8)),
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(14)),
            pyramid_overlap=_pyramid_npt(_OVERLAP_YEARS, _peaks_at(14)),
            seed=13,
        )

        full = result.full_record
        assert isinstance(full, WindowEvidence)
        assert full.bootstrap.spread_hours > (
            DEFAULT_PARAMS.coloc_bootstrap_adequate_max_spread_hours
        )
        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE
        )


class TestInsufficientSignalStatesAreIndeterminateNotCrashes:
    """MAJOR — an all-zero matched-resolution rung, or an empty paired
    population, are DATA states. They must resolve to INDETERMINATE with a
    reason, never escape as `NonPositiveGrandMeanError` /
    `EmptyPairedPopulationError` from the middle of an adjudication."""

    def test_an_all_sub_threshold_dhm_record_is_indeterminate(self) -> None:
        """Every DHM value sits below the 0.2 mm matched-resolution rung, so
        the ablation zeroes the entire record and no peak exists there."""
        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, lambda _ts: 0.05),
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, lambda _ts: 0.05),
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(14)),
            pyramid_overlap=_pyramid_npt(_OVERLAP_YEARS, _peaks_at(14)),
            seed=31,
        )

        full = result.full_record
        assert isinstance(full, WindowUnavailable)
        assert full.failure == EvidenceFailure.INSUFFICIENT_SIGNAL
        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.ADEQUACY_INSUFFICIENT_SIGNAL
        )
        assert result.station_verdict.gate_stopped_at == "adequacy"

    def test_an_unpairable_overlap_is_reported_without_gating_the_verdict(
        self,
    ) -> None:
        """D11 — the overlap is corroboration. When no timestamp is shared
        (here the Pyramid overlap sits on a different day range), the
        corroboration is simply unavailable and the full-record verdict
        still stands."""
        result = _adjudicate(
            dhm_full=_dhm_utc(_DHM_FULL_YEARS, _peaks_at(8)),
            dhm_overlap=_dhm_utc(_OVERLAP_YEARS, _peaks_at(8), days=range(1, 4)),
            pyramid_full=_pyramid_npt(_PYRAMID_FULL_YEARS, _peaks_at(14)),
            pyramid_overlap=_pyramid_npt(
                _OVERLAP_YEARS, _peaks_at(14), days=range(10, 13)
            ),
            seed=37,
        )

        overlap = result.overlap
        assert isinstance(overlap, WindowUnavailable)
        assert overlap.failure == EvidenceFailure.INSUFFICIENT_COMMON_DATA
        assert overlap.n_common_retained == 0
        assert result.overlap_vs_full_record_peak_diff_hours is None
        assert result.station_verdict.verdict == Verdict.H1_REFUTED
