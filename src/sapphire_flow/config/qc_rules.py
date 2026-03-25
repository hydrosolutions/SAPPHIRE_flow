from __future__ import annotations

import os
import re
import tomllib
from datetime import timedelta
from pathlib import Path

from sapphire_flow.types.domain import QcRuleParams, QcRuleSet

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ValueError(f"Environment variable ${{{var_name}}} is not set")
        return value

    return _ENV_VAR_PATTERN.sub(_replace, text)


def _parse_rule(raw: dict) -> QcRuleParams:
    return QcRuleParams(
        rule_id=raw["rule_id"],
        rule_version=raw["rule_version"],
        parameter=raw["parameter"],
        time_step=timedelta(seconds=raw["time_step_seconds"]),
        thresholds=dict(raw["thresholds"]),
    )


def _default_swiss_qc_rules() -> QcRuleSet:
    return QcRuleSet(
        version="1.0.0",
        rules=(
            # --- Discharge (10-min operational) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=600),
                thresholds={"value_min": 0.0, "value_max": 5000.0},
            ),
            QcRuleParams(
                rule_id="rate_of_change",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=600),
                thresholds={"max_rate": 50.0},
            ),
            QcRuleParams(
                rule_id="frozen_sensor",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=600),
                thresholds={"tolerance": 0.001, "min_consecutive": 12.0},
            ),
            QcRuleParams(
                rule_id="spike",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=600),
                thresholds={"tolerance": 0.1},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=600),
                thresholds={"k_sigma": 5.0},
            ),
            # --- Discharge (daily historical) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": 0.0, "value_max": 5000.0},
            ),
            QcRuleParams(
                rule_id="rate_of_change",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"max_rate": 500.0},
            ),
            QcRuleParams(
                rule_id="frozen_sensor",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"tolerance": 0.001, "min_consecutive": 5.0},
            ),
            QcRuleParams(
                rule_id="spike",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"tolerance": 0.1},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"k_sigma": 5.0},
            ),
            # --- Water level (10-min operational) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=600),
                thresholds={"value_min": -2.0, "value_max": 20.0},
            ),
            QcRuleParams(
                rule_id="rate_of_change",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=600),
                thresholds={"max_rate": 0.5},
            ),
            QcRuleParams(
                rule_id="frozen_sensor",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=600),
                thresholds={"tolerance": 0.001, "min_consecutive": 12.0},
            ),
            QcRuleParams(
                rule_id="spike",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=600),
                thresholds={"tolerance": 0.1},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=600),
                thresholds={"k_sigma": 5.0},
            ),
            # --- Water level (daily — CAMELS-CH lake stations) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": -5.0, "value_max": 30.0},
            ),
            QcRuleParams(
                rule_id="rate_of_change",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"max_rate": 1.0},
            ),
            QcRuleParams(
                rule_id="frozen_sensor",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"tolerance": 0.001, "min_consecutive": 5.0},
            ),
            QcRuleParams(
                rule_id="spike",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"tolerance": 0.3},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"k_sigma": 5.0},
            ),
            # --- Water temperature (10-min operational) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="water_temperature",
                time_step=timedelta(seconds=600),
                thresholds={"value_min": -2.0, "value_max": 40.0},
            ),
            QcRuleParams(
                rule_id="rate_of_change",
                rule_version="1.0.0",
                parameter="water_temperature",
                time_step=timedelta(seconds=600),
                thresholds={"max_rate": 2.0},
            ),
            QcRuleParams(
                rule_id="frozen_sensor",
                rule_version="1.0.0",
                parameter="water_temperature",
                time_step=timedelta(seconds=600),
                thresholds={"tolerance": 0.01, "min_consecutive": 18.0},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="water_temperature",
                time_step=timedelta(seconds=600),
                thresholds={"k_sigma": 4.0},
            ),
            # --- Precipitation (daily historical) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="precipitation",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": 0.0, "value_max": 500.0},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="precipitation",
                time_step=timedelta(seconds=86400),
                thresholds={"k_sigma": 5.0},
            ),
            # --- Temperature (daily historical) ---
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="temperature",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": -50.0, "value_max": 50.0},
            ),
            QcRuleParams(
                rule_id="gross_outlier",
                rule_version="1.0.0",
                parameter="temperature",
                time_step=timedelta(seconds=86400),
                thresholds={"k_sigma": 4.0},
            ),
        ),
    )


def load_qc_rules(config_path: Path | str | None = None) -> QcRuleSet:
    if config_path is None:
        env_path = os.environ.get("SAPPHIRE_CONFIG")
        if env_path is None:
            raise ValueError("No config path provided and SAPPHIRE_CONFIG is not set")
        config_path = env_path
    path = Path(config_path)
    raw_text = path.read_text()
    resolved_text = _resolve_env_vars(raw_text)
    data = tomllib.loads(resolved_text)

    qc_section = data.get("qc_rules")
    if qc_section is None:
        return _default_swiss_qc_rules()

    version = qc_section.get("version", "1.0.0")
    rules = tuple(_parse_rule(r) for r in qc_section.get("rules", []))
    return QcRuleSet(version=version, rules=rules)
