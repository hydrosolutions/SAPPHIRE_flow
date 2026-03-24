from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.types.model import ModelRecord, ModelRegistryEntry

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import ModelStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId

log = structlog.get_logger()

_ENTRY_POINT_GROUP = "sapphire_flow.models"


def _derive_display_name(model_id: str) -> str:
    return " ".join(part.capitalize() for part in model_id.split("_"))


def discover_models() -> dict[ModelId, ForecastModel]:
    from sapphire_flow.types.ids import ModelId as _ModelId

    eps = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
    result: dict[ModelId, ForecastModel] = {}
    for ep in eps:
        model_id = _ModelId(ep.name)
        try:
            cls = ep.load()
            instance = cls()
            result[model_id] = instance
            log.info("model_discovered", model_id=ep.name)
        except Exception:
            log.exception("model_discovery_failed", model_id=ep.name)
    return result


def build_registry_entry(
    model_id: ModelId,
    model: ForecastModel,
    registered_at: UtcDatetime,
) -> ModelRegistryEntry:
    display_name: str = getattr(model, "display_name", None) or _derive_display_name(
        str(model_id)
    )
    description: str = getattr(model, "description", "") or ""

    return ModelRegistryEntry(
        id=model_id,
        display_name=display_name,
        description=description,
        artifact_scope=model.artifact_scope,
        required_features=model.required_features,
        required_static_attributes=model.required_static_attributes,
        spatial_input_type=model.spatial_input_type,
        supported_time_steps=model.supported_time_steps,
        registered_at=registered_at,
    )


def register_models(
    models: dict[ModelId, ForecastModel],
    store: ModelStore,
    clock: Callable[[], UtcDatetime],
) -> list[ModelRegistryEntry]:
    now = clock()
    entries: list[ModelRegistryEntry] = []
    for model_id, model in models.items():
        entry = build_registry_entry(model_id, model, registered_at=now)
        record = ModelRecord(
            id=entry.id,
            display_name=entry.display_name,
            artifact_scope=entry.artifact_scope,
            description=entry.description,
            created_at=now,
        )
        store.register_model(record)
        entries.append(entry)
        log.info("model_registered", model_id=str(model_id))
    return entries
