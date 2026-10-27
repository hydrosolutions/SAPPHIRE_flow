from uuid import uuid4

import pytest
from pydantic import ValidationError

from sapphire_flow.config.daily_evaluation import DailyEvaluationConfig
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.types.ids import ModelId


def configuration() -> dict[str, object]:
    return {
        "tenant_id": str(uuid4()),
        "deployment": "bafu-staging",
        "prefect_deployment_id": str(uuid4()),
        "group_ids": [str(uuid4())],
        "output_contracts": [
            {
                "parameter": "discharge",
                "units": "m³/s",
                "representation": "quantiles",
                "first_lead_hours": 24,
                "horizon_steps": 10,
                "series_keys": [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99],
            }
        ],
    }


class TestDailyEvaluationConfig:
    def test_default_is_disabled(self) -> None:
        assert DeploymentConfig(max_retention_days=600).daily_evaluation is None

    def test_explicit_configuration_converts_to_domain(self) -> None:
        parsed = DailyEvaluationConfig.model_validate(configuration())
        domain = parsed.to_domain()
        assert domain.model_id == ModelId("cmal_small")
        assert domain.output_contracts[0].horizon_steps == 10
        assert len(domain.group_ids) == 1

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("deployment", " "),
            ("group_ids", []),
            ("output_contracts", []),
            ("model_id", "regression"),
            ("data_use", "expired_rating_test"),
            ("prefect_deployment_id", "not-a-uuid"),
        ],
    )
    def test_rejects_invalid_scope(self, field: str, value: object) -> None:
        data = configuration()
        data[field] = value
        with pytest.raises(ValidationError, match=".+"):
            DailyEvaluationConfig.model_validate(data)

    def test_rejects_subdaily_contract(self) -> None:
        data = configuration()
        data["output_contracts"] = [
            {
                "parameter": "discharge",
                "units": "m³/s",
                "representation": "members",
                "time_step_hours": 6,
                "first_lead_hours": 6,
                "horizon_steps": 40,
                "series_keys": [0, 1],
            }
        ]
        with pytest.raises(ValidationError, match="time_step_hours"):
            DailyEvaluationConfig.model_validate(data)

    def test_rejects_duplicate_groups(self) -> None:
        data = configuration()
        group = str(uuid4())
        data["group_ids"] = [group, group]
        with pytest.raises(ValidationError, match="unique"):
            DailyEvaluationConfig.model_validate(data)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("series_keys", [0.1, 0.5, 0.9]),
            ("horizon_steps", 0),
            ("first_lead_hours", 1),
            ("units", ""),
        ],
    )
    def test_rejects_invalid_output_contract(self, field: str, value: object) -> None:
        data = configuration()
        contract = {
            "parameter": "discharge",
            "units": "m³/s",
            "representation": "quantiles",
            "first_lead_hours": 24,
            "horizon_steps": 10,
            "series_keys": [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99],
        }
        contract[field] = value
        data["output_contracts"] = [contract]
        with pytest.raises(ValidationError, match=".+"):
            DailyEvaluationConfig.model_validate(data)

    def test_deployment_parses_nested_configuration(self) -> None:
        data = configuration()
        parsed = DeploymentConfig.model_validate(
            {"max_retention_days": 600, "daily_evaluation": data}
        )
        assert parsed.daily_evaluation is not None
        assert str(parsed.daily_evaluation.to_domain().tenant_id) == data["tenant_id"]
