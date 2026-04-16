from __future__ import annotations

import os
from pathlib import Path

import pytest

from sapphire_flow.config.onboarding import OnboardingConfig, load_onboarding_config


class TestOnboardingConfig:
    def test_defaults(self) -> None:
        cfg = OnboardingConfig()
        assert cfg.data_source == "camels-ch"
        assert cfg.basin_ids == ()

    def test_frozen(self) -> None:
        cfg = OnboardingConfig()
        with pytest.raises(AttributeError):
            cfg.data_source = "other"  # type: ignore[misc]


class TestLoadOnboardingConfig:
    def test_parses_section(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            "[onboarding]\n"
            'data_source = "camels-ch"\n'
            'basin_ids = ["2004", "2009", "2135"]\n'
        )
        cfg = load_onboarding_config(toml)
        assert cfg is not None
        assert cfg.data_source == "camels-ch"
        assert cfg.basin_ids == ("2004", "2009", "2135")

    def test_missing_section_returns_none(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("max_retention_days = 730\n")
        result = load_onboarding_config(toml)
        assert result is None

    def test_uses_sapphire_config_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text('[onboarding]\nbasin_ids = ["2004"]\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(toml))
        cfg = load_onboarding_config()
        assert cfg is not None
        assert cfg.basin_ids == ("2004",)

    def test_raises_without_path_or_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        with pytest.raises(ValueError, match="SAPPHIRE_CONFIG"):
            load_onboarding_config()

    def test_basin_ids_are_strings(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("[onboarding]\nbasin_ids = [2004, 2009]\n")
        cfg = load_onboarding_config(toml)
        assert cfg is not None
        assert cfg.basin_ids == ("2004", "2009")
        assert all(isinstance(bid, str) for bid in cfg.basin_ids)

    def test_empty_basin_ids(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("[onboarding]\nbasin_ids = []\n")
        cfg = load_onboarding_config(toml)
        assert cfg is not None
        assert cfg.basin_ids == ()

    def test_default_data_source(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text('[onboarding]\nbasin_ids = ["2004"]\n')
        cfg = load_onboarding_config(toml)
        assert cfg is not None
        assert cfg.data_source == "camels-ch"
