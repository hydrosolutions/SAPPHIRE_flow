from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl

from sapphire_flow.services.forecast_qc import (
    ForecastOutputQualityChecker,
    _apply_climatology_outlier,
    _apply_ensemble_spread,
    _apply_flat_ensemble,
    _apply_negative_value,
    _apply_quantile_crossing,
    _apply_range_check,
    _apply_temporal_consistency,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import (
    ClimBaseline,
    ForecastQcRuleParams,
    ForecastQcRuleSet,
    StationForecastQcOverride,
)
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId

_STATION = StationId(uuid4())
_NOW = ensure_utc(datetime(2024, 6, 15, 12, 0, tzinfo=UTC))
_STEP = timedelta(hours=1)

_QUANTILE_LEVELS = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]


def _make_members_ensemble(
    values_per_step: list[list[float]],
    station_id: StationId = _STATION,
) -> ForecastEnsemble:
    rows = []
    for step_idx, member_vals in enumerate(values_per_step):
        vt = _NOW + _STEP * (step_idx + 1)
        for mid, val in enumerate(member_vals):
            rows.append({"valid_time": vt, "member_id": mid, "value": val})
    df = pl.DataFrame(rows).cast(
        {
            "valid_time": pl.Datetime("us", "UTC"),
            "member_id": pl.Int32,
            "value": pl.Float64,
        }
    )
    return ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=_NOW,
        parameter="discharge",
        units="m3/s",
        time_step=_STEP,
        values=df,
    )


def _make_quantiles_ensemble(
    values_per_step: list[list[float]],
    station_id: StationId = _STATION,
) -> ForecastEnsemble:
    rows = []
    for step_idx, qvals in enumerate(values_per_step):
        vt = _NOW + _STEP * (step_idx + 1)
        for q, val in zip(_QUANTILE_LEVELS, qvals, strict=True):
            rows.append({"valid_time": vt, "quantile": q, "value": val})
    df = pl.DataFrame(rows).cast(
        {
            "valid_time": pl.Datetime("us", "UTC"),
            "quantile": pl.Float64,
            "value": pl.Float64,
        }
    )
    return ForecastEnsemble.from_quantiles(
        station_id=station_id,
        issued_at=_NOW,
        parameter="discharge",
        units="m3/s",
        time_step=_STEP,
        values=df,
    )


def _make_rule(rule_id: str, thresholds: dict[str, float]) -> ForecastQcRuleParams:
    return ForecastQcRuleParams(
        rule_id=rule_id,
        rule_version="1.0.0",
        parameter="discharge",
        time_step=_STEP,
        thresholds=thresholds,
    )


def _make_ruleset(*rules: ForecastQcRuleParams) -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0.0", rules=rules)


def _make_baseline(station_id: StationId = _STATION) -> ClimBaseline:
    return ClimBaseline(
        station_id=station_id,
        parameter="discharge",
        day_of_year=167,  # June 15
        rolling_mean=50.0,
        rolling_std=20.0,
        sample_count=30,
    )


class TestNegativeValue:
    def test_passes_all_positive(self) -> None:
        ensemble = _make_members_ensemble([[10.0, 20.0, 30.0], [15.0, 25.0, 35.0]])
        rule = _make_rule("negative_value", {"value_min": 0.0})
        result = _apply_negative_value(ensemble, rule.thresholds, rule)
        assert result is None

    def test_fails_negative_discharge(self) -> None:
        ensemble = _make_members_ensemble([[10.0, -5.0, 30.0], [15.0, 25.0, 35.0]])
        rule = _make_rule("negative_value", {"value_min": 0.0})
        result = _apply_negative_value(ensemble, rule.thresholds, rule)
        assert result is not None
        assert result.rule_id == "negative_value"
        assert result.status == QcStatus.QC_FAILED


class TestRangeCheck:
    def test_passes_within_range(self) -> None:
        ensemble = _make_members_ensemble([[10.0, 20.0, 30.0], [15.0, 25.0, 35.0]])
        rule = _make_rule("range_check", {"value_min": 0.0, "value_max": 500.0})
        result = _apply_range_check(ensemble, rule.thresholds, rule)
        assert result is None

    def test_fails_median_above_max(self) -> None:
        # All members at 600 — median is 600, max is 500
        ensemble = _make_members_ensemble(
            [[600.0, 600.0, 600.0], [600.0, 600.0, 600.0]]
        )
        rule = _make_rule("range_check", {"value_min": 0.0, "value_max": 500.0})
        result = _apply_range_check(ensemble, rule.thresholds, rule)
        assert result is not None
        assert result.rule_id == "range_check"
        assert result.status == QcStatus.QC_FAILED


class TestFlatEnsemble:
    def test_passes_varied_ensemble(self) -> None:
        ensemble = _make_members_ensemble([[10.0, 50.0, 100.0], [20.0, 60.0, 110.0]])
        rule = _make_rule("flat_ensemble", {"tolerance": 0.001})
        result = _apply_flat_ensemble(ensemble, rule.thresholds, rule)
        assert result is None

    def test_flags_constant_values(self) -> None:
        # All values identical — std is 0, below tolerance
        ensemble = _make_members_ensemble([[50.0, 50.0, 50.0], [50.0, 50.0, 50.0]])
        rule = _make_rule("flat_ensemble", {"tolerance": 0.001})
        result = _apply_flat_ensemble(ensemble, rule.thresholds, rule)
        assert result is not None
        assert result.rule_id == "flat_ensemble"
        assert result.status == QcStatus.QC_SUSPECT


class TestEnsembleSpread:
    def test_passes_normal_spread(self) -> None:
        # IQR ~40, clim_std=20 → ratio=2.0, within [0.01, 10.0]
        ensemble = _make_members_ensemble(
            [[30.0, 50.0, 70.0, 80.0], [30.0, 50.0, 70.0, 80.0]]
        )
        rule = _make_rule(
            "ensemble_spread", {"min_spread_ratio": 0.01, "max_spread_ratio": 10.0}
        )
        baseline = _make_baseline()
        baseline_index = {
            (baseline.station_id, baseline.parameter, baseline.day_of_year): baseline
        }
        result = _apply_ensemble_spread(ensemble, rule.thresholds, baseline_index, rule)
        assert result is None

    def test_flags_narrow_spread(self) -> None:
        # All members nearly identical — IQR ≈ 0, ratio << min_spread_ratio
        ensemble = _make_members_ensemble(
            [[50.0, 50.001, 50.002, 50.003], [50.0, 50.001, 50.002, 50.003]]
        )
        rule = _make_rule(
            "ensemble_spread", {"min_spread_ratio": 0.5, "max_spread_ratio": 10.0}
        )
        baseline = _make_baseline()
        baseline_index = {
            (baseline.station_id, baseline.parameter, baseline.day_of_year): baseline
        }
        result = _apply_ensemble_spread(ensemble, rule.thresholds, baseline_index, rule)
        assert result is not None
        assert result.rule_id == "ensemble_spread"
        assert result.status == QcStatus.QC_SUSPECT

    def test_skips_when_no_baseline(self) -> None:
        ensemble = _make_members_ensemble([[10.0, 20.0, 30.0], [10.0, 20.0, 30.0]])
        rule = _make_rule(
            "ensemble_spread", {"min_spread_ratio": 0.01, "max_spread_ratio": 10.0}
        )
        result = _apply_ensemble_spread(ensemble, rule.thresholds, {}, rule)
        assert result is None


class TestClimatologyOutlier:
    def test_passes_within_climatology(self) -> None:
        # Median ~52 — within 6σ of mean=50, std=20
        ensemble = _make_members_ensemble(
            [[48.0, 50.0, 52.0, 54.0], [48.0, 50.0, 52.0, 54.0]]
        )
        rule = _make_rule("climatology_outlier", {"k_sigma": 6.0})
        baseline = _make_baseline()
        baseline_index = {
            (baseline.station_id, baseline.parameter, baseline.day_of_year): baseline
        }
        result = _apply_climatology_outlier(
            ensemble, rule.thresholds, baseline_index, rule
        )
        assert result is None

    def test_flags_extreme_deviation(self) -> None:
        # Median 500 — far beyond 6σ of mean=50, std=20
        ensemble = _make_members_ensemble(
            [[500.0, 500.0, 500.0, 500.0], [500.0, 500.0, 500.0, 500.0]]
        )
        rule = _make_rule("climatology_outlier", {"k_sigma": 6.0})
        baseline = _make_baseline()
        baseline_index = {
            (baseline.station_id, baseline.parameter, baseline.day_of_year): baseline
        }
        result = _apply_climatology_outlier(
            ensemble, rule.thresholds, baseline_index, rule
        )
        assert result is not None
        assert result.rule_id == "climatology_outlier"
        assert result.status == QcStatus.QC_SUSPECT

    def test_skips_when_no_baseline(self) -> None:
        ensemble = _make_members_ensemble(
            [[500.0, 500.0, 500.0], [500.0, 500.0, 500.0]]
        )
        rule = _make_rule("climatology_outlier", {"k_sigma": 6.0})
        result = _apply_climatology_outlier(ensemble, rule.thresholds, {}, rule)
        assert result is None


class TestTemporalConsistency:
    def test_passes_smooth_forecast(self) -> None:
        # Steps change by ~10 — within max_rate=500
        ensemble = _make_members_ensemble(
            [[40.0, 50.0, 60.0], [45.0, 55.0, 65.0], [50.0, 60.0, 70.0]]
        )
        rule = _make_rule("temporal_consistency", {"max_rate": 500.0})
        result = _apply_temporal_consistency(ensemble, rule.thresholds, rule)
        assert result is None

    def test_flags_large_jump(self) -> None:
        # Median jumps from ~50 to ~1000 between steps — exceeds max_rate=200
        ensemble = _make_members_ensemble([[48.0, 50.0, 52.0], [998.0, 1000.0, 1002.0]])
        rule = _make_rule("temporal_consistency", {"max_rate": 200.0})
        result = _apply_temporal_consistency(ensemble, rule.thresholds, rule)
        assert result is not None
        assert result.rule_id == "temporal_consistency"
        assert result.status == QcStatus.QC_SUSPECT


class TestQuantileCrossing:
    def test_passes_ordered_quantiles(self) -> None:
        # Strictly increasing quantile values at each step
        step_vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
        ensemble = _make_quantiles_ensemble([step_vals, step_vals])
        rule = _make_rule("quantile_crossing", {})
        result = _apply_quantile_crossing(ensemble, rule)
        assert result is None

    def test_flags_crossed_quantiles(self) -> None:
        # Q0.50 < Q0.25 at step 1 — quantile crossing
        normal = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
        # index 3 is Q0.25=40, index 4 is Q0.50 — make Q0.50 < Q0.25
        crossed = [10.0, 20.0, 30.0, 40.0, 35.0, 60.0, 70.0, 80.0, 90.0]
        ensemble = _make_quantiles_ensemble([normal, crossed])
        rule = _make_rule("quantile_crossing", {})
        result = _apply_quantile_crossing(ensemble, rule)
        assert result is not None
        assert result.rule_id == "quantile_crossing"
        assert result.status == QcStatus.QC_FAILED

    def test_skips_members_representation(self) -> None:
        ensemble = _make_members_ensemble([[10.0, 20.0, 30.0], [15.0, 25.0, 35.0]])
        rule = _make_rule("quantile_crossing", {})
        result = _apply_quantile_crossing(ensemble, rule)
        assert result is None


class TestForecastOutputQualityChecker:
    def test_multiple_rules_multiple_flags(self) -> None:
        # Ensemble with negative values AND constant spread — triggers two rules
        ensemble = _make_members_ensemble([[-5.0, -5.0, -5.0], [-5.0, -5.0, -5.0]])
        ruleset = _make_ruleset(
            _make_rule("negative_value", {"value_min": 0.0}),
            _make_rule("flat_ensemble", {"tolerance": 0.001}),
        )
        checker = ForecastOutputQualityChecker()
        flags = checker.check(ensemble, ruleset, [], [])
        rule_ids = {f.rule_id for f in flags}
        assert "negative_value" in rule_ids
        assert "flat_ensemble" in rule_ids

    def test_no_rules_returns_empty(self) -> None:
        ensemble = _make_members_ensemble([[10.0, 20.0, 30.0], [15.0, 25.0, 35.0]])
        # Ruleset targets a different parameter — no rules match discharge/1h
        rule = ForecastQcRuleParams(
            rule_id="range_check",
            rule_version="1.0.0",
            parameter="water_level",
            time_step=_STEP,
            thresholds={"value_min": 0.0, "value_max": 100.0},
        )
        ruleset = ForecastQcRuleSet(version="1.0.0", rules=(rule,))
        checker = ForecastOutputQualityChecker()
        flags = checker.check(ensemble, ruleset, [], [])
        assert flags == []


class TestForecastQcOverrideMerging:
    def test_override_tightens_threshold_causing_failure(self) -> None:
        # Default max is 500 — value of 300 passes.
        # Override lowers max to 200 — should fail.
        ensemble = _make_members_ensemble(
            [[300.0, 300.0, 300.0], [300.0, 300.0, 300.0]]
        )
        ruleset = _make_ruleset(
            _make_rule("range_check", {"value_min": 0.0, "value_max": 500.0})
        )
        override = StationForecastQcOverride(
            station_id=_STATION,
            rule_id="range_check",
            parameter="discharge",
            time_step=_STEP,
            thresholds={"value_max": 200.0},
        )
        checker = ForecastOutputQualityChecker()
        flags = checker.check(ensemble, ruleset, [override], [])
        assert len(flags) == 1
        assert flags[0].rule_id == "range_check"
        assert flags[0].status == QcStatus.QC_FAILED

    def test_override_loosens_threshold_preventing_failure(self) -> None:
        # Default max is 200 — value of 300 fails.
        # Override raises max to 500 — should pass.
        ensemble = _make_members_ensemble(
            [[300.0, 300.0, 300.0], [300.0, 300.0, 300.0]]
        )
        ruleset = _make_ruleset(
            _make_rule("range_check", {"value_min": 0.0, "value_max": 200.0})
        )
        override = StationForecastQcOverride(
            station_id=_STATION,
            rule_id="range_check",
            parameter="discharge",
            time_step=_STEP,
            thresholds={"value_max": 500.0},
        )
        checker = ForecastOutputQualityChecker()
        flags = checker.check(ensemble, ruleset, [override], [])
        assert flags == []

    def test_override_for_different_station_does_not_apply(self) -> None:
        # Override targets a different station — default threshold of 200 should apply.
        other_station = StationId(uuid4())
        ensemble = _make_members_ensemble(
            [[300.0, 300.0, 300.0], [300.0, 300.0, 300.0]]
        )
        ruleset = _make_ruleset(
            _make_rule("range_check", {"value_min": 0.0, "value_max": 200.0})
        )
        override = StationForecastQcOverride(
            station_id=other_station,
            rule_id="range_check",
            parameter="discharge",
            time_step=_STEP,
            thresholds={"value_max": 500.0},
        )
        checker = ForecastOutputQualityChecker()
        flags = checker.check(ensemble, ruleset, [override], [])
        assert len(flags) == 1
        assert flags[0].rule_id == "range_check"
        assert flags[0].status == QcStatus.QC_FAILED
