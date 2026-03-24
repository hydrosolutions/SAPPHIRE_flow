from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.protocols.stores import (
        ModelStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationGroupId, StationId

from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelAssignmentStatus,
    StationKind,
    StationStatus,
)
from sapphire_flow.types.training import TrainingScope, TrainingUnit


def determine_training_scope(
    model_ids: list[ModelId] | None,
    station_ids: list[StationId] | None,
    group_ids: list[StationGroupId] | None,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    model_store: ModelStore,
    station_store: StationStore,
    group_store: StationGroupStore,
) -> TrainingScope:
    models = (
        [model_store.fetch_model(mid) for mid in model_ids]
        if model_ids is not None
        else model_store.fetch_all_models()
    )
    models = [m for m in models if m is not None]

    units: list[TrainingUnit] = []

    for model in models:
        if model.artifact_scope == ArtifactScope.STATION:
            stations = station_store.fetch_all_stations(kind=StationKind.RIVER)
            for station in stations:
                if station.station_status != StationStatus.OPERATIONAL:
                    continue
                if station_ids is not None and station.id not in station_ids:
                    continue
                assignments = station_store.fetch_model_assignments(station.id)
                has_active = any(
                    a.model_id == model.id and a.status == ModelAssignmentStatus.ACTIVE
                    for a in assignments
                )
                if not has_active:
                    continue
                units.append(
                    TrainingUnit(
                        model_id=model.id,
                        station_id=station.id,
                        group_id=None,
                        station_ids=frozenset({station.id}),
                        training_period_start=period_start,
                        training_period_end=period_end,
                        time_step=time_step,
                    )
                )
        elif model.artifact_scope == ArtifactScope.GROUP:
            groups = group_store.fetch_groups_for_model(model.id)
            for group in groups:
                if group_ids is not None and group.id not in group_ids:
                    continue
                units.append(
                    TrainingUnit(
                        model_id=model.id,
                        station_id=None,
                        group_id=group.id,
                        station_ids=group.station_ids,
                        training_period_start=period_start,
                        training_period_end=period_end,
                        time_step=time_step,
                    )
                )

    return TrainingScope(units=tuple(units))
