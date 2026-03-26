from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.config.deployment import DeploymentConfig, load_config


class TestDeploymentConfig:
    def test_minimal_valid_config(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("max_retention_days = 3650\n")
        config = load_config(toml)
        assert config.max_retention_days == 3650
        assert config.weather_hot_days == 180

    def test_retention_validation_fails(self) -> None:
        with pytest.raises(ValueError, match="max_retention_days"):
            DeploymentConfig(max_retention_days=100, forecast_hot_days=548)

    def test_env_var_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_RETENTION", "5000")
        toml = tmp_path / "config.toml"
        toml.write_text("max_retention_days = ${MY_RETENTION}\n")
        config = load_config(toml)
        assert config.max_retention_days == 5000

    def test_unset_env_var_raises(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        content = 'default_display_timezone = "${NONEXISTENT_VAR}"\n'
        content += "max_retention_days = 3650\n"
        toml.write_text(content)
        with pytest.raises(ValueError, match="NONEXISTENT_VAR"):
            load_config(toml)

    def test_danger_levels_conversion(self) -> None:
        config = DeploymentConfig(
            max_retention_days=3650,
            danger_levels=[
                {
                    "name": "Low",
                    "level": 1,
                    "color": "#FF0",
                    "trigger_probability": 0.5,
                    "resolve_probability": 0.3,
                }
            ],
        )
        defs = config.get_danger_level_definitions()
        assert len(defs) == 1
        assert defs[0].name == "Low"
        assert defs[0].trigger_probability == 0.5

    def test_season_definitions_conversion(self) -> None:
        config = DeploymentConfig(
            max_retention_days=3650,
            seasons=[{"name": "winter", "months": [11, 12, 1, 2, 3]}],
        )
        defs = config.get_season_definitions()
        assert len(defs) == 1
        assert defs[0].months == frozenset({11, 12, 1, 2, 3})

    def test_adapter_sections_ignored(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            textwrap.dedent("""\
            max_retention_days = 3650
            [adapters.weather_forecast]
            type = "meteoswiss_nwp"
        """)
        )
        config = load_config(toml)
        assert config.max_retention_days == 3650

    def test_skill_interpretation_conversion(self) -> None:
        config = DeploymentConfig(
            max_retention_days=3650,
            skill_interpretation=[
                {
                    "metric": "crpss",
                    "time_step_hours": 24.0,
                    "bands": [
                        {"lower": -1e99, "upper": 0.0, "label": "no skill"},
                        {"lower": 0.0, "upper": 0.5, "label": "useful"},
                        {"lower": 0.5, "upper": 1e99, "label": "excellent"},
                    ],
                }
            ],
        )
        schemes = config.get_skill_interpretation_schemes()
        assert len(schemes) == 1
        assert schemes[0].metric == "crpss"
        assert len(schemes[0].bands) == 3
        assert schemes[0].bands[0].label == "no skill"

    def test_load_config_from_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toml = tmp_path / "profile.toml"
        toml.write_text("max_retention_days = 4000\n")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(toml))
        config = load_config()
        assert config.max_retention_days == 4000

    def test_load_config_no_path_no_env_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        with pytest.raises(ValueError, match="SAPPHIRE_CONFIG"):
            load_config()

    def test_paths_section_populates_field(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            textwrap.dedent("""\
            max_retention_days = 3650
            [paths]
            data_dir = "/some/path"
        """)
        )
        config = load_config(toml)
        assert config.paths_data_dir == "/some/path"

    def test_paths_section_absent_gives_none(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("max_retention_days = 3650\n")
        config = load_config(toml)
        assert config.paths_data_dir is None

    def test_paths_without_data_dir_gives_none(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            textwrap.dedent("""\
            max_retention_days = 3650
            [paths]
            other_key = "foo"
        """)
        )
        config = load_config(toml)
        assert config.paths_data_dir is None

    def test_paths_data_dir_env_var_interpolation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_DATA_ROOT", "/opt/data")
        toml = tmp_path / "config.toml"
        toml.write_text(
            textwrap.dedent("""\
            max_retention_days = 3650
            [paths]
            data_dir = "${MY_DATA_ROOT}/sapphire"
        """)
        )
        config = load_config(toml)
        assert config.paths_data_dir == "/opt/data/sapphire"

    def test_config_reference_toml_loads(self) -> None:
        from pathlib import Path as _Path

        ref = _Path(__file__).parents[2] / "docs" / "spec" / "config-reference.toml"
        config = load_config(ref)
        assert len(config.danger_levels) == 5
        assert len(config.seasons) == 2
        assert len(config.skill_interpretation) == 1
        assert config.skill_interpretation[0].metric == "crpss"
        assert len(config.skill_interpretation[0].bands) == 5
        assert config.paths_data_dir == "/tmp/sapphire-test-data"
