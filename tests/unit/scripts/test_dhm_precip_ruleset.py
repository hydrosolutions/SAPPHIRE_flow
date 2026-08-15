"""Task 1a (Plan 173, M-A3) — the in-code precipitation `QcRuleSet` (D3, D4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.qc_ruleset import (
    LONG_ZERO_RUN_RULE_VERSION,
    QC_MASK_TIME_STEP,
    STUCK_VALUE_RULE_VERSION,
    build_precipitation_qc_rule_set,
)

if TYPE_CHECKING:
    from sapphire_flow.types.domain import QcFlag

_STATION_ID = StationId(uuid.uuid4())


def _obs(timestamp: datetime, value: float | None) -> Observation:
    status = QcStatus.MISSING if value is None else QcStatus.RAW
    return Observation(
        id=ObservationId(uuid.uuid4()),
        station_id=_STATION_ID,
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


class TestBuildPrecipitationQcRuleSet:
    def test_rules_for_returns_both_frozen_sensor_instances_with_distinct_versions(
        self,
    ) -> None:
        rule_set = build_precipitation_qc_rule_set(DEFAULT_PARAMS)
        rules = rule_set.rules_for("precipitation", QC_MASK_TIME_STEP)
        frozen = [r for r in rules if r.rule_id == "frozen_sensor"]
        assert len(frozen) == 2
        versions = {r.rule_version for r in frozen}
        assert versions == {STUCK_VALUE_RULE_VERSION, LONG_ZERO_RUN_RULE_VERSION}

    def test_a_tipping_bucket_noise_run_at_least_min_consecutive_long_is_not_stuck(
        self,
    ) -> None:
        # D3: the 5.0 mm exclusion floor must do the work here — not the run
        # being too short to qualify. The run is deliberately AT LEAST
        # minimum_run_duration_hours long so a shorter-run explanation is
        # ruled out.
        start = datetime(2025, 1, 3, 0, tzinfo=UTC)
        noise = [0.2, 0.4, 0.6, 0.2, 0.4, 0.6, 0.2, 0.4, 0.6, 0.2, 0.4, 0.6]
        assert len(noise) >= DEFAULT_PARAMS.minimum_run_duration_hours
        observations = [
            _obs(start + timedelta(hours=i), v) for i, v in enumerate(noise)
        ]
        rule_set = build_precipitation_qc_rule_set(DEFAULT_PARAMS)
        flags = Stage1QualityChecker().check(observations, rule_set, [], [])
        stuck_flags: list[QcFlag] = [
            f
            for obs_flags in flags.values()
            for f in obs_flags
            if f.rule_version == STUCK_VALUE_RULE_VERSION
        ]
        assert stuck_flags == []

    def test_a_mismatched_time_step_yields_no_flags_at_the_checker_level(self) -> None:
        # Characterisation only (D5) — the enforcing raise lives in the mask
        # builder (task 2a), not here. 30-minute cadence never matches this
        # rule set's declared 3600s time_step, so `rules_for` silently
        # matches nothing.
        start = datetime(2025, 1, 3, 0, tzinfo=UTC)
        observations = [
            _obs(start + timedelta(minutes=30 * i), 72.0) for i in range(20)
        ]
        rule_set = build_precipitation_qc_rule_set(DEFAULT_PARAMS)
        flags = Stage1QualityChecker().check(observations, rule_set, [], [])
        assert all(obs_flags == [] for obs_flags in flags.values())


class TestRangeCheckCalibration:
    def test_a_legitimate_100mm_extreme_passes(self) -> None:
        start = datetime(2025, 7, 1, 0, tzinfo=UTC)
        observations = [_obs(start, 100.0)]
        rule_set = build_precipitation_qc_rule_set(DEFAULT_PARAMS)
        flags = Stage1QualityChecker().check(observations, rule_set, [], [])
        assert flags[observations[0].id] == []

    def test_200_point_1_is_masked(self) -> None:
        start = datetime(2025, 7, 1, 0, tzinfo=UTC)
        observations = [_obs(start, 200.1)]
        rule_set = build_precipitation_qc_rule_set(DEFAULT_PARAMS)
        flags = Stage1QualityChecker().check(observations, rule_set, [], [])
        assert any(f.status == QcStatus.QC_FAILED for f in flags[observations[0].id])


if __name__ == "__main__":
    pytest.main([__file__])
