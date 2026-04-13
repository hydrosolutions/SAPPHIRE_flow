from __future__ import annotations

import pytest

from sapphire_flow.config.deployment import InputQualityConfig
from sapphire_flow.services.input_quality import assess_input_quality
from sapphire_flow.types.enums import (
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
    WarmUpSource,
)


def _default_kwargs() -> dict:
    return dict(
        observation_staleness_hours=0.0,
        warm_up_source=WarmUpSource.FRESH,
        warm_up_state_age_hours=None,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        nwp_age_hours=0.0,
        obs_partial_hours=6.0,
        config=InputQualityConfig(),
        warmup_partial_hours=24.0,
        warmup_degraded_hours=42.0,
    )


class TestCallSiteValidation:
    def test_obs_partial_equal_to_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["obs_partial_hours"] = kwargs["config"].obs_degraded_hours
        with pytest.raises(ValueError, match="obs_partial_hours"):
            assess_input_quality(**kwargs)

    def test_obs_partial_above_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["obs_partial_hours"] = kwargs["config"].obs_degraded_hours + 1.0
        with pytest.raises(ValueError, match="obs_partial_hours"):
            assess_input_quality(**kwargs)

    def test_warmup_partial_equal_to_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warmup_partial_hours"] = kwargs["warmup_degraded_hours"]
        with pytest.raises(ValueError, match="warmup_partial_hours"):
            assess_input_quality(**kwargs)

    def test_warmup_partial_above_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warmup_partial_hours"] = kwargs["warmup_degraded_hours"] + 1.0
        with pytest.raises(ValueError, match="warmup_partial_hours"):
            assess_input_quality(**kwargs)


class TestObservationAssessment:
    def test_none_staleness_no_obs_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = None
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.FULL
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert obs_flags == []

    def test_below_partial_threshold_no_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 5.9
        level, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert obs_flags == []

    def test_at_partial_threshold_partial_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 6.0  # == obs_partial_hours
        level, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert len(obs_flags) == 1
        assert obs_flags[0].level == InputQualityLevel.PARTIAL

    def test_at_degraded_threshold_degraded_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 12.0  # == obs_degraded_hours
        level, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert len(obs_flags) == 1
        assert obs_flags[0].level == InputQualityLevel.DEGRADED

    def test_detail_contains_staleness_value(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 8.5
        _, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert "8.5h" in obs_flags[0].detail


class TestNwpAssessment:
    def test_below_partial_threshold_no_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 8.9
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert nwp_flags == []

    def test_at_partial_threshold_partial_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0  # == nwp_age_partial_hours
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert len(nwp_flags) == 1
        assert nwp_flags[0].level == InputQualityLevel.PARTIAL

    def test_at_degraded_threshold_degraded_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 11.0  # == nwp_age_degraded_hours
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert len(nwp_flags) == 1
        assert nwp_flags[0].level == InputQualityLevel.DEGRADED

    def test_fallback_source_in_detail(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0
        kwargs["nwp_cycle_source"] = NwpCycleSource.FALLBACK
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "fallback" in nwp_flags[0].detail

    def test_primary_source_not_in_detail(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0
        kwargs["nwp_cycle_source"] = NwpCycleSource.PRIMARY
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "fallback" not in nwp_flags[0].detail

    def test_detail_contains_age_and_threshold(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 10.3
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "10.3h" in nwp_flags[0].detail
        assert "9.0h" in nwp_flags[0].detail  # partial threshold


class TestWarmUpAssessment:
    def test_none_source_no_warmup_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = None
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags == []

    def test_fresh_source_no_warmup_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.FRESH
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags == []

    def test_cold_start_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.COLD_START
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.DEGRADED

    def test_cold_start_degraded_regardless_of_age(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.COLD_START
        kwargs["warm_up_state_age_hours"] = 1.0
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags[0].level == InputQualityLevel.DEGRADED

    def test_snapshot_none_age_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = None
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.DEGRADED
        assert "age unknown" in wu_flags[0].detail

    def test_snapshot_below_partial_no_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 23.9
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags == []

    def test_snapshot_at_partial_threshold_partial(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 24.0  # == warmup_partial_hours
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.PARTIAL

    def test_snapshot_at_degraded_threshold_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 42.0  # == warmup_degraded_hours
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.DEGRADED


class TestWorstWinsAggregation:
    def test_all_full_no_flags(self) -> None:
        kwargs = _default_kwargs()
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.FULL
        assert flags == ()

    def test_one_partial_others_full_returns_partial(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0  # partial
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.PARTIAL
        assert len(flags) == 1

    def test_one_degraded_others_partial_returns_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 11.0  # degraded
        kwargs["observation_staleness_hours"] = 6.0  # partial
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 24.0  # partial
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.DEGRADED

    def test_all_three_degraded_three_flags(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 12.0  # degraded
        kwargs["nwp_age_hours"] = 11.0  # degraded
        kwargs["warm_up_source"] = WarmUpSource.COLD_START  # degraded
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.DEGRADED
        assert len(flags) == 3
