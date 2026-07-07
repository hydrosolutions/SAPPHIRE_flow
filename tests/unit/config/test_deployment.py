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
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    NWP_RAINFALL_RUNOFF_MODEL_ID,
)
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


class TestAvailableNwpParameters:
    """M2: onboarding compatibility for NWP-forced models needs precipitation and
    temperature exposed via available_nwp_parameters."""

    def test_default_includes_precipitation_and_temperature(self) -> None:
        config = make_deployment_config()
        assert "precipitation" in config.available_nwp_parameters
        assert "temperature" in config.available_nwp_parameters

    def test_override_exposes_listed_parameters(self) -> None:
        config = make_deployment_config(
            available_nwp_parameters=frozenset({"precipitation", "temperature"})
        )
        assert config.available_nwp_parameters == frozenset(
            {"precipitation", "temperature"}
        )


class TestNwpGridRetention:
    """Plan 095: retention floor >= ceil(nwp_max_fallback_age_hours / 24) + 1."""

    def test_default_passes_with_default_fallback(self) -> None:
        # Default nwp_grid_retention_days=3 with default fallback=12.0 -> floor 2.
        config = make_deployment_config()
        assert config.nwp_grid_retention_days == 3

    def test_below_floor_rejected(self) -> None:
        # nwp_max_fallback_age_hours=24.0 -> floor ceil(24/24)+1 = 2; reject 2? No:
        # 2*24=48 >= 24+24=48, so 2 is exactly the floor (accepted); 1 is rejected.
        with pytest.raises(pydantic.ValidationError, match="nwp_grid_retention_days"):
            make_deployment_config(
                nwp_grid_retention_days=1, nwp_max_fallback_age_hours=24.0
            )

    def test_floor_boundary_accepted(self) -> None:
        config = make_deployment_config(
            nwp_grid_retention_days=2, nwp_max_fallback_age_hours=24.0
        )
        assert config.nwp_grid_retention_days == 2

    def test_two_rejected_when_fallback_forces_three(self) -> None:
        # A fallback of 25h -> ceil(25/24)+1 = 3, so retention=2 must be rejected.
        with pytest.raises(pydantic.ValidationError, match="nwp_grid_retention_days"):
            make_deployment_config(
                nwp_grid_retention_days=2, nwp_max_fallback_age_hours=25.0
            )


class TestModelPriorities:
    """Plan 089: config-driven model priority map (lower = preferred)."""

    def test_listed_model_resolves_configured_priority(self) -> None:
        config = make_deployment_config(
            model_priorities={"nwp_rainfall_runoff": 20, "climatology_fallback": 100}
        )
        assert config.priority_for_model("nwp_rainfall_runoff") == 20
        assert config.priority_for_model("climatology_fallback") == 100

    def test_unlisted_model_returns_default_priority(self) -> None:
        from sapphire_flow.config.deployment import DEFAULT_PRIORITY

        config = make_deployment_config(model_priorities={"nwp_rainfall_runoff": 20})
        assert config.priority_for_model("some_new_model") == DEFAULT_PRIORITY

    def test_default_priority_sits_between_skill_and_fallback(self) -> None:
        from sapphire_flow.config.deployment import DEFAULT_PRIORITY

        assert 30 < DEFAULT_PRIORITY < 90

    def test_empty_map_default(self) -> None:
        config = make_deployment_config()
        assert config.model_priorities == {}

    def test_explicit_below_tier_fallback_priority_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="climatology_fallback"):
            make_deployment_config(
                model_priorities={str(CLIMATOLOGY_FALLBACK_MODEL_ID): 5}
            )

    def test_omitted_fallback_priorities_load(self) -> None:
        config = make_deployment_config(
            model_priorities={str(NWP_RAINFALL_RUNOFF_MODEL_ID): 20}
        )
        assert config.priority_for_model(str(CLIMATOLOGY_FALLBACK_MODEL_ID)) == 50
        assert (
            config.assignment_priority_for_model(CLIMATOLOGY_FALLBACK_MODEL_ID) == 100
        )

    def test_configured_fallback_assignment_priority_used_when_present(self) -> None:
        config = make_deployment_config(
            model_priorities={str(CLIMATOLOGY_FALLBACK_MODEL_ID): 110}
        )
        assert (
            config.assignment_priority_for_model(CLIMATOLOGY_FALLBACK_MODEL_ID) == 110
        )

    def test_skill_assignment_priority_uses_config_default_chain(self) -> None:
        config = make_deployment_config(
            model_priorities={str(NWP_RAINFALL_RUNOFF_MODEL_ID): 20}
        )
        assert config.assignment_priority_for_model(NWP_RAINFALL_RUNOFF_MODEL_ID) == 20
        assert config.assignment_priority_for_model("some_new_model") == 50


class TestLoadConfig:
    def test_model_priorities_table_parsed_without_swallowing_scalars(
        self, tmp_path: Path
    ) -> None:
        toml = (
            _MINIMAL_TOML
            + '\ncalendar = "gregorian"\n'
            + "\n[model_priorities]\n"
            + "nwp_rainfall_runoff = 20\n"
            + "climatology_fallback = 100\n"
        )
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(toml)
        config = load_config(cfg_file)
        assert config.model_priorities == {
            "nwp_rainfall_runoff": 20,
            "climatology_fallback": 100,
        }
        # A [model_priorities] table must not swallow preceding top-level scalars.
        assert config.calendar == "gregorian"

    def test_nwp_cycle_min_age_minutes_defaults_and_parses(
        self, tmp_path: Path
    ) -> None:
        # Default when omitted.
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(_MINIMAL_TOML)
        assert load_config(cfg_file).nwp_cycle_min_age_minutes == 105

        # Plan 090: the delivery-delay scalar must parse AND (TOML-safety) not be
        # swallowed by the following [model_priorities] table header.
        toml = (
            _MINIMAL_TOML
            + "nwp_cycle_min_age_minutes = 120\n"
            + "\n[model_priorities]\n"
            + "climatology_fallback = 100\n"
        )
        override = tmp_path / "override.toml"
        override.write_text(toml)
        config = load_config(override)
        assert config.nwp_cycle_min_age_minutes == 120

    def test_nwp_grid_retention_days_defaults_and_parses(self, tmp_path: Path) -> None:
        # Default when omitted.
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(_MINIMAL_TOML)
        assert load_config(cfg_file).nwp_grid_retention_days == 3

        # Plan 095: the scalar must parse AND (TOML-safety) not be swallowed by a
        # following [model_priorities] table header.
        toml = (
            _MINIMAL_TOML
            + "nwp_grid_retention_days = 5\n"
            + "\n[model_priorities]\n"
            + "climatology_fallback = 100\n"
        )
        override = tmp_path / "override.toml"
        override.write_text(toml)
        config = load_config(override)
        assert config.nwp_grid_retention_days == 5
        assert config.model_priorities == {"climatology_fallback": 100}

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

    def test_overlay_patches_scalar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "deployment.toml"
        cfg_file.write_text(_MINIMAL_TOML)
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text("max_retention_days = 5000\n")
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay_file))

        config = load_config(cfg_file)

        assert config.max_retention_days == 5000


class TestResolveEnvVars:
    def test_substitutes_set_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAPPHIRE_MY_VAR", "hello")
        assert (
            _resolve_env_vars("prefix_${SAPPHIRE_MY_VAR}_suffix")
            == "prefix_hello_suffix"
        )

    def test_multiple_substitutions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAPPHIRE_A", "foo")
        monkeypatch.setenv("SAPPHIRE_B", "bar")
        assert _resolve_env_vars("${SAPPHIRE_A}/${SAPPHIRE_B}") == "foo/bar"

    def test_no_placeholders_unchanged(self) -> None:
        assert _resolve_env_vars("no placeholders here") == "no placeholders here"

    def test_unset_env_var_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_MISSING_VAR", raising=False)
        with pytest.raises(ValueError, match=r"\$\{SAPPHIRE_MISSING_VAR\} is not set"):
            _resolve_env_vars("${SAPPHIRE_MISSING_VAR}")

    def test_non_sapphire_prefix_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="not in the allowlist"):
            _resolve_env_vars("${DATABASE_URL}")


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
