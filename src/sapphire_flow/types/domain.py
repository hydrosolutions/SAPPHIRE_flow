# pyright: reportUnknownMemberType=false
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, NamedTuple, cast

from sapphire_flow.types.enums import (
    AggregationMethod,
    ParameterDomain,
    QcStatus,
    ThresholdDirection,
    ThresholdSource,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId


class GeoCoord(NamedTuple):
    lon: float
    lat: float
    altitude_masl: float | None = None


def _geocoord_new(
    cls: type, lon: float, lat: float, altitude_masl: float | None = None
) -> GeoCoord:
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"longitude {lon} out of range [-180, 180]")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"latitude {lat} out of range [-90, 90]")
    return cast("GeoCoord", tuple.__new__(cls, (lon, lat, altitude_masl)))


GeoCoord.__new__ = _geocoord_new  # type: ignore[method-assign]


class ParameterDefinition(NamedTuple):
    name: str
    display_name: str
    unit: str
    parameter_domain: ParameterDomain
    aggregation_method: AggregationMethod
    created_at: UtcDatetime


class DangerLevelDefinition(NamedTuple):
    name: str
    display_order: int
    trigger_probability: float
    resolve_probability: float
    min_trigger_duration: timedelta
    min_resolve_duration: timedelta
    direction: ThresholdDirection = ThresholdDirection.ABOVE


def _dangerlevel_new(
    cls: type,
    name: str,
    display_order: int,
    trigger_probability: float,
    resolve_probability: float,
    min_trigger_duration: timedelta,
    min_resolve_duration: timedelta,
    direction: ThresholdDirection = ThresholdDirection.ABOVE,
) -> DangerLevelDefinition:
    if not (0.0 < trigger_probability <= 1.0):
        raise ValueError(
            f"trigger_probability must be in (0, 1], got {trigger_probability}"
        )
    if not (0.0 < resolve_probability < trigger_probability):
        raise ValueError(
            f"resolve_probability must be in (0, trigger_probability), "
            f"got {resolve_probability} >= {trigger_probability}"
        )
    if min_trigger_duration < timedelta(0):
        raise ValueError(
            f"min_trigger_duration must be >= 0, got {min_trigger_duration}"
        )
    if min_resolve_duration < timedelta(0):
        raise ValueError(
            f"min_resolve_duration must be >= 0, got {min_resolve_duration}"
        )
    return cast(
        "DangerLevelDefinition",
        tuple.__new__(
            cls,
            (
                name,
                display_order,
                trigger_probability,
                resolve_probability,
                min_trigger_duration,
                min_resolve_duration,
                direction,
            ),
        ),
    )


DangerLevelDefinition.__new__ = _dangerlevel_new  # type: ignore[method-assign]


class StationThreshold(NamedTuple):
    station_id: StationId
    danger_level: str
    parameter: str
    value: float
    source: ThresholdSource
    created_at: UtcDatetime
    updated_at: UtcDatetime


class QcFlag(NamedTuple):
    rule_id: str
    rule_version: str
    status: QcStatus
    detail: str | None = None


def _qcflag_new(
    cls: type,
    rule_id: str,
    rule_version: str,
    status: QcStatus,
    detail: str | None = None,
) -> QcFlag:
    if status == QcStatus.RAW:
        raise ValueError("QcFlag.status cannot be RAW — RAW means QC has not run")
    if status == QcStatus.MISSING:
        raise ValueError(
            "QcFlag.status cannot be MISSING — MISSING is set directly on observations,"
            " not by QC rules"
        )
    return cast("QcFlag", tuple.__new__(cls, (rule_id, rule_version, status, detail)))


QcFlag.__new__ = _qcflag_new  # type: ignore[method-assign]


def aggregate_qc_status(flags: list[QcFlag]) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED
    severity = {QcStatus.QC_PASSED: 0, QcStatus.QC_SUSPECT: 1, QcStatus.QC_FAILED: 2}
    worst = max(flags, key=lambda f: severity[f.status])
    return worst.status


class SeasonDefinition(NamedTuple):
    name: str
    months: frozenset[int]


def _seasondef_new(cls: type, name: str, months: frozenset[int]) -> SeasonDefinition:
    if not months:
        raise ValueError("months must not be empty")
    if not all(1 <= m <= 12 for m in months):
        raise ValueError(f"months must be in [1, 12], got {months}")
    return cast("SeasonDefinition", tuple.__new__(cls, (name, months)))


SeasonDefinition.__new__ = _seasondef_new  # type: ignore[method-assign]


class SkillInterpretationBand(NamedTuple):
    lower: float
    upper: float
    label: str


class SkillInterpretationScheme(NamedTuple):
    metric: str
    time_step: timedelta
    bands: tuple[SkillInterpretationBand, ...]


class ExceedanceResult(NamedTuple):
    station_id: StationId
    danger_level: str
    parameter: str
    threshold_value: float
    exceedance_probability: float | None
    observed_value: float | None
    exceeded: bool
