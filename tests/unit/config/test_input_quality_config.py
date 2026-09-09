from __future__ import annotations

import pytest

from sapphire_flow.config.deployment import InputQualityConfig
from tests.conftest import make_deployment_config


class TestInputQualityConfigDefaults:
    def test_default_config_constructs_without_error(self) -> None:
        config = make_deployment_config()
        assert config.input_quality.obs_degraded_hours == 12.0
        assert config.input_quality.nwp_age_partial_hours == 9.0
        assert config.input_quality.nwp_age_degraded_hours == 11.0
        assert config.input_quality.warmup_snapshot_age_partial_hours == 24.0
        assert config.input_quality.warmup_snapshot_age_degraded_hours == 42.0


class TestInputQualityOrdering:
    def test_nwp_partial_ge_degraded_raises(self) -> None:
        with pytest.raises(
            ValueError, match="nwp_age_partial_hours must be < nwp_age_degraded_hours"
        ):
            make_deployment_config(
                input_quality=InputQualityConfig(
                    nwp_age_partial_hours=11.0,
                    nwp_age_degraded_hours=9.0,
                )
            )

    def test_nwp_partial_equal_degraded_raises(self) -> None:
        with pytest.raises(
            ValueError, match="nwp_age_partial_hours must be < nwp_age_degraded_hours"
        ):
            make_deployment_config(
                input_quality=InputQualityConfig(
                    nwp_age_partial_hours=10.0,
                    nwp_age_degraded_hours=10.0,
                )
            )

    def test_warmup_partial_ge_degraded_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match="warmup_snapshot_age_partial_hours must be",
        ):
            make_deployment_config(
                input_quality=InputQualityConfig(
                    warmup_snapshot_age_partial_hours=42.0,
                    warmup_snapshot_age_degraded_hours=24.0,
                )
            )

    def test_warmup_partial_equal_degraded_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match="warmup_snapshot_age_partial_hours must be",
        ):
            make_deployment_config(
                input_quality=InputQualityConfig(
                    warmup_snapshot_age_partial_hours=30.0,
                    warmup_snapshot_age_degraded_hours=30.0,
                )
            )


class TestInputQualityCrossConfigValidation:
    def test_nwp_age_degraded_exceeds_fallback_gate_raises(self) -> None:
        with pytest.raises(ValueError, match="nwp_age_degraded_hours"):
            make_deployment_config(
                nwp_max_fallback_age_hours=10.0,
                input_quality=InputQualityConfig(
                    nwp_age_partial_hours=8.0,
                    nwp_age_degraded_hours=11.0,
                ),
            )

    def test_nwp_age_partial_exceeds_fallback_gate_raises(self) -> None:
        with pytest.raises(ValueError, match="nwp_age_partial_hours"):
            make_deployment_config(
                nwp_max_fallback_age_hours=10.0,
                input_quality=InputQualityConfig(
                    nwp_age_partial_hours=11.0,
                    nwp_age_degraded_hours=10.0,
                ),
            )

    def test_warmup_degraded_exceeds_snapshot_gate_raises(self) -> None:
        with pytest.raises(ValueError, match="warmup_snapshot_age_degraded_hours"):
            make_deployment_config(
                warm_up_snapshot_max_age_hours=40.0,
                input_quality=InputQualityConfig(
                    warmup_snapshot_age_partial_hours=24.0,
                    warmup_snapshot_age_degraded_hours=42.0,
                ),
            )

    def test_warmup_partial_exceeds_snapshot_gate_raises(self) -> None:
        with pytest.raises(ValueError, match="warmup_snapshot_age_partial_hours"):
            make_deployment_config(
                warm_up_snapshot_max_age_hours=30.0,
                input_quality=InputQualityConfig(
                    warmup_snapshot_age_partial_hours=31.0,
                    warmup_snapshot_age_degraded_hours=29.0,
                ),
            )

    def test_obs_degraded_less_than_warning_raises(self) -> None:
        with pytest.raises(ValueError, match="obs_degraded_hours"):
            make_deployment_config(
                observation_staleness_warning_hours=6.0,
                input_quality=InputQualityConfig(obs_degraded_hours=4.0),
            )

    def test_obs_degraded_equal_to_warning_raises(self) -> None:
        with pytest.raises(ValueError, match="obs_degraded_hours"):
            make_deployment_config(
                observation_staleness_warning_hours=6.0,
                input_quality=InputQualityConfig(obs_degraded_hours=6.0),
            )


class TestForcingRecentStepsBounds:
    """Independent review 2026-09-09 (minor): `forcing_recent_steps` was an
    unbounded `int`. A negative value put the recent-window cutoff in the
    FUTURE, so every gap — including one in the most recent bucket — was
    labelled PARTIAL, silently inverting the rule the setting expresses.
    """

    def test_negative_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="forcing_recent_steps"):
            InputQualityConfig(forcing_recent_steps=-1)

    def test_zero_is_accepted_as_an_empty_recent_window(self) -> None:
        # Legitimate: "no recent window", so every gap is old and PARTIAL.
        assert InputQualityConfig(forcing_recent_steps=0).forcing_recent_steps == 0

    def test_the_default_is_two(self) -> None:
        assert InputQualityConfig().forcing_recent_steps == 2
