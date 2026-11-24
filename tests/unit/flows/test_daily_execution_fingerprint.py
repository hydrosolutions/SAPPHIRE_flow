from uuid import uuid4

import pytest

from sapphire_flow.config.evaluation_forecasts import EvaluationForecastsConfig
from sapphire_flow.flows.daily_forecast_execution import execution_configuration_sha256
from tests.unit.flows.test_run_forecast_cycle import _make_config


class TestExecutionFingerprint:
    def test_read_settings_do_not_change_execution_identity(self) -> None:
        config = _make_config()
        reads = EvaluationForecastsConfig(
            tenant_id=uuid4(),
            deployment="test",
            group_ids=(uuid4(),),
        )
        changed = config.model_copy(update={"evaluation_forecasts": reads})
        assert execution_configuration_sha256(config) == execution_configuration_sha256(
            changed
        )

    def test_scientific_input_quality_change_changes_identity(self) -> None:
        config = _make_config()
        changed = config.model_copy(
            update={
                "input_quality": config.input_quality.model_copy(
                    update={
                        "forcing_recent_steps": (
                            config.input_quality.forcing_recent_steps + 1
                        )
                    }
                )
            }
        )
        assert execution_configuration_sha256(config) != execution_configuration_sha256(
            changed
        )

    def test_group_addition_and_registration_do_not_change_identity(self) -> None:
        from sapphire_flow.config.daily_evaluation import (
            DailyEvaluationConfig,
            DailyOutputContractConfig,
        )
        from sapphire_flow.types.enums import EnsembleRepresentation

        daily = DailyEvaluationConfig(
            tenant_id=uuid4(),
            deployment="test",
            prefect_deployment_id=uuid4(),
            group_ids=(uuid4(),),
            output_contracts=(
                DailyOutputContractConfig(
                    parameter="discharge",
                    units="m³/s",
                    representation=EnsembleRepresentation.MEMBERS,
                    first_lead_hours=24,
                    horizon_steps=2,
                    series_keys=tuple(float(i) for i in range(21)),
                ),
            ),
        )
        config = _make_config().model_copy(update={"daily_evaluation": daily})
        changed = config.model_copy(
            update={
                "daily_evaluation": daily.model_copy(
                    update={
                        "group_ids": (*daily.group_ids, uuid4()),
                        "prefect_deployment_id": uuid4(),
                    }
                )
            }
        )
        assert execution_configuration_sha256(config) == execution_configuration_sha256(
            changed
        )

    def test_selected_default_preserves_reference_execution_hash(self) -> None:
        from sapphire_flow.config.deployment import DeploymentConfig
        from sapphire_flow.config.operational_weather import (
            OperationalPastWeatherRoute,
            OperationalWeatherConfig,
        )

        config = DeploymentConfig(
            max_retention_days=600,
            operational_weather=OperationalWeatherConfig(
                route=OperationalPastWeatherRoute.RECAP_COMBINED
            ),
        )
        # Retained weather policy is included; the unset calendar key is omitted.
        assert execution_configuration_sha256(config) == (
            "be4943a8e9a7c82264c77bf0d67ad3a6c9ff8139131aaabb2978b2de69197330"
        )
        configured = config.model_copy(
            update={
                "operational_weather": OperationalWeatherConfig.model_validate(
                    {
                        "route": "recap_combined",
                        "automatic_issue_timezone": "Asia/Kathmandu",
                    }
                )
            }
        )
        assert execution_configuration_sha256(
            configured
        ) != execution_configuration_sha256(config)
        other_zone = configured.model_copy(
            update={
                "operational_weather": OperationalWeatherConfig.model_validate(
                    {"route": "recap_combined", "automatic_issue_timezone": "UTC"}
                )
            }
        )
        assert execution_configuration_sha256(
            other_zone
        ) != execution_configuration_sha256(configured)

    @pytest.mark.parametrize(
        "policy", [{"rollover_buffer_days": 1}, {"max_retained_past_lead_hours": 48}]
    )
    def test_retained_weather_policy_changes_execution_identity(
        self, policy: dict[str, int]
    ) -> None:
        from sapphire_flow.config.operational_weather import OperationalWeatherConfig

        config = _make_config().model_copy(
            update={
                "operational_weather": OperationalWeatherConfig.model_validate(
                    {"route": "recap_combined"}
                )
            }
        )
        changed = config.model_copy(
            update={
                "operational_weather": OperationalWeatherConfig.model_validate(
                    {"route": "recap_combined", **policy}
                )
            }
        )
        assert execution_configuration_sha256(config) != execution_configuration_sha256(
            changed
        )

    def test_inactive_route_calendar_does_not_change_execution_hash(self) -> None:
        from sapphire_flow.config.operational_weather import OperationalWeatherConfig

        config = _make_config()
        changed = config.model_copy(
            update={
                "operational_weather": OperationalWeatherConfig.model_validate(
                    {"automatic_issue_timezone": "Asia/Kathmandu"}
                )
            }
        )
        assert execution_configuration_sha256(config) == execution_configuration_sha256(
            changed
        )


def test_nondefault_daily_horizon_changes_execution_identity() -> None:
    from sapphire_flow.config.operational_weather import (
        OperationalPastWeatherRoute,
        OperationalWeatherConfig,
    )

    config = _make_config().model_copy(
        update={
            "operational_weather": OperationalWeatherConfig(
                route=OperationalPastWeatherRoute.RECAP_COMBINED
            )
        }
    )
    policy = config.operational_weather.model_copy(
        update={"minimum_negotiated_horizon_days": 11}
    )
    changed = config.model_copy(update={"operational_weather": policy})
    assert policy.model_dump()["minimum_negotiated_horizon_days"] == 11
    assert execution_configuration_sha256(changed) != execution_configuration_sha256(
        config
    )
