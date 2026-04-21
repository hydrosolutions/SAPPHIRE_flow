from __future__ import annotations

import os
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from pydantic import BaseModel, field_validator, model_validator

from sapphire_flow.types.domain import (
    DangerLevelDefinition,
    SeasonDefinition,
    SkillInterpretationBand,
    SkillInterpretationScheme,
)
from sapphire_flow.types.enums import ModelCombinationStrategy, ThresholdDirection
from sapphire_flow.types.model_onboarding import (
    SUPPORTED_SKILL_METRICS,
    SkillGateMetric,
)

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


class InputQualityConfig(BaseModel):
    obs_degraded_hours: float = 12.0
    nwp_age_partial_hours: float = 9.0
    nwp_age_degraded_hours: float = 11.0
    warmup_snapshot_age_partial_hours: float = 24.0
    warmup_snapshot_age_degraded_hours: float = 42.0


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

    alert_model_strategy: ModelCombinationStrategy = ModelCombinationStrategy.PRIMARY
    forecast_combination_strategy: ModelCombinationStrategy = (
        ModelCombinationStrategy.PRIMARY
    )
    min_operational_ensemble_size: int = 20
    min_operational_quantile_levels: int = 7

    infer_missing_thresholds: bool = False

    skill_gate_thresholds: dict[str, SkillGateMetric] = {}
    available_nwp_parameters: frozenset[str] = frozenset(
        {"precipitation", "temperature"}
    )

    min_skill_samples: int = 100
    min_skill_seasons: int = 2

    default_display_timezone: str = "UTC"
    calendar: Literal["gregorian", "bikram_sambat"] = "gregorian"

    paths_data_dir: str | None = None
    nwp_grid_archive_base_path: str | None = None

    input_quality: InputQualityConfig = InputQualityConfig()

    @field_validator("min_operational_ensemble_size")
    @classmethod
    def _validate_min_ensemble_size(cls, v: int) -> int:
        if v < 1:
            from sapphire_flow.exceptions import ConfigurationError

            raise ConfigurationError(
                f"min_operational_ensemble_size must be >= 1, got {v}"
            )
        return v

    @field_validator("min_operational_quantile_levels")
    @classmethod
    def _validate_min_quantile_levels(cls, v: int) -> int:
        if v < 7:
            from sapphire_flow.exceptions import ConfigurationError

            raise ConfigurationError(
                f"min_operational_quantile_levels must be >= 7, got {v}"
            )
        return v

    @model_validator(mode="after")
    def _validate_retention(self) -> Self:
        if self.max_retention_days <= self.forecast_hot_days:
            raise ValueError(
                f"max_retention_days ({self.max_retention_days}) must be > "
                f"forecast_hot_days ({self.forecast_hot_days})"
            )
        return self

    @model_validator(mode="after")
    def _validate_skill_gate_keys(self) -> Self:
        unknown = set(self.skill_gate_thresholds.keys()) - SUPPORTED_SKILL_METRICS
        if unknown:
            raise ValueError(
                f"Unknown skill gate metric(s): {sorted(unknown)}. "
                f"Valid: {sorted(SUPPORTED_SKILL_METRICS)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_input_quality_thresholds(self) -> Self:
        iq = self.input_quality
        sw = self.observation_staleness_warning_hours
        if iq.obs_degraded_hours <= sw:
            raise ValueError(
                f"obs_degraded_hours ({iq.obs_degraded_hours}) must be > "
                f"observation_staleness_warning_hours ({sw})"
            )
        nwp_gate = self.nwp_max_fallback_age_hours
        if iq.nwp_age_degraded_hours > nwp_gate:
            raise ValueError(
                f"nwp_age_degraded_hours ({iq.nwp_age_degraded_hours}) must be <= "
                f"nwp_max_fallback_age_hours ({nwp_gate})"
            )
        if iq.nwp_age_partial_hours > nwp_gate:
            raise ValueError(
                f"nwp_age_partial_hours ({iq.nwp_age_partial_hours}) must be <= "
                f"nwp_max_fallback_age_hours ({nwp_gate})"
            )
        wu_gate = self.warm_up_snapshot_max_age_hours
        if iq.warmup_snapshot_age_degraded_hours > wu_gate:
            raise ValueError(
                f"warmup_snapshot_age_degraded_hours "
                f"({iq.warmup_snapshot_age_degraded_hours}) must be <= "
                f"warm_up_snapshot_max_age_hours ({wu_gate})"
            )
        if iq.warmup_snapshot_age_partial_hours > wu_gate:
            raise ValueError(
                f"warmup_snapshot_age_partial_hours "
                f"({iq.warmup_snapshot_age_partial_hours}) must be <= "
                f"warm_up_snapshot_max_age_hours ({wu_gate})"
            )
        if iq.nwp_age_partial_hours >= iq.nwp_age_degraded_hours:
            raise ValueError("nwp_age_partial_hours must be < nwp_age_degraded_hours")
        if (
            iq.warmup_snapshot_age_partial_hours
            >= iq.warmup_snapshot_age_degraded_hours
        ):
            raise ValueError(
                "warmup_snapshot_age_partial_hours must be"
                " < warmup_snapshot_age_degraded_hours"
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


def _resolve_env_vars(text: str) -> str:  # pyright: ignore[reportUnusedFunction]
    # Called cross-module by config._overlay; pyright's reportUnusedFunction
    # doesn't track external callers of private-prefixed module functions.
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if not var_name.startswith("SAPPHIRE_"):
            raise ValueError(
                f"Environment variable ${{{var_name}}} is not in the allowlist; "
                "only variables prefixed with SAPPHIRE_ may be referenced in config"
            )
        value = os.environ.get(var_name)
        if value is None:
            raise ValueError(f"Environment variable ${{{var_name}}} is not set")
        return value

    return _ENV_VAR_PATTERN.sub(_replace, text)


def load_config(path: Path | str | None = None) -> DeploymentConfig:
    from pathlib import Path as _Path

    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    if path is None:
        env_path = os.environ.get("SAPPHIRE_CONFIG")
        if env_path is None:
            raise ValueError("No config path provided and SAPPHIRE_CONFIG is not set")
        path = env_path
    path = _Path(path)
    # Cast to dict[str, Any] — post-parse code treats TOML values loosely
    # (same behaviour as the prior tomllib.loads return type).
    data = cast("dict[str, Any]", load_merged_toml(path, _resolve_overlay_paths()))
    # Extract NWP grid archive path before popping adapters section
    _adapters = data.get("adapters", {})
    _weather_forecast = (
        _adapters.get("weather_forecast", {}) if isinstance(_adapters, dict) else {}
    )
    nwp_grid_archive_base_path = (
        _weather_forecast.get("archive_base_path")
        if isinstance(_weather_forecast, dict)
        else None
    )
    # Remove adapter sections (not part of DeploymentConfig)
    data.pop("adapters", None)
    data.pop("monitoring", None)
    data.pop("models", None)
    data.pop("api", None)
    data.pop("onboarding", None)
    paths_section = data.pop("paths", {})
    data["paths_data_dir"] = paths_section.get("data_dir")
    data["nwp_grid_archive_base_path"] = nwp_grid_archive_base_path
    return DeploymentConfig.model_validate(data)
