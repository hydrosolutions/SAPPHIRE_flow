from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

from sapphire_flow.api.model_display_names import (
    load_model_display_names,
    model_display_fields,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ArtifactScope
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.model import ModelRecord
from tests.fakes.fake_stores import FakeModelStore


class CountingModelStore(FakeModelStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Collection[ModelId]] = []

    def fetch_models_by_ids(self, model_ids: Collection[ModelId]) -> list[ModelRecord]:
        self.calls.append(model_ids)
        return super().fetch_models_by_ids(model_ids)


def test_bounded_lookup_and_legacy_fallback() -> None:
    store = CountingModelStore()
    model_id = ModelId("legacy")
    record = ModelRecord(
        id=model_id,
        display_name="  ",
        artifact_scope=ArtifactScope.STATION,
        description="",
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    store.register_model(record)
    assert load_model_display_names(store, []) == {}
    assert store.calls == []
    labels = load_model_display_names(store, [model_id] * 21)
    assert store.calls == [frozenset({model_id})]
    assert model_display_fields(model_id, labels) == {
        "model_display_name": "legacy",
        "model_display_name_short": None,
    }
    assert model_display_fields(ModelId("unknown"), labels) == {
        "model_display_name": "unknown",
        "model_display_name_short": None,
    }
