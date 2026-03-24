from __future__ import annotations

import os
import tomllib
from datetime import timedelta
from pathlib import Path

from sapphire_flow.config.qc_rules import _resolve_env_vars
from sapphire_flow.types.domain import ForecastQcRuleParams, ForecastQcRuleSet


def _parse_rule(raw: dict) -> ForecastQcRuleParams:
    return ForecastQcRuleParams(
        rule_id=raw["rule_id"],
        rule_version=raw["rule_version"],
        parameter=raw["parameter"],
        time_step=timedelta(seconds=raw["time_step_seconds"]),
        thresholds=dict(raw["thresholds"]),
    )


def _default_swiss_forecast_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(
        version="1.0.0",
        rules=(
            # --- Discharge (daily) ---
            ForecastQcRuleParams(
                rule_id="negative_value",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": 0.0},
            ),
            ForecastQcRuleParams(
                rule_id="negative_value",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={"value_min": 0.0},
            ),
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": 0.0, "value_max": 5000.0},
            ),
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={"value_min": 0.0, "value_max": 5000.0},
            ),
            ForecastQcRuleParams(
                rule_id="flat_ensemble",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"tolerance": 0.001},
            ),
            ForecastQcRuleParams(
                rule_id="flat_ensemble",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={"tolerance": 0.001},
            ),
            ForecastQcRuleParams(
                rule_id="ensemble_spread",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"min_spread_ratio": 0.01, "max_spread_ratio": 10.0},
            ),
            ForecastQcRuleParams(
                rule_id="ensemble_spread",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={"min_spread_ratio": 0.01, "max_spread_ratio": 10.0},
            ),
            ForecastQcRuleParams(
                rule_id="climatology_outlier",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"k_sigma": 6.0},
            ),
            ForecastQcRuleParams(
                rule_id="climatology_outlier",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={"k_sigma": 6.0},
            ),
            ForecastQcRuleParams(
                rule_id="temporal_consistency",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={"max_rate": 500.0},
            ),
            ForecastQcRuleParams(
                rule_id="temporal_consistency",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={"max_rate": 500.0},
            ),
            ForecastQcRuleParams(
                rule_id="quantile_crossing",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=86400),
                thresholds={},
            ),
            ForecastQcRuleParams(
                rule_id="quantile_crossing",
                rule_version="1.0.0",
                parameter="discharge",
                time_step=timedelta(seconds=3600),
                thresholds={},
            ),
            # --- Water level (daily) ---
            ForecastQcRuleParams(
                rule_id="negative_value",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": -2.0},
            ),
            ForecastQcRuleParams(
                rule_id="negative_value",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={"value_min": -2.0},
            ),
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"value_min": -2.0, "value_max": 20.0},
            ),
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={"value_min": -2.0, "value_max": 20.0},
            ),
            ForecastQcRuleParams(
                rule_id="flat_ensemble",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"tolerance": 0.001},
            ),
            ForecastQcRuleParams(
                rule_id="flat_ensemble",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={"tolerance": 0.001},
            ),
            ForecastQcRuleParams(
                rule_id="ensemble_spread",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"min_spread_ratio": 0.01, "max_spread_ratio": 10.0},
            ),
            ForecastQcRuleParams(
                rule_id="ensemble_spread",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={"min_spread_ratio": 0.01, "max_spread_ratio": 10.0},
            ),
            ForecastQcRuleParams(
                rule_id="climatology_outlier",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"k_sigma": 6.0},
            ),
            ForecastQcRuleParams(
                rule_id="climatology_outlier",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={"k_sigma": 6.0},
            ),
            ForecastQcRuleParams(
                rule_id="temporal_consistency",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={"max_rate": 2.0},
            ),
            ForecastQcRuleParams(
                rule_id="temporal_consistency",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={"max_rate": 2.0},
            ),
            ForecastQcRuleParams(
                rule_id="quantile_crossing",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=86400),
                thresholds={},
            ),
            ForecastQcRuleParams(
                rule_id="quantile_crossing",
                rule_version="1.0.0",
                parameter="water_level",
                time_step=timedelta(seconds=3600),
                thresholds={},
            ),
        ),
    )


def load_forecast_qc_rules(config_path: Path | str | None = None) -> ForecastQcRuleSet:
    if config_path is None:
        env_path = os.environ.get("SAPPHIRE_CONFIG")
        if env_path is None:
            raise ValueError("No config path provided and SAPPHIRE_CONFIG is not set")
        config_path = env_path
    path = Path(config_path)
    raw_text = path.read_text()
    resolved_text = _resolve_env_vars(raw_text)
    data = tomllib.loads(resolved_text)

    qc_section = data.get("forecast_qc_rules")
    if qc_section is None:
        return _default_swiss_forecast_qc_rules()

    version = qc_section.get("version", "1.0.0")
    rules = tuple(_parse_rule(r) for r in qc_section.get("rules", []))
    return ForecastQcRuleSet(version=version, rules=rules)
