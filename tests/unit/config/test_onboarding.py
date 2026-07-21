from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.onboarding import OnboardingConfig, load_onboarding_config

if TYPE_CHECKING:
    from pathlib import Path


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

    def test_parses_water_level_datum_and_unit_tables(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            "[onboarding]\n"
            'basin_ids = ["2009"]\n'
            "[onboarding.water_level_datums_masl]\n"
            '"2009" = 260.5\n'
            "[onboarding.water_level_units]\n"
            '"2009" = "m a.s.l."\n'
        )

        cfg = load_onboarding_config(toml)

        assert cfg is not None
        assert cfg.water_level_datums_masl == {"2009": 260.5}
        assert cfg.water_level_units == {"2009": "m a.s.l."}

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

    def test_overlay_replaces_basin_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "config.toml"
        base.write_text(
            "[onboarding]\n"
            'data_source = "camels-ch"\n'
            'basin_ids = ["2004", "2009", "2033", "2085", "2091", "2100"]\n'
        )
        overlay = tmp_path / "overlay.toml"
        overlay.write_text('[onboarding]\nbasin_ids = ["2004", "2009"]\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))

        cfg = load_onboarding_config(base)

        assert cfg is not None
        # overlay list replaces the base list wholesale
        assert cfg.basin_ids == ("2004", "2009")
        # unpatched keys preserved from base
        assert cfg.data_source == "camels-ch"


class TestLoadCalculatedStations:
    _BLOCK = (
        "[onboarding]\n"
        'basin_ids = ["2009"]\n'
        "[[onboarding.calculated]]\n"
        'code = "RES-INFLOW-1"\n'
        'name = "Reservoir Inflow"\n'
        'network = "dhm"\n'
        'parameter = "discharge"\n'
        "lon = 85.3\n"
        "lat = 27.7\n"
        "components = [\n"
        '  { code = "GAUGE-A", weight = 1.0 },\n'
        '  { code = "SPILL-C", weight = -1.0 },\n'
        "]\n"
    )

    def test_parses_calculated_block(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(self._BLOCK)
        cfg = load_onboarding_config(toml)
        assert cfg is not None
        assert len(cfg.calculated) == 1
        spec = cfg.calculated[0]
        assert spec.code == "RES-INFLOW-1"
        assert spec.network == "dhm"
        assert spec.parameter == "discharge"
        assert spec.timezone == "UTC"  # default
        assert spec.effective_from is None
        assert [(c.code, c.weight) for c in spec.components] == [
            ("GAUGE-A", 1.0),
            ("SPILL-C", -1.0),
        ]

    def test_no_calculated_section_is_empty(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text('[onboarding]\nbasin_ids = ["2009"]\n')
        cfg = load_onboarding_config(toml)
        assert cfg is not None
        assert cfg.calculated == ()

    def test_rejects_out_of_range_lat(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            "[onboarding]\n"
            "[[onboarding.calculated]]\n"
            'code = "X"\nname = "X"\nnetwork = "dhm"\nparameter = "discharge"\n'
            "lon = 0.0\nlat = 999.0\n"
            'components = [{ code = "A", weight = 1.0 }]\n'
        )
        with pytest.raises(ValueError, match="lat"):
            load_onboarding_config(toml)

    def test_rejects_empty_components(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            "[onboarding]\n"
            "[[onboarding.calculated]]\n"
            'code = "X"\nname = "X"\nnetwork = "dhm"\nparameter = "discharge"\n'
            "lon = 0.0\nlat = 0.0\n"
            "components = []\n"
        )
        with pytest.raises(ValueError, match="component"):
            load_onboarding_config(toml)

    def _one_component_block(self, extra: str, comp: str) -> str:
        return (
            "[onboarding]\n"
            "[[onboarding.calculated]]\n"
            'code = "X"\nname = "X"\nnetwork = "dhm"\nparameter = "discharge"\n'
            f"lon = 0.0\nlat = 0.0\n{extra}"
            f"components = [{comp}]\n"
        )

    def test_rejects_zero_weight(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(self._one_component_block("", '{ code = "A", weight = 0.0 }'))
        with pytest.raises(ValueError, match="nonzero"):
            load_onboarding_config(toml)

    def test_rejects_bad_effective_from(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            self._one_component_block(
                'effective_from = "not-a-date"\n', '{ code = "A", weight = 1.0 }'
            )
        )
        with pytest.raises(ValueError):
            load_onboarding_config(toml)

    def test_rejects_duplicate_component_codes(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            self._one_component_block(
                "", '{ code = "A", weight = 1.0 }, { code = "A", weight = 2.0 }'
            )
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_onboarding_config(toml)

    def test_rejects_self_reference(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        # calc code is "X"; a component also named "X" is a self-reference
        toml.write_text(self._one_component_block("", '{ code = "X", weight = 1.0 }'))
        with pytest.raises(ValueError, match="itself"):
            load_onboarding_config(toml)
