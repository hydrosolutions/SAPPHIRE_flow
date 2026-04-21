from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.config.forecast_qc_rules import (
    _default_swiss_forecast_qc_rules,
    load_forecast_qc_rules,
)

_MINIMAL_TOML = """\
weather_hot_days = 180
forecast_hot_days = 548
max_retention_days = 3650

[forecast_qc_rules]
version = "2.0.0"

[[forecast_qc_rules.rules]]
rule_id = "negative_value"
rule_version = "1.0.0"
parameter = "discharge"
time_step_seconds = 3600
thresholds = { value_min = 0.0 }

[[forecast_qc_rules.rules]]
rule_id = "range_check"
rule_version = "1.0.0"
parameter = "discharge"
time_step_seconds = 3600
thresholds = { value_min = 0.0, value_max = 9999.0 }

[[forecast_qc_rules.rules]]
rule_id = "climatology_outlier"
rule_version = "1.0.0"
parameter = "water_level"
time_step_seconds = 86400
thresholds = { k_sigma = 4.0 }
"""

_NO_FORECAST_QC_TOML = """\
weather_hot_days = 180
forecast_hot_days = 548
max_retention_days = 3650
"""


class TestLoadDefaultsWhenSectionMissing:
    def test_load_defaults_when_section_missing(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_NO_FORECAST_QC_TOML)

        result = load_forecast_qc_rules(config_file)
        default = _default_swiss_forecast_qc_rules()

        assert result.version == default.version
        assert result.rules == default.rules

    def test_no_path_no_env_raises(self) -> None:
        import os

        env_backup = os.environ.pop("SAPPHIRE_CONFIG", None)
        try:
            with pytest.raises(ValueError, match="SAPPHIRE_CONFIG"):
                load_forecast_qc_rules()
        finally:
            if env_backup is not None:
                os.environ["SAPPHIRE_CONFIG"] = env_backup


class TestLoadFromToml:
    def test_load_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_MINIMAL_TOML)

        result = load_forecast_qc_rules(config_file)

        assert result.version == "2.0.0"
        assert len(result.rules) == 3

        neg_rule = result.rules[0]
        assert neg_rule.rule_id == "negative_value"
        assert neg_rule.parameter == "discharge"
        assert neg_rule.time_step == timedelta(seconds=3600)
        assert neg_rule.thresholds == {"value_min": 0.0}

        range_rule = result.rules[1]
        assert range_rule.rule_id == "range_check"
        assert range_rule.parameter == "discharge"
        assert range_rule.time_step == timedelta(seconds=3600)
        assert range_rule.thresholds == {"value_min": 0.0, "value_max": 9999.0}

        clim_rule = result.rules[2]
        assert clim_rule.rule_id == "climatology_outlier"
        assert clim_rule.parameter == "water_level"
        assert clim_rule.time_step == timedelta(seconds=86400)
        assert clim_rule.thresholds == {"k_sigma": 4.0}


class TestRulesForFilter:
    def test_rules_for_filters_correctly(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_MINIMAL_TOML)
        result = load_forecast_qc_rules(config_file)

        discharge_hourly = result.rules_for("discharge", timedelta(seconds=3600))
        assert len(discharge_hourly) == 2
        assert all(r.parameter == "discharge" for r in discharge_hourly)
        assert all(r.time_step == timedelta(seconds=3600) for r in discharge_hourly)

    def test_rules_for_returns_empty_for_unknown(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_MINIMAL_TOML)
        result = load_forecast_qc_rules(config_file)

        unknown = result.rules_for("precipitation", timedelta(seconds=3600))
        assert unknown == ()

    def test_rules_for_default_discharge_hourly(self) -> None:
        rules = _default_swiss_forecast_qc_rules()
        hourly = rules.rules_for("discharge", timedelta(seconds=3600))
        assert len(hourly) > 0
        assert all(r.parameter == "discharge" for r in hourly)
        assert all(r.time_step == timedelta(seconds=3600) for r in hourly)

    def test_rules_for_default_discharge_daily(self) -> None:
        rules = _default_swiss_forecast_qc_rules()
        daily = rules.rules_for("discharge", timedelta(seconds=86400))
        assert len(daily) > 0

    def test_rules_for_default_water_level(self) -> None:
        rules = _default_swiss_forecast_qc_rules()
        wl = rules.rules_for("water_level", timedelta(seconds=3600))
        assert len(wl) > 0

    def test_default_version(self) -> None:
        rules = _default_swiss_forecast_qc_rules()
        assert rules.version == "1.0.0"


class TestOverlaySupport:
    def test_overlay_patches_forecast_qc_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "config.toml"
        base.write_text(_MINIMAL_TOML)
        overlay = tmp_path / "overlay.toml"
        overlay.write_text('[forecast_qc_rules]\nversion = "4.2.0"\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))

        result = load_forecast_qc_rules(base)

        # overlay deep-merged — version changed, rules preserved
        assert result.version == "4.2.0"
        assert len(result.rules) == 3
