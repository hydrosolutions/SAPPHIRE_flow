from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.enums import (
    AggregationMethod,
    InputQualityCategory,
    InputQualityLevel,
    ModelCombinationStrategy,
    ParameterDomain,
    QcStatus,
    ThresholdDirection,
    ThresholdSource,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class GeoCoord:
    lon: float
    lat: float
    altitude_masl: float | None = None

    def __post_init__(self) -> None:
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"longitude {self.lon} out of range [-180, 180]")
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"latitude {self.lat} out of range [-90, 90]")


@dataclass(frozen=True, kw_only=True, slots=True)
class ParameterDefinition:
    name: str
    display_name: str
    unit: str
    parameter_domain: ParameterDomain
    aggregation_method: AggregationMethod
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class DangerLevelDefinition:
    name: str
    display_order: int
    trigger_probability: float
    resolve_probability: float
    min_trigger_duration: timedelta
    min_resolve_duration: timedelta
    direction: ThresholdDirection = ThresholdDirection.ABOVE

    def __post_init__(self) -> None:
        if not (0.0 < self.trigger_probability <= 1.0):
            raise ValueError(
                f"trigger_probability must be in (0, 1], got {self.trigger_probability}"
            )
        if not (0.0 < self.resolve_probability < self.trigger_probability):
            raise ValueError(
                f"resolve_probability must be in (0, trigger_probability), "
                f"got {self.resolve_probability} >= {self.trigger_probability}"
            )
        if self.min_trigger_duration < timedelta(0):
            raise ValueError(
                f"min_trigger_duration must be >= 0, got {self.min_trigger_duration}"
            )
        if self.min_resolve_duration < timedelta(0):
            raise ValueError(
                f"min_resolve_duration must be >= 0, got {self.min_resolve_duration}"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class StationThreshold:
    station_id: StationId
    danger_level: str
    parameter: Literal["discharge", "water_level"]
    value: float
    source: ThresholdSource
    created_at: UtcDatetime
    updated_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class QcFlag:
    rule_id: str
    rule_version: str
    status: QcStatus
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status == QcStatus.RAW:
            raise ValueError("QcFlag.status cannot be RAW — RAW means QC has not run")
        if self.status == QcStatus.MISSING:
            raise ValueError(
                "QcFlag.status cannot be MISSING — MISSING is set directly"
                " on observations, not by QC rules"
            )


def aggregate_qc_status(flags: list[QcFlag]) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED
    severity = {QcStatus.QC_PASSED: 0, QcStatus.QC_SUSPECT: 1, QcStatus.QC_FAILED: 2}
    worst = max(flags, key=lambda f: severity[f.status])
    return worst.status


@dataclass(frozen=True, kw_only=True, slots=True)
class InputQualityFlag:
    category: InputQualityCategory
    level: InputQualityLevel
    detail: str

    def __post_init__(self) -> None:
        if self.level == InputQualityLevel.FULL:
            raise ValueError(
                "InputQualityFlag must not be FULL — only record actual issues"
            )


def aggregate_input_quality(flags: list[InputQualityFlag]) -> InputQualityLevel:
    if not flags:
        return InputQualityLevel.FULL
    severity = {
        InputQualityLevel.FULL: 0,
        InputQualityLevel.PARTIAL: 1,
        InputQualityLevel.DEGRADED: 2,
    }
    worst = max(flags, key=lambda f: severity[f.level])
    return worst.level


@dataclass(frozen=True, kw_only=True, slots=True)
class QcRuleParams:
    rule_id: str
    rule_version: str
    parameter: str
    time_step: timedelta
    thresholds: dict[str, float]


@dataclass(frozen=True, kw_only=True, slots=True)
class QcRuleSet:
    version: str
    rules: tuple[QcRuleParams, ...]

    def rules_for(
        self, parameter: str, time_step: timedelta
    ) -> tuple[QcRuleParams, ...]:
        return tuple(
            r
            for r in self.rules
            if r.parameter == parameter and r.time_step == time_step
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class StationQcOverride:
    station_id: StationId
    rule_id: str
    parameter: str
    time_step: timedelta
    thresholds: dict[str, float | None]


@dataclass(frozen=True, kw_only=True, slots=True)
class ClimBaseline:
    station_id: StationId
    parameter: str
    day_of_year: int
    rolling_mean: float
    rolling_std: float
    sample_count: int


@dataclass(frozen=True, kw_only=True, slots=True)
class SeasonDefinition:
    name: str
    months: frozenset[int]

    def __post_init__(self) -> None:
        if not self.months:
            raise ValueError("months must not be empty")
        if not all(1 <= m <= 12 for m in self.months):
            raise ValueError(f"months must be in [1, 12], got {self.months}")


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillInterpretationBand:
    lower: float
    upper: float
    label: str


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillInterpretationScheme:
    metric: str
    time_step: timedelta
    bands: tuple[SkillInterpretationBand, ...]


ForecastParameter = Literal["discharge", "water_level"]


@dataclass(frozen=True, kw_only=True, slots=True)
class ExceedanceResult:
    station_id: StationId
    danger_level: str
    parameter: Literal["discharge", "water_level"]
    threshold_value: float
    exceedance_probability: float | None
    observed_value: float | None
    exceeded: bool
    model_ids: tuple[ModelId, ...] = ()
    strategy: ModelCombinationStrategy = ModelCombinationStrategy.PRIMARY

    def __post_init__(self) -> None:
        if self.exceeded and self.exceedance_probability is None:
            raise ValueError("exceedance_probability must be set when exceeded=True")


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastQcRuleParams:
    rule_id: str
    rule_version: str
    parameter: str
    time_step: timedelta
    thresholds: dict[str, float]


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastQcRuleSet:
    version: str
    rules: tuple[ForecastQcRuleParams, ...]

    def rules_for(
        self, parameter: str, time_step: timedelta
    ) -> tuple[ForecastQcRuleParams, ...]:
        return tuple(
            r
            for r in self.rules
            if r.parameter == parameter and r.time_step == time_step
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class StationForecastQcOverride:
    station_id: StationId
    rule_id: str
    parameter: str
    time_step: timedelta
    thresholds: dict[str, float | None]
