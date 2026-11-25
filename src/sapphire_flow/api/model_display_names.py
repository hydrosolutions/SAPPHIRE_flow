from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sapphire_flow.protocols.stores import ModelStore
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.model import ModelRecord


def load_model_display_names(
    store: ModelStore | None, model_ids: Iterable[ModelId]
) -> dict[ModelId, ModelRecord]:
    ids = frozenset(model_ids)
    if store is None or not ids:
        return {}
    return {record.id: record for record in store.fetch_models_by_ids(ids)}


def model_display_fields(
    model_id: ModelId, models: Mapping[ModelId, ModelRecord] | None = None
) -> dict[str, Any]:
    record = models.get(model_id) if models is not None else None
    name = record.display_name.strip() if record is not None else ""
    return {
        "model_display_name": name or str(model_id),
        "model_display_name_short": record.display_name_short if record else None,
    }
