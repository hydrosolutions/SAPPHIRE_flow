from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.config.qc_rules import _default_swiss_qc_rules, load_qc_rules

_MINIMAL_TOML = """\
weather_hot_days = 180
forecast_hot_days = 548
max_retention_days = 3650

[qc_rules]
version = "2.0.0"

[[qc_rules.rules]]
rule_id = "range_check"
rule_version = "1.0.0"
parameter = "discharge"
time_step_seconds = 600
thresholds = { value_min = 0.0, value_max = 9999.0 }

[[qc_rules.rules]]
rule_id = "gross_outlier"
rule_version = "1.0.0"
parameter = "water_level"
time_step_seconds = 600
thresholds = { k_sigma = 3.0 }
"""

_NO_QC_TOML = """\
weather_hot_days = 180
forecast_hot_days = 548
max_retention_days = 3650
"""


class TestLoadFromToml:
    def test_load_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_MINIMAL_TOML)

        result = load_qc_rules(config_file)

        assert result.version == "2.0.0"
        assert len(result.rules) == 2

        discharge_rule = result.rules[0]
        assert discharge_rule.rule_id == "range_check"
        assert discharge_rule.parameter == "discharge"
        assert discharge_rule.time_step == timedelta(seconds=600)
        assert discharge_rule.thresholds == {"value_min": 0.0, "value_max": 9999.0}

        wl_rule = result.rules[1]
        assert wl_rule.rule_id == "gross_outlier"
        assert wl_rule.parameter == "water_level"
        assert wl_rule.thresholds == {"k_sigma": 3.0}


class TestDefaultRules:
    def test_default_rules_has_discharge_10min(self) -> None:
        rules = _default_swiss_qc_rules()
        discharge_10min = rules.rules_for("discharge", timedelta(seconds=600))
        assert len(discharge_10min) > 0

    def test_default_rules_has_discharge_daily(self) -> None:
        rules = _default_swiss_qc_rules()
        discharge_daily = rules.rules_for("discharge", timedelta(seconds=86400))
        assert len(discharge_daily) > 0

    def test_default_rules_has_water_level(self) -> None:
        rules = _default_swiss_qc_rules()
        wl_rules = rules.rules_for("water_level", timedelta(seconds=600))
        assert len(wl_rules) > 0

    def test_default_rules_has_water_temperature(self) -> None:
        rules = _default_swiss_qc_rules()
        wt_rules = rules.rules_for("water_temperature", timedelta(seconds=600))
        assert len(wt_rules) > 0

    def test_default_rules_has_precipitation(self) -> None:
        rules = _default_swiss_qc_rules()
        precip_rules = rules.rules_for("precipitation", timedelta(seconds=86400))
        assert len(precip_rules) > 0

    def test_default_rules_has_temperature(self) -> None:
        rules = _default_swiss_qc_rules()
        temp_rules = rules.rules_for("temperature", timedelta(seconds=86400))
        assert len(temp_rules) > 0

    def test_default_version(self) -> None:
        rules = _default_swiss_qc_rules()
        assert rules.version == "1.0.0"

    def test_water_level_daily_rules_exist(self) -> None:
        rules = _default_swiss_qc_rules()
        wl_daily = rules.rules_for("water_level", timedelta(seconds=86400))
        assert len(wl_daily) == 5
        rule_ids = {r.rule_id for r in wl_daily}
        assert rule_ids == {
            "range_check",
            "rate_of_change",
            "frozen_sensor",
            "spike",
            "gross_outlier",
        }


class TestRulesForFilter:
    def test_rules_for_returns_correct_subset(self) -> None:
        rules = _default_swiss_qc_rules()
        result = rules.rules_for("discharge", timedelta(seconds=600))

        assert all(r.parameter == "discharge" for r in result)
        assert all(r.time_step == timedelta(seconds=600) for r in result)

    def test_rules_for_excludes_daily_discharge(self) -> None:
        rules = _default_swiss_qc_rules()
        result = rules.rules_for("discharge", timedelta(seconds=600))
        daily = rules.rules_for("discharge", timedelta(seconds=86400))

        assert set(r.rule_id for r in result) & set(r.rule_id for r in daily)
        # 10-min and daily should be distinct objects
        assert not any(r in daily for r in result)

    def test_rules_for_empty_for_unknown_parameter(self) -> None:
        rules = _default_swiss_qc_rules()
        result = rules.rules_for("unknown_param", timedelta(seconds=600))
        assert result == ()


class TestMissingQcSectionReturnsDefault:
    def test_missing_qc_section_returns_default(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_NO_QC_TOML)

        result = load_qc_rules(config_file)
        default = _default_swiss_qc_rules()

        assert result.version == default.version
        assert result.rules == default.rules

    def test_no_path_no_env_raises(self) -> None:
        import os

        env_backup = os.environ.pop("SAPPHIRE_CONFIG", None)
        try:
            with pytest.raises(ValueError, match="SAPPHIRE_CONFIG"):
                load_qc_rules()
        finally:
            if env_backup is not None:
                os.environ["SAPPHIRE_CONFIG"] = env_backup
