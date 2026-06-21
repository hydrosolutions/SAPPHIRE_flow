from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from sapphire_flow.config._overlay import (
    _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
    load_merged_toml,
)
from sapphire_flow.types.domain import QcRuleId, QcRuleParams, QcRuleSet

_VALID_RULE_IDS: frozenset[str] = frozenset(
    {
        "range_check",
        "rate_of_change",
        "spike",
        "gross_outlier",
        "frozen_sensor",
    }
)


def _parse_rule(raw: dict) -> QcRuleParams:
    rule_id = raw["rule_id"]
    if rule_id not in _VALID_RULE_IDS:
        raise ValueError(
            f"Unknown QC rule_id {rule_id!r}; valid: {sorted(_VALID_RULE_IDS)}"
        )
    return QcRuleParams(
        rule_id=cast("QcRuleId", rule_id),
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
    # Cast to dict[str, Any] — post-parse code treats TOML values loosely
    # (same behaviour as the prior tomllib.loads return type).
    data = cast("dict[str, Any]", load_merged_toml(path, _resolve_overlay_paths()))

    qc_section = data.get("qc_rules")
    if qc_section is None:
        return _default_swiss_qc_rules()

    version = qc_section.get("version", "1.0.0")
    rules = tuple(_parse_rule(r) for r in qc_section.get("rules", []))
    return QcRuleSet(version=version, rules=rules)
