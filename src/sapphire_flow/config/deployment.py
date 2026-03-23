from __future__ import annotations

import os
import re
import tomllib
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, model_validator

from sapphire_flow.types.domain import (
    DangerLevelDefinition,
    SeasonDefinition,
    SkillInterpretationBand,
    SkillInterpretationScheme,
)
from sapphire_flow.types.enums import ThresholdDirection

if TYPE_CHECKING:
    from pathlib import Path


class _DangerLevelInput(BaseModel):
    name: str
    level: int  # maps to display_order
    color: str
    trigger_probability: float = 0.5
    resolve_probability: float = 0.3
    min_trigger_duration_hours: float = 0.0
    min_resolve_duration_hours: float = 0.0
    direction: str = "above"


class _SeasonInput(BaseModel):
    name: str
    months: list[int]


class _SkillBandInput(BaseModel):
    lower: float
    upper: float
    label: str


class _SkillInterpretationInput(BaseModel):
    metric: str
    time_step_hours: float = 24.0
    bands: list[_SkillBandInput]


class DeploymentConfig(BaseModel):
    danger_levels: list[_DangerLevelInput] = []
    seasons: list[_SeasonInput] = []
    skill_interpretation: list[_SkillInterpretationInput] = []

    weather_hot_days: int = 180
    forecast_hot_days: int = 548
    max_retention_days: int

    observation_staleness_warning_hours: float = 6.0
    nwp_max_wait_hours: float = 3.0
    nwp_max_fallback_age_hours: float = 12.0

    warm_up_snapshot_max_age_hours: float = 48.0
    warm_up_snapshot_max_age_monsoon_hours: float = 24.0

    flow_regime_p50_percentile: float = 50.0
    flow_regime_p90_percentile: float = 90.0

    enable_forecast_alerts: bool = False
    enable_observation_alerts: bool = False
    enable_pipeline_alerts: bool = False
    threshold_check_mode: Literal["raw", "published", "both"] = "raw"

    infer_missing_thresholds: bool = False

    min_skill_samples: int = 100
    min_skill_seasons: int = 2

    default_display_timezone: str = "UTC"
    calendar: Literal["gregorian", "bikram_sambat"] = "gregorian"

    @model_validator(mode="after")
    def _validate_retention(self) -> DeploymentConfig:
        if self.max_retention_days <= self.forecast_hot_days:
            raise ValueError(
                f"max_retention_days ({self.max_retention_days}) must be > "
                f"forecast_hot_days ({self.forecast_hot_days})"
            )
        return self

    def get_danger_level_definitions(self) -> list[DangerLevelDefinition]:
        return [
            DangerLevelDefinition(
                name=dl.name,
                display_order=dl.level,
                trigger_probability=dl.trigger_probability,
                resolve_probability=dl.resolve_probability,
                min_trigger_duration=timedelta(hours=dl.min_trigger_duration_hours),
                min_resolve_duration=timedelta(hours=dl.min_resolve_duration_hours),
                direction=ThresholdDirection(dl.direction),
            )
            for dl in self.danger_levels
        ]

    def get_season_definitions(self) -> list[SeasonDefinition]:
        return [
            SeasonDefinition(name=s.name, months=frozenset(s.months))
            for s in self.seasons
        ]

    def get_skill_interpretation_schemes(self) -> list[SkillInterpretationScheme]:
        return [
            SkillInterpretationScheme(
                metric=si.metric,
                time_step=timedelta(hours=si.time_step_hours),
                bands=tuple(
                    SkillInterpretationBand(lower=b.lower, upper=b.upper, label=b.label)
                    for b in si.bands
                ),
            )
            for si in self.skill_interpretation
        ]


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ValueError(f"Environment variable ${{{var_name}}} is not set")
        return value

    return _ENV_VAR_PATTERN.sub(_replace, text)


def load_config(path: Path | str | None = None) -> DeploymentConfig:
    from pathlib import Path as _Path

    if path is None:
        env_path = os.environ.get("SAPPHIRE_CONFIG")
        if env_path is None:
            raise ValueError("No config path provided and SAPPHIRE_CONFIG is not set")
        path = env_path
    path = _Path(path)
    raw_text = path.read_text()
    resolved_text = _resolve_env_vars(raw_text)
    data = tomllib.loads(resolved_text)
    # Remove adapter sections (not part of DeploymentConfig)
    data.pop("adapters", None)
    data.pop("monitoring", None)
    data.pop("models", None)
    data.pop("api", None)
    return DeploymentConfig.model_validate(data)
