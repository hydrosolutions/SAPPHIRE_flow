from uuid import UUID

import pytest
from pydantic import ValidationError

from sapphire_flow.config.evaluation_forecasts import EvaluationForecastsConfig
from tests.conftest import make_deployment_config


class TestEvaluationForecastsConfig:
    def test_default_off_and_read_enablement_does_not_schedule(self) -> None:
        assert make_deployment_config().evaluation_forecasts is None
        read = EvaluationForecastsConfig(
            tenant_id=UUID(int=1), deployment="nepal21", group_ids=(UUID(int=2),)
        )
        config = make_deployment_config(evaluation_forecasts=read)
        assert config.daily_evaluation is None
        assert config.evaluation_forecasts.to_domain().deployment == "nepal21"

    @pytest.mark.parametrize(
        "patch",
        [
            {"group_ids": ()},
            {"deployment": ""},
            {"data_use": "expired_rating_test"},
            {"plausibility_models": ("other",)},
        ],
    )
    def test_invalid_scope_or_policy(self, patch: dict) -> None:
        args = dict(tenant_id=UUID(int=1), deployment="bafu", group_ids=(UUID(int=2),))
        args.update(patch)
        with pytest.raises(ValidationError):
            EvaluationForecastsConfig(**args)
