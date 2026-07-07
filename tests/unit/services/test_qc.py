from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import (
    ClimBaseline,
    QcRuleParams,
    QcRuleSet,
    StationQcOverride,
)
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation

_STATION = StationId(uuid4())
_PARAM = "discharge"
_STEP = timedelta(hours=1)
_T0 = ensure_utc(datetime(2025, 6, 15, 12, 0, tzinfo=UTC))


def _t(hours: int) -> UtcDatetime:
    return ensure_utc(datetime.fromtimestamp(_T0.timestamp() + hours * 3600, tz=UTC))


def _make_obs(
    value: float,
    hours: int = 0,
    *,
    station_id: StationId = _STATION,
    parameter: str = _PARAM,
) -> Observation:
    return Observation(
        id=ObservationId(uuid4()),
        station_id=station_id,
        timestamp=_t(hours),
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.RAW,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_T0,
    )


def _rule(rule_id: str, thresholds: dict[str, float]) -> QcRuleParams:
    return QcRuleParams(
        rule_id=rule_id,
        rule_version="1.0",
        parameter=_PARAM,
        time_step=_STEP,
        thresholds=thresholds,
    )


def _rule_set(*rules: QcRuleParams) -> QcRuleSet:
    return QcRuleSet(version="1.0", rules=tuple(rules))


class TestRangeCheckRealistic:
    @pytest.mark.parametrize(
        "value,expected_pass",
        [
            (15.0, True),  # normal flow for small Alpine river
            (0.05, True),  # very low flow (valid)
            (-0.1, False),  # negative (instrument error)
            (5000.0, False),  # extreme outlier for small basin
        ],
    )
    def test_range_check_realistic(self, value: float, expected_pass: bool) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(value)
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 3000.0}))
        result = checker.check([obs], rs, [], [])
        flags = result[obs.id]
        if expected_pass:
            assert flags == []
        else:
            assert len(flags) == 1
            assert flags[0].status == QcStatus.QC_FAILED
            assert flags[0].rule_id == "range_check"


class TestRangeCheck:
    def test_value_in_range_passes(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(50.0)
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 100.0}))
        result = checker.check([obs], rs, [], [])
        assert result[obs.id] == []

    def test_value_below_min_fails(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(-1.0)
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 100.0}))
        result = checker.check([obs], rs, [], [])
        flags = result[obs.id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_FAILED
        assert flags[0].rule_id == "range_check"

    def test_value_above_max_fails(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(200.0)
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 100.0}))
        result = checker.check([obs], rs, [], [])
        flags = result[obs.id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_FAILED


class TestRateOfChange:
    def test_normal_rate_passes(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(10.0, 0), _make_obs(11.0, 1)]
        rs = _rule_set(_rule("rate_of_change", {"max_rate": 5.0}))
        result = checker.check(obs, rs, [], [])
        assert result[obs[1].id] == []

    def test_excessive_rate_suspect(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(10.0, 0), _make_obs(100.0, 1)]
        rs = _rule_set(_rule("rate_of_change", {"max_rate": 5.0}))
        result = checker.check(obs, rs, [], [])
        flags = result[obs[1].id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_SUSPECT
        assert flags[0].rule_id == "rate_of_change"

    def test_first_observation_skipped(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(10.0, 0), _make_obs(100.0, 1)]
        rs = _rule_set(_rule("rate_of_change", {"max_rate": 5.0}))
        result = checker.check(obs, rs, [], [])
        # First obs has no previous — must have no flag from rate_of_change
        assert result[obs[0].id] == []


class TestFrozenSensor:
    def test_varying_values_pass(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(float(v), i) for i, v in enumerate([10, 20, 30, 40, 50])]
        rs = _rule_set(_rule("frozen_sensor", {"tolerance": 0.1, "min_consecutive": 3}))
        result = checker.check(obs, rs, [], [])
        assert all(result[o.id] == [] for o in obs)

    def test_frozen_values_suspect(self) -> None:
        checker = Stage1QualityChecker()
        # 4 observations all at 10.0 — within tolerance 0.01, min_consecutive=3
        obs = [_make_obs(10.0, i) for i in range(4)]
        rs = _rule_set(
            _rule("frozen_sensor", {"tolerance": 0.01, "min_consecutive": 3})
        )
        result = checker.check(obs, rs, [], [])
        flagged = [o for o in obs if result[o.id]]
        assert len(flagged) >= 3
        for o in flagged:
            assert result[o.id][0].status == QcStatus.QC_SUSPECT
            assert result[o.id][0].rule_id == "frozen_sensor"

    def test_just_below_threshold_passes(self) -> None:
        checker = Stage1QualityChecker()
        # Only 2 identical values, min_consecutive=3 — not enough to flag
        obs = [_make_obs(10.0, 0), _make_obs(10.0, 1), _make_obs(20.0, 2)]
        rs = _rule_set(
            _rule("frozen_sensor", {"tolerance": 0.01, "min_consecutive": 3})
        )
        result = checker.check(obs, rs, [], [])
        assert all(result[o.id] == [] for o in obs)


class TestSpike:
    def test_absolute_max_delta_detects_spike(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(10.0, 0), _make_obs(12.1, 1), _make_obs(10.2, 2)]
        rs = _rule_set(_rule("spike", {"max_delta": 1.0}))

        result = checker.check(obs, rs, [], [])

        flags = result[obs[1].id]
        assert len(flags) == 1
        assert flags[0].rule_id == "spike"
        assert flags[0].status == QcStatus.QC_SUSPECT

    def test_absolute_max_delta_allows_small_delta(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(10.0, 0), _make_obs(10.8, 1), _make_obs(10.1, 2)]
        rs = _rule_set(_rule("spike", {"max_delta": 1.0}))

        result = checker.check(obs, rs, [], [])

        assert result[obs[1].id] == []

    def test_normal_variation_passes(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(10.0, 0), _make_obs(11.0, 1), _make_obs(10.5, 2)]
        rs = _rule_set(_rule("spike", {"tolerance": 0.5}))
        result = checker.check(obs, rs, [], [])
        assert all(result[o.id] == [] for o in obs)

    def test_spike_detected(self) -> None:
        checker = Stage1QualityChecker()
        # prev=10, current=100, next=10.5 — huge deviation from both neighbors
        obs = [_make_obs(10.0, 0), _make_obs(100.0, 1), _make_obs(10.5, 2)]
        rs = _rule_set(_rule("spike", {"tolerance": 0.5}))
        result = checker.check(obs, rs, [], [])
        flags = result[obs[1].id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_SUSPECT
        assert flags[0].rule_id == "spike"

    def test_no_spike_at_edges(self) -> None:
        checker = Stage1QualityChecker()
        obs = [_make_obs(100.0, 0), _make_obs(10.0, 1), _make_obs(100.0, 2)]
        rs = _rule_set(_rule("spike", {"tolerance": 0.5}))
        result = checker.check(obs, rs, [], [])
        # First and last obs have no prev/next — cannot be flagged as spikes
        assert result[obs[0].id] == []
        assert result[obs[2].id] == []


class TestGrossOutlier:
    def _baseline(self, mean: float, std: float) -> ClimBaseline:
        doy = _T0.timetuple().tm_yday
        return ClimBaseline(
            station_id=_STATION,
            parameter=_PARAM,
            day_of_year=doy,
            rolling_mean=mean,
            rolling_std=std,
            sample_count=30,
        )

    def test_normal_value_passes(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(12.0)
        rs = _rule_set(_rule("gross_outlier", {"k_sigma": 3.0}))
        baseline = self._baseline(mean=10.0, std=2.0)
        result = checker.check([obs], rs, [], [baseline])
        assert result[obs.id] == []

    def test_outlier_detected(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(100.0)
        rs = _rule_set(_rule("gross_outlier", {"k_sigma": 3.0}))
        baseline = self._baseline(mean=10.0, std=2.0)
        result = checker.check([obs], rs, [], [baseline])
        flags = result[obs.id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_SUSPECT
        assert flags[0].rule_id == "gross_outlier"

    def test_missing_baseline_skips(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(999.0)
        rs = _rule_set(_rule("gross_outlier", {"k_sigma": 3.0}))
        # No baseline provided — must not flag, must not raise
        result = checker.check([obs], rs, [], [])
        assert result[obs.id] == []


class TestOverrideMerging:
    def test_override_replaces_threshold(self) -> None:
        checker = Stage1QualityChecker()
        # Default rule allows up to 100; override tightens to 50
        obs = _make_obs(75.0)
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 100.0}))
        override = StationQcOverride(
            station_id=_STATION,
            rule_id="range_check",
            parameter=_PARAM,
            time_step=_STEP,
            thresholds={"value_max": 50.0, "value_min": None},
        )
        result = checker.check([obs], rs, [override], [])
        flags = result[obs.id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_FAILED

    def test_override_none_inherits_default(self) -> None:
        checker = Stage1QualityChecker()
        # None in override for value_min keeps the default 0.0
        obs = _make_obs(-5.0)
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 100.0}))
        override = StationQcOverride(
            station_id=_STATION,
            rule_id="range_check",
            parameter=_PARAM,
            time_step=_STEP,
            thresholds={"value_max": 200.0, "value_min": None},
        )
        result = checker.check([obs], rs, [override], [])
        flags = result[obs.id]
        # value_min stays 0.0 (None → inherit), so -5.0 still fails
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_FAILED


class TestWaterLevelQc:
    def test_water_level_range_check(self) -> None:
        checker = Stage1QualityChecker()
        obs = Observation(
            id=ObservationId(uuid4()),
            station_id=_STATION,
            timestamp=_t(0),
            parameter="water_level",
            value=9999.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.RAW,
            qc_flags=[],
            qc_rule_version=None,
            created_at=_T0,
        )
        rule = QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(hours=1),
            thresholds={"value_min": 0.0, "value_max": 100.0},
        )
        rs = QcRuleSet(version="1.0", rules=(rule,))
        result = checker.check([obs], rs, [], [])
        flags = result[obs.id]
        assert len(flags) == 1
        assert flags[0].status == QcStatus.QC_FAILED
        assert flags[0].rule_id == "range_check"

    def test_water_level_no_daily_rules_returns_empty_flags(self) -> None:
        # 10-min rule; obs inferred as hourly → no rule match → empty flags
        checker = Stage1QualityChecker()
        obs = Observation(
            id=ObservationId(uuid4()),
            station_id=_STATION,
            timestamp=_t(0),
            parameter="water_level",
            value=999.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.RAW,
            qc_flags=[],
            qc_rule_version=None,
            created_at=_T0,
        )
        rule = QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(minutes=10),
            thresholds={"value_min": 0.0, "value_max": 100.0},
        )
        rs = QcRuleSet(version="1.0", rules=(rule,))
        result = checker.check([obs], rs, [], [])
        # No rules match the daily (1-hour step) obs — all pass
        assert result[obs.id] == []


class TestIntegration:
    def test_multiple_rules_multiple_flags(self) -> None:
        checker = Stage1QualityChecker()
        # obs[0] is fine; obs[1] is out of range AND has excessive rate of change
        obs = [_make_obs(10.0, 0), _make_obs(500.0, 1)]
        rs = _rule_set(
            _rule("range_check", {"value_min": 0.0, "value_max": 100.0}),
            _rule("rate_of_change", {"max_rate": 5.0}),
        )
        result = checker.check(obs, rs, [], [])
        flags = result[obs[1].id]
        rule_ids = {f.rule_id for f in flags}
        assert "range_check" in rule_ids
        assert "rate_of_change" in rule_ids

    def test_empty_observations_returns_empty(self) -> None:
        checker = Stage1QualityChecker()
        rs = _rule_set(_rule("range_check", {"value_min": 0.0, "value_max": 100.0}))
        result = checker.check([], rs, [], [])
        assert result == {}

    def test_no_rules_for_parameter(self) -> None:
        checker = Stage1QualityChecker()
        obs = _make_obs(999.0)
        # Rule targets "temperature", not "discharge"
        rule = QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="temperature",
            time_step=_STEP,
            thresholds={"value_min": -50.0, "value_max": 60.0},
        )
        rs = QcRuleSet(version="1.0", rules=(rule,))
        result = checker.check([obs], rs, [], [])
        assert result[obs.id] == []
