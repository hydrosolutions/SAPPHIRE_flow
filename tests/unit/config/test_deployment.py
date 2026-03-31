from __future__ import annotations

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import AlertModelStrategy
from tests.conftest import make_deployment_config


class TestAlertModelStrategyConfig:
    def test_min_ensemble_size_validation(self) -> None:
        with pytest.raises(ConfigurationError, match="min_operational_ensemble_size"):
            make_deployment_config(min_operational_ensemble_size=0)

    def test_min_quantile_levels_validation(self) -> None:
        with pytest.raises(ConfigurationError, match="min_operational_quantile_levels"):
            make_deployment_config(min_operational_quantile_levels=6)

    def test_alert_model_strategy_from_string(self) -> None:
        config = make_deployment_config(alert_model_strategy="primary")
        assert config.alert_model_strategy == AlertModelStrategy.PRIMARY

    def test_enable_alert_flags_exist(self) -> None:
        config = make_deployment_config()
        assert config.enable_forecast_alerts is False
        assert config.enable_observation_alerts is False
        assert config.enable_pipeline_alerts is False
