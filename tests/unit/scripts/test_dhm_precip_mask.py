"""Task 2a/2b (Plan 173, M-A3) — the fit-for-purpose QC mask, removal
accounting and the M-A6 exclusion list.

Red-first acceptance cases for the plan's three real defect signatures:
Aiselukhark's 52-day zero run, Sindhuli Madhi's stuck-high block, and
Lukla's sentinels (`TestBuildMaskDefectSignatures`), plus D5's silent-skip
guard and D3b's season-boundary proof.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.qc_mask import (
    ExclusionReason,
    ReconciliationError,
    RemovalAccountingRow,
    RetentionCategory,
    TimeStepMismatchError,
    _raise_on_time_step_mismatch,
    build_exclusion_list,
    build_mask,
    build_removal_accounting,
    rule_provenance_rows,
)
from scripts.dhm_precip.qc_ruleset import (
    LONG_ZERO_RUN_RULE_VERSION,
    STUCK_VALUE_RULE_VERSION,
    build_precipitation_qc_rule_set,
    rule_subset,
)
from scripts.dhm_precip.seasons import Season


def _obs(
    station_id: StationId, timestamp: datetime, value: float | None
) -> Observation:
    status = QcStatus.MISSING if value is None else QcStatus.RAW
    return Observation(
        id=ObservationId(uuid.uuid4()),
        station_id=station_id,
        timestamp=timestamp,
        parameter="precipitation",
        value=value,
        source=ObservationSource.MANUAL_IMPORT,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=timestamp,
    )


def _hourly_run(
    station_id: StationId, start: datetime, value: float | None, n_hours: int
) -> list[Observation]:
    return [_obs(station_id, start + timedelta(hours=i), value) for i in range(n_hours)]


class TestBuildMaskDefectSignatures:
    def test_a_52_day_jjas_zero_run_is_masked_in_full(self) -> None:
        # Aiselukhark's real defect signature: a long zero run, well within
        # JJAS, far exceeding the 7-day (168h) removal threshold.
        station = Station("Aiselukhark")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 7, 1, tzinfo=UTC)
        n_hours = 52 * 24
        run = _hourly_run(sid, start, 0.0, n_hours)
        expected = frozenset((station, obs.timestamp) for obs in run)

        mask = build_mask({station: run}, DEFAULT_PARAMS)

        assert mask == expected

    def test_a_120_hour_stuck_high_block_is_masked_in_full(self) -> None:
        # Sindhuli Madhi's real defect signature: a pinned non-zero value,
        # above the 5.0 exclusion floor, well past minimum_run_duration_hours
        # (12h). Placed in DJF deliberately — stuck-value has no season
        # restriction (unlike long-zero-run).
        station = Station("Sindhuli Madhi")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 1, 5, tzinfo=UTC)
        run = _hourly_run(sid, start, 72.0, 120)
        expected = frozenset((station, obs.timestamp) for obs in run)

        mask = build_mask({station: run}, DEFAULT_PARAMS)

        assert mask == expected

    def test_sentinels_are_masked(self) -> None:
        # Lukla's real defect signature: the -9999999 sentinel, caught by
        # range_check's value_min=0.0 floor (D4) — a single-hour defect,
        # unlike the run-based rules above.
        station = Station("Lukla Airport")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 3, 1, tzinfo=UTC)
        background = _hourly_run(sid, start, 1.0, 10)
        sentinel_ts = start + timedelta(hours=20)
        sentinel = _obs(sid, sentinel_ts, -9999999.0)
        series = [*background, sentinel]

        mask = build_mask({station: series}, DEFAULT_PARAMS)

        assert mask == frozenset({(station, sentinel_ts)})

    def test_a_167_hour_zero_run_one_short_of_threshold_is_not_caught(self) -> None:
        station = Station("A")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 7, 1, tzinfo=UTC)
        run = _hourly_run(sid, start, 0.0, 167)

        mask = build_mask({station: run}, DEFAULT_PARAMS)

        assert mask == frozenset()

    def test_a_zero_run_interrupted_by_a_gap_yields_two_shorter_runs(self) -> None:
        # M-A2's null-fill severs runs as intended: 100h zero, a 1h gap,
        # then 100h zero. If the gap did not break the run the combined
        # 200 in-season hours would exceed 168h and be masked; split, each
        # half stays under threshold.
        station = Station("A")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 7, 1, tzinfo=UTC)
        first = _hourly_run(sid, start, 0.0, 100)
        gap = [_obs(sid, start + timedelta(hours=100), None)]
        second = _hourly_run(sid, start + timedelta(hours=101), 0.0, 100)
        series = [*first, *gap, *second]

        mask = build_mask({station: series}, DEFAULT_PARAMS)

        assert mask == frozenset()

    def test_a_run_spanning_sep_30_to_oct_1_does_not_merge_across_the_season(
        self,
    ) -> None:
        # 144 in-season (Sept) hours + 96 out-of-season (Oct) hours, all
        # exactly zero and hourly-contiguous. Combined (240h) would clear
        # the 168h threshold; the JJAS-only portion (144h) alone does not.
        # If the season filter were broken (Pass B seeing the whole span
        # unfiltered), this WOULD be flagged in full, including October
        # dates — proving the boundary holds requires checking both halves.
        station = Station("A")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 9, 25, 0, tzinfo=UTC)  # Sep 25 00:00
        series = _hourly_run(sid, start, 0.0, 240)  # through Oct 4 23:00
        assert series[-1].timestamp.month == 10

        mask = build_mask({station: series}, DEFAULT_PARAMS)

        assert mask == frozenset()

    def test_a_winter_month_of_zeros_is_untouched(self) -> None:
        # D3b's whole point: DJF is legitimately weeks of zero for a Terai
        # station. Long enough (300h) that it WOULD clear the 168h
        # threshold if Pass B wrongly ran over DJF too.
        station = Station("A")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 12, 1, tzinfo=UTC)
        run = _hourly_run(sid, start, 0.0, 300)

        mask = build_mask({station: run}, DEFAULT_PARAMS)

        assert mask == frozenset()

    def test_a_time_step_mismatch_raises_the_typed_error(self) -> None:
        station = Station("A")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 7, 1, tzinfo=UTC)
        series = [_obs(sid, start + timedelta(minutes=30 * i), 1.0) for i in range(30)]

        with pytest.raises(TimeStepMismatchError):
            build_mask({station: series}, DEFAULT_PARAMS)

    def test_a_single_mismatched_rule_among_matching_rules_still_raises(
        self,
    ) -> None:
        # D5's exact failure mode: a rule set where MOST rules match the
        # inferred step but ONE does not must still raise — not be silently
        # and partially dropped while the matching rules run unflagged.
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 7, 1, tzinfo=UTC)
        hourly_series = _hourly_run(sid, start, 1.0, 30)

        mixed_rule_set = QcRuleSet(
            version="test-mixed",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="matches-hourly",
                    parameter="precipitation",
                    time_step=timedelta(hours=1),
                    thresholds={"value_min": 0.0, "value_max": 200.0},
                ),
                QcRuleParams(
                    rule_id="frozen_sensor",
                    rule_version="mismatched-30min",
                    parameter="precipitation",
                    time_step=timedelta(minutes=30),
                    thresholds={"tolerance": 0.01, "min_consecutive": 12.0},
                ),
            ),
        )

        with pytest.raises(TimeStepMismatchError, match="mismatched-30min"):
            _raise_on_time_step_mismatch(hourly_series, mixed_rule_set)


class TestBuildMaskOverlap:
    def test_a_constant_run_above_the_floor_trips_both_instances_but_masks_once(
        self,
    ) -> None:
        station = Station("A")
        sid = StationId(uuid.uuid4())
        start = datetime(2024, 7, 1, tzinfo=UTC)
        run = _hourly_run(sid, start, 10.0, 200)  # >5.0 floor, >=168h, in JJAS

        # Prove both rule instances actually fire on this run (distinct
        # rule_version each) before checking the mask/accounting collapse it.
        rule_set = build_precipitation_qc_rule_set(DEFAULT_PARAMS)
        stuck = rule_subset(rule_set, frozenset({STUCK_VALUE_RULE_VERSION}))
        long_zero = rule_subset(rule_set, frozenset({LONG_ZERO_RUN_RULE_VERSION}))
        checker = Stage1QualityChecker()
        stuck_flags = checker.check(run, stuck, [], [])
        long_zero_flags = checker.check(run, long_zero, [], [])
        assert any(stuck_flags[o.id] for o in run)
        assert any(long_zero_flags[o.id] for o in run)

        mask = build_mask({station: run}, DEFAULT_PARAMS)
        expected = frozenset((station, obs.timestamp) for obs in run)
        assert mask == expected  # one key per timestamp, not a multiset

        rows = build_removal_accounting({station: run}, mask, DEFAULT_PARAMS)
        qc_removed_total = sum(
            r.count for r in rows if r.category == RetentionCategory.QC_REMOVED
        )
        assert qc_removed_total == 200  # each observation counted exactly once


class TestRemovalAccounting:
    def test_cross_classified_table_reconciles_to_the_axis_row_count(self) -> None:
        station = Station("X")
        sid = StationId(uuid.uuid4())
        obs = [
            _obs(sid, datetime(2024, 4, 15, 10, tzinfo=UTC), 1.0),  # MAM, hour10
            _obs(sid, datetime(2024, 4, 15, 11, tzinfo=UTC), None),  # MAM, hour11
            _obs(sid, datetime(2024, 7, 15, 10, tzinfo=UTC), 5.0),  # JJAS, hour10
            _obs(sid, datetime(2024, 7, 15, 11, tzinfo=UTC), 2.0),  # JJAS, hour11
            _obs(sid, datetime(2024, 10, 15, 10, tzinfo=UTC), 3.0),  # ON, hour10
            _obs(sid, datetime(2025, 1, 15, 10, tzinfo=UTC), 4.0),  # DJF, hour10
        ]
        mask = frozenset({(station, obs[2].timestamp)})

        rows = build_removal_accounting({station: obs}, mask, DEFAULT_PARAMS)

        # minor-5: the marginal checks below only constrain totals and one
        # cell — several wrong (season, hour, category) assignments could
        # still satisfy them. Lock the COMPLETE expected row set instead.
        expected_rows = {
            RemovalAccountingRow(
                station=station,
                season=Season.MAM,
                hour_of_day=10,
                category=RetentionCategory.RETAINED_NONMISSING,
                count=1,
            ),
            RemovalAccountingRow(
                station=station,
                season=Season.MAM,
                hour_of_day=11,
                category=RetentionCategory.SOURCE_MISSING,
                count=1,
            ),
            RemovalAccountingRow(
                station=station,
                season=Season.JJAS,
                hour_of_day=10,
                category=RetentionCategory.QC_REMOVED,
                count=1,
            ),
            RemovalAccountingRow(
                station=station,
                season=Season.JJAS,
                hour_of_day=11,
                category=RetentionCategory.RETAINED_NONMISSING,
                count=1,
            ),
            RemovalAccountingRow(
                station=station,
                season=Season.ON,
                hour_of_day=10,
                category=RetentionCategory.RETAINED_NONMISSING,
                count=1,
            ),
            RemovalAccountingRow(
                station=station,
                season=Season.DJF,
                hour_of_day=10,
                category=RetentionCategory.RETAINED_NONMISSING,
                count=1,
            ),
        }
        assert set(rows) == expected_rows
        assert len(rows) == len(expected_rows)  # no duplicate (season, hour, category)

        assert sum(r.count for r in rows) == len(obs)
        assert {r.season for r in rows} == {
            Season.MAM,
            Season.JJAS,
            Season.ON,
            Season.DJF,
        }
        hour10_total = sum(r.count for r in rows if r.hour_of_day == 10)
        hour11_total = sum(r.count for r in rows if r.hour_of_day == 11)
        assert hour10_total == 4
        assert hour11_total == 2

        jjas_masked = [
            r for r in rows if r.season == Season.JJAS and r.hour_of_day == 10
        ]
        assert len(jjas_masked) == 1
        assert jjas_masked[0].category == RetentionCategory.QC_REMOVED
        assert jjas_masked[0].count == 1

    def test_a_gap_row_counts_as_source_missing_never_retained(self) -> None:
        station = Station("A")
        sid = StationId(uuid.uuid4())
        obs = [_obs(sid, datetime(2024, 7, 1, tzinfo=UTC), None)]

        rows = build_removal_accounting({station: obs}, frozenset(), DEFAULT_PARAMS)

        assert len(rows) == 1
        assert rows[0].category == RetentionCategory.SOURCE_MISSING

    def test_reconciliation_error_is_a_value_error(self) -> None:
        # `build_removal_accounting`'s reconciliation guard is defence in
        # depth (the counts dict is derived from the same loop that
        # increments `total`, so it cannot legitimately diverge today) —
        # locked here as a typed exception a caller can catch specifically,
        # distinct from a bare ValueError.
        assert issubclass(ReconciliationError, ValueError)


class TestExclusionList:
    def _row(
        self,
        station: Station,
        category: RetentionCategory,
        count: int,
        season: Season = Season.JJAS,
    ) -> RemovalAccountingRow:
        return RemovalAccountingRow(
            station=station,
            season=season,
            hour_of_day=0,
            category=category,
            count=count,
        )

    def test_below_threshold_is_excluded(self) -> None:
        rows = (
            self._row(Station("Below"), RetentionCategory.RETAINED_NONMISSING, 49),
            self._row(Station("Below"), RetentionCategory.QC_REMOVED, 51),
        )
        entries = build_exclusion_list(rows, DEFAULT_PARAMS)
        assert len(entries) == 1
        assert entries[0].station == Station("Below")
        assert entries[0].jjas_retained_fraction == pytest.approx(0.49)
        assert entries[0].reason == ExclusionReason.JJAS_RETENTION_BELOW_MINIMUM

    def test_exactly_at_threshold_is_not_excluded(self) -> None:
        rows = (
            self._row(Station("AtBoundary"), RetentionCategory.RETAINED_NONMISSING, 50),
            self._row(Station("AtBoundary"), RetentionCategory.QC_REMOVED, 50),
        )
        entries = build_exclusion_list(rows, DEFAULT_PARAMS)
        assert entries == ()

    def test_zero_observed_jjas_hours_is_excluded_with_that_reason(self) -> None:
        rows = (
            self._row(Station("NeverObserved"), RetentionCategory.SOURCE_MISSING, 100),
        )
        entries = build_exclusion_list(rows, DEFAULT_PARAMS)
        assert len(entries) == 1
        assert entries[0].jjas_retained_fraction is None
        assert entries[0].reason == ExclusionReason.NO_OBSERVED_JJAS_HOURS

    def test_source_missing_is_excluded_from_the_denominator(self) -> None:
        # A station buried in JJAS source_missing hours but with a perfect
        # retained/removed ratio must NOT be excluded — source_missing must
        # never enter the denominator.
        rows = (
            self._row(Station("Y"), RetentionCategory.SOURCE_MISSING, 1000),
            self._row(Station("Y"), RetentionCategory.RETAINED_NONMISSING, 10),
        )
        entries = build_exclusion_list(rows, DEFAULT_PARAMS)
        assert entries == ()


class TestRuleProvenanceRows:
    def test_records_both_passes_with_distinct_versions_and_scopes(self) -> None:
        rows = rule_provenance_rows(DEFAULT_PARAMS)
        by_version = {r.rule_version: r for r in rows}
        assert by_version[STUCK_VALUE_RULE_VERSION].pass_name == "A"
        assert by_version[STUCK_VALUE_RULE_VERSION].scope == "whole_series"
        assert by_version[LONG_ZERO_RUN_RULE_VERSION].pass_name == "B"
        assert by_version[LONG_ZERO_RUN_RULE_VERSION].scope == "jjas_only"
        assert (
            by_version[LONG_ZERO_RUN_RULE_VERSION].thresholds["min_consecutive"]
            == 168.0
        )


if __name__ == "__main__":
    pytest.main([__file__])
