"""Plan 182 (M-A10) — end-to-end composition: D2 UTC->NPT reconciliation,
threshold ladder, D3 pairing, D5 bootstrap+stationarity, D9 verdict, over
synthetic data (no real Pyramid files needed).

Timestamps are honest about the real DHM source record: it spans
2020-01-01 -> 2025-12-31 in its entirety
(`docs/design/dhm-precipitation-vision.md:20`) — no test here fabricates a
pre-2020 DHM year to make the stationarity split look easy.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.coloc_adjudication import adjudicate_station
from scripts.dhm_precip.coloc_verdict import IndeterminateReason, Verdict
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from collections.abc import Callable

_DHM_STATION = Station("Lukla Airport")
_PYRAMID_STATION = "AWS3 Lukla"
_HOUR_OFFSET = DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset


def _frame(
    *,
    station: object,
    timestamps: list[datetime],
    value_at: Callable[[datetime], float],
) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"station": station, "timestamp": ts, "value_mm": value_at(ts)}
            for ts in timestamps
        ]
    )


def _july_timestamps(years: list[int], days: range) -> list[datetime]:
    return [
        datetime(year, 7, day, hour)
        for year in years
        for day in days
        for hour in range(24)
    ]


class TestAdjudicateStationRefutesWhenBothInstrumentsAgree:
    def test_full_pipeline_refutes_h1_on_matching_synthetic_data(self) -> None:
        """DHM (UTC) peaks at hour 8 -> NPT 14 once correctly converted;
        Pyramid (already NPT) peaks at hour 14 too — SAME true peak at
        every threshold, on the SAME (paired) population — and 5
        season-years are provided (adequacy passes), straddling the params'
        `coloc_dhm_stationarity_split_year` (2020-2022 vs 2023-2024) so the
        stationarity gate is exercised rather than skipped. The whole
        pipeline should refute H1 without any gate raising."""
        years = [2020, 2021, 2022, 2023, 2024]
        days = range(1, 21)  # 20 days/year, comfortably inside JJAS
        dhm_timestamps = _july_timestamps(years, days)
        pyramid_timestamps = [
            ts + timedelta(hours=_HOUR_OFFSET) for ts in dhm_timestamps
        ]

        def dhm_value(ts: datetime) -> float:
            # Real signal at UTC hour 8 (-> NPT 14); a tiny UNIFORM
            # sub-threshold noise floor sits on every hour (a common
            # scalar — ablation must not move the peak, per D7.2).
            return 5.05 if ts.hour == 8 else 0.05

        def pyramid_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 14 else 0.0

        dhm_overlap = _frame(
            station=_DHM_STATION, timestamps=dhm_timestamps, value_at=dhm_value
        )
        dhm_full_record = dhm_overlap
        pyramid = _frame(
            station=_PYRAMID_STATION,
            timestamps=pyramid_timestamps,
            value_at=pyramid_value,
        )

        result = adjudicate_station(
            dhm_station=_DHM_STATION,
            dhm_overlap_retained=dhm_overlap,
            dhm_full_record_retained=dhm_full_record,
            pyramid_retained=pyramid,
            rng=random.Random(7),
            params=DEFAULT_PARAMS,
        )

        assert result.threshold_ladder_peaks[0.0] == 14
        assert result.threshold_ladder_peaks[0.2] == 14
        assert result.pyramid_peak_hour == 14
        assert result.bootstrap.n_season_years == 5
        assert result.bootstrap.adequate_sample is True
        assert result.disjoint_period_data_sufficient is True
        assert result.pairing.n_common_retained == result.pairing.n_pyramid_retained
        assert result.station_verdict.verdict == Verdict.H1_REFUTED


class TestAdjudicateStationAppliesUtcToNptConversion:
    """Locks the D2 blocker: without the UTC->NPT conversion, DHM's UTC
    hour-of-day is compared directly against Pyramid's NPT hour-of-day as
    if they were the same clock."""

    def test_naive_unconverted_comparison_would_wrongly_refute_h1(self) -> None:
        """DHM peaks at UTC hour 2 (-> NPT ~8 once correctly converted);
        Pyramid peaks at NPT hour 23 — a real ~9h circular disagreement
        once both are on the same clock, mirroring the plan's own worked
        Lukla example (UTC 02 vs NPT minimum). Naive raw-hour comparison
        sees `circ_dist(2, 23) == 3h`, UNDER the 4h gate-1 threshold, so a
        buggy implementation proceeds to the ablation gate (0h movement)
        and wrongly REFUTES H1. The correct conversion must stop at gate 1
        (matched-resolution disagreement) instead."""
        years = [2020, 2021, 2022, 2023, 2024]
        days = range(1, 21)
        dhm_timestamps = _july_timestamps(years, days)
        pyramid_timestamps = [
            ts + timedelta(hours=_HOUR_OFFSET) for ts in dhm_timestamps
        ]

        def dhm_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 2 else 0.0

        def pyramid_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 23 else 0.0

        dhm_overlap = _frame(
            station=_DHM_STATION, timestamps=dhm_timestamps, value_at=dhm_value
        )
        pyramid = _frame(
            station=_PYRAMID_STATION,
            timestamps=pyramid_timestamps,
            value_at=pyramid_value,
        )

        result = adjudicate_station(
            dhm_station=_DHM_STATION,
            dhm_overlap_retained=dhm_overlap,
            dhm_full_record_retained=dhm_overlap,
            pyramid_retained=pyramid,
            rng=random.Random(3),
            params=DEFAULT_PARAMS,
        )

        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.MATCHED_RESOLUTION_DISAGREEMENT
        )
        assert result.station_verdict.gate_stopped_at == "matched_resolution"


class TestAdjudicateStationPairsBeforeComputingMatchedResolutionPeaks:
    """Locks the D3/D9 major finding: the ladder and Pyramid peaks must be
    computed from the SAME common-retained (paired) population, never
    independently from each side's own retained population."""

    def test_dhm_only_outlier_days_absent_from_pyramid_do_not_move_the_verdict(
        self,
    ) -> None:
        """DHM has 3 days/year genuinely common with Pyramid (real signal
        at UTC hour 8 -> NPT 14, matching Pyramid) PLUS 2 extra days/year
        that exist ONLY in DHM's own retained population, with a huge
        outlier at hour 3. Computed on DHM's OWN (unpaired) retained
        population, hour 3's mean (boosted by the outlier) swamps hour
        14's — the WRONG peak. Computed on the paired population (the
        fix), the outlier days never enter at all, and the true shared
        hour-14 peak survives."""
        years = [2020, 2021, 2022, 2023, 2024]
        common_days = range(1, 4)  # 3 common days/year
        outlier_days = range(4, 6)  # 2 DHM-only days/year

        dhm_common_ts = _july_timestamps(years, common_days)
        dhm_outlier_ts = _july_timestamps(years, outlier_days)
        dhm_timestamps = dhm_common_ts + dhm_outlier_ts
        pyramid_timestamps = [
            ts + timedelta(hours=_HOUR_OFFSET) for ts in dhm_common_ts
        ]

        outlier_ts_set = set(dhm_outlier_ts)

        def dhm_value(ts: datetime) -> float:
            if ts in outlier_ts_set:
                return 1000.0 if ts.hour == 3 else 0.0
            return 5.0 if ts.hour == 8 else 0.0

        def pyramid_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 14 else 0.0

        dhm_overlap = _frame(
            station=_DHM_STATION, timestamps=dhm_timestamps, value_at=dhm_value
        )
        pyramid = _frame(
            station=_PYRAMID_STATION,
            timestamps=pyramid_timestamps,
            value_at=pyramid_value,
        )

        result = adjudicate_station(
            dhm_station=_DHM_STATION,
            dhm_overlap_retained=dhm_overlap,
            dhm_full_record_retained=dhm_overlap,
            pyramid_retained=pyramid,
            rng=random.Random(11),
            params=DEFAULT_PARAMS,
        )

        assert result.pairing.n_common_retained == result.pairing.n_pyramid_retained
        assert result.threshold_ladder_peaks[0.0] == 14
        assert result.threshold_ladder_peaks[0.2] == 14
        assert result.pyramid_peak_hour == 14
        assert result.station_verdict.verdict == Verdict.H1_REFUTED


class TestAdjudicateStationHandlesInsufficientDisjointData:
    """Locks the D5 blocker: the DHM source record's real span (2020-2025)
    means a station's own full record can legitimately fail to straddle
    `coloc_dhm_stationarity_split_year` — this must map to INDETERMINATE, never
    raise `NoProfileRowsError` uncaught."""

    def test_full_record_entirely_after_the_split_year_is_indeterminate_not_a_crash(
        self,
    ) -> None:
        # 5 season-years (adequacy gate 0's FIRST check passes) but every
        # one is >= the split year, so `pre` is always empty — this is the
        # synthetic stand-in for the real DHM source record (2020-2025
        # only), which can never supply a pre-split year for a station
        # whose own overlap already starts at the split.
        years = [2023, 2024, 2025, 2026, 2027]
        days = range(1, 21)
        dhm_timestamps = _july_timestamps(years, days)
        pyramid_timestamps = [
            ts + timedelta(hours=_HOUR_OFFSET) for ts in dhm_timestamps
        ]

        def dhm_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 8 else 0.0

        def pyramid_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 14 else 0.0

        dhm_overlap = _frame(
            station=_DHM_STATION, timestamps=dhm_timestamps, value_at=dhm_value
        )
        pyramid = _frame(
            station=_PYRAMID_STATION,
            timestamps=pyramid_timestamps,
            value_at=pyramid_value,
        )

        result = adjudicate_station(
            dhm_station=_DHM_STATION,
            dhm_overlap_retained=dhm_overlap,
            dhm_full_record_retained=dhm_overlap,  # no data before the split at all
            pyramid_retained=pyramid,
            rng=random.Random(5),
            params=DEFAULT_PARAMS,
        )

        assert result.disjoint_period_data_sufficient is False
        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.ADEQUACY_INSUFFICIENT_DISJOINT_DATA
        )
        assert result.station_verdict.gate_stopped_at == "adequacy"


class TestAdjudicateStationGatesOnBootstrapSpread:
    """Locks the D5 blocker: a wide bootstrap peak-hour spread must block
    the verdict rather than being computed and then ignored."""

    def test_season_years_with_wildly_different_peaks_go_indeterminate(self) -> None:
        years = [2020, 2021, 2022, 2023, 2024]
        days = range(1, 21)
        # A DIFFERENT peak hour every year in the OVERLAP window -> wide
        # circular bootstrap spread.
        peak_hour_by_year = {2020: 2, 2021: 8, 2022: 14, 2023: 20, 2024: 2}

        def dhm_value(ts: datetime) -> float:
            return 5.0 if ts.hour == peak_hour_by_year[ts.year] else 0.0

        def pyramid_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 8 else 0.0

        dhm_timestamps = _july_timestamps(years, days)
        pyramid_timestamps = [
            ts + timedelta(hours=_HOUR_OFFSET) for ts in dhm_timestamps
        ]
        dhm_overlap = _frame(
            station=_DHM_STATION, timestamps=dhm_timestamps, value_at=dhm_value
        )
        pyramid = _frame(
            station=_PYRAMID_STATION,
            timestamps=pyramid_timestamps,
            value_at=pyramid_value,
        )

        # The FULL RECORD's own stationarity check must pass on its own
        # terms, independent of the overlap window's bootstrap spread — a
        # STABLE single-peak full record isolates the bootstrap-spread gate
        # from the stationarity gate.
        def stable_dhm_value(ts: datetime) -> float:
            return 5.0 if ts.hour == 8 else 0.0

        dhm_full_record = _frame(
            station=_DHM_STATION, timestamps=dhm_timestamps, value_at=stable_dhm_value
        )

        result = adjudicate_station(
            dhm_station=_DHM_STATION,
            dhm_overlap_retained=dhm_overlap,
            dhm_full_record_retained=dhm_full_record,
            pyramid_retained=pyramid,
            rng=random.Random(13),
            params=DEFAULT_PARAMS,
        )

        assert (
            result.bootstrap.spread_hours
            > DEFAULT_PARAMS.coloc_bootstrap_adequate_max_spread_hours
        )
        assert result.station_verdict.verdict == Verdict.INDETERMINATE
        assert (
            result.station_verdict.reason
            == IndeterminateReason.ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE
        )
        assert result.station_verdict.gate_stopped_at == "adequacy"
