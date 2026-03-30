from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sapphire_flow.types.domain import (
        DangerLevelDefinition,
        ExceedanceResult,
        ForecastParameter,
        StationThreshold,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ModelId, StationId


@runtime_checkable
class ModelAlertStrategy(Protocol):
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]: ...
