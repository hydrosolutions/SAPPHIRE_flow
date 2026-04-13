from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pydantic
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.config.deployment import _resolve_env_vars, load_config
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.domain import DangerLevelDefinition, SeasonDefinition
from sapphire_flow.types.enums import ModelCombinationStrategy, ThresholdDirection
from tests.conftest import make_deployment_config


class TestModelCombinationStrategyConfig:
    def test_min_ensemble_size_validation(self) -> None:
        with pytest.raises(ConfigurationError, match="min_operational_ensemble_size"):
            make_deployment_config(min_operational_ensemble_size=0)

    def test_min_quantile_levels_validation(self) -> None:
        with pytest.raises(ConfigurationError, match="min_operational_quantile_levels"):
            make_deployment_config(min_operational_quantile_levels=6)

    def test_alert_model_strategy_from_string(self) -> None:
        config = make_deployment_config(alert_model_strategy="primary")
        assert config.alert_model_strategy == ModelCombinationStrategy.PRIMARY

    def test_forecast_combination_strategy_default(self) -> None:
        config = make_deployment_config()
        assert config.forecast_combination_strategy == ModelCombinationStrategy.PRIMARY

    def test_enable_alert_flags_exist(self) -> None:
        config = make_deployment_config()
        assert config.enable_forecast_alerts is False
        assert config.enable_observation_alerts is False
        assert config.enable_pipeline_alerts is False


_MINIMAL_TOML = """\
max_retention_days = 3650
"""

_PATHS_TOML = """\
max_retention_days = 3650

[paths]
data_dir = "/some/path"
"""


class TestLoadConfig:
    def test_minimal_toml_populates_fields(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(_MINIMAL_TOML)
        config = load_config(cfg_file)
        assert config.max_retention_days == 3650
        assert config.forecast_hot_days == 548

    def test_paths_section_parsed_into_paths_data_dir(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(_PATHS_TOML)
        config = load_config(cfg_file)
        assert config.paths_data_dir == "/some/path"

    def test_adapter_sections_stripped(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML + "\n[adapters]\nfoo = 1\n[models]\nbar = 2\n"
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(toml)
        config = load_config(cfg_file)
        assert not hasattr(config, "adapters")
        assert not hasattr(config, "models")

    def test_uses_sapphire_config_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(_MINIMAL_TOML)
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(cfg_file))
        config = load_config()
        assert config.max_retention_days == 3650

    def test_raises_when_no_path_and_no_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        with pytest.raises(ValueError, match="SAPPHIRE_CONFIG is not set"):
            load_config()


class TestResolveEnvVars:
    def test_substitutes_set_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "hello")
        assert _resolve_env_vars("prefix_${MY_VAR}_suffix") == "prefix_hello_suffix"

    def test_multiple_substitutions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "foo")
        monkeypatch.setenv("B", "bar")
        assert _resolve_env_vars("${A}/${B}") == "foo/bar"

    def test_no_placeholders_unchanged(self) -> None:
        assert _resolve_env_vars("no placeholders here") == "no placeholders here"

    def test_unset_env_var_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ValueError, match=r"\$\{MISSING_VAR\} is not set"):
            _resolve_env_vars("${MISSING_VAR}")


class TestGetDangerLevelDefinitions:
    def test_returns_list_of_danger_level_definitions(self) -> None:
        config = make_deployment_config(
            danger_levels=[
                {
                    "name": "Low",
                    "level": 1,
                    "color": "#00ff00",
                    "trigger_probability": 0.3,
                    "resolve_probability": 0.1,
                },
                {
                    "name": "Medium",
                    "level": 2,
                    "color": "#ffff00",
                    "trigger_probability": 0.5,
                    "resolve_probability": 0.2,
                },
                {
                    "name": "High",
                    "level": 3,
                    "color": "#ff0000",
                    "trigger_probability": 0.8,
                    "resolve_probability": 0.4,
                },
            ]
        )
        result = config.get_danger_level_definitions()
        assert len(result) == 3
        assert all(isinstance(d, DangerLevelDefinition) for d in result)
        assert result[0].name == "Low"
        assert result[0].display_order == 1
        assert result[0].trigger_probability == 0.3
        assert result[0].min_trigger_duration == timedelta(0)
        assert result[2].name == "High"
        assert result[2].direction == ThresholdDirection.ABOVE

    def test_empty_danger_levels_returns_empty_list(self) -> None:
        config = make_deployment_config()
        assert config.get_danger_level_definitions() == []


class TestGetSeasonDefinitions:
    def test_returns_list_of_season_definitions(self) -> None:
        config = make_deployment_config(
            seasons=[
                {"name": "Monsoon", "months": [6, 7, 8, 9]},
                {"name": "Dry", "months": [11, 12, 1, 2]},
            ]
        )
        result = config.get_season_definitions()
        assert len(result) == 2
        assert all(isinstance(s, SeasonDefinition) for s in result)
        assert result[0].name == "Monsoon"
        assert result[0].months == frozenset({6, 7, 8, 9})
        assert result[1].name == "Dry"
        assert result[1].months == frozenset({11, 12, 1, 2})

    def test_empty_seasons_returns_empty_list(self) -> None:
        config = make_deployment_config()
        assert config.get_season_definitions() == []


class TestValidateRetention:
    def test_raises_when_max_retention_not_greater_than_forecast_hot_days(
        self,
    ) -> None:
        with pytest.raises(pydantic.ValidationError, match="max_retention_days"):
            make_deployment_config(forecast_hot_days=548, max_retention_days=548)

    def test_raises_when_max_retention_less_than_forecast_hot_days(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="max_retention_days"):
            make_deployment_config(forecast_hot_days=548, max_retention_days=100)

    def test_valid_when_max_retention_greater_than_forecast_hot_days(self) -> None:
        config = make_deployment_config(forecast_hot_days=548, max_retention_days=549)
        assert config.max_retention_days == 549
