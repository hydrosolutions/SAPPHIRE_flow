from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.services.model_registry import (
    build_registry_entry,
    discover_models,
    register_models,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
from sapphire_flow.types.ids import ModelId
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import FakeModelStore

_NOW = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
_CLOCK = lambda: _NOW  # noqa: E731


class TestBuildRegistryEntryStationModel:
    def test_fields_match_model_attributes(self) -> None:
        model_id = ModelId("lstm_daily")
        model = FakeStationForecastModel()
        entry = build_registry_entry(model_id, model, registered_at=_NOW)

        assert entry.id == model_id
        assert entry.artifact_scope == ArtifactScope.STATION
        assert entry.required_features == frozenset({"precipitation", "temperature"})
        assert entry.required_static_attributes == frozenset()
        assert entry.spatial_input_type == SpatialRepresentation.POINT
        assert entry.registered_at == _NOW

    def test_display_name_derived_from_model_id(self) -> None:
        entry = build_registry_entry(
            ModelId("lstm_daily"), FakeStationForecastModel(), registered_at=_NOW
        )
        assert entry.display_name == "Lstm Daily"

    def test_display_name_underscores_become_spaces(self) -> None:
        entry = build_registry_entry(
            ModelId("gr4j_v2"), FakeStationForecastModel(), registered_at=_NOW
        )
        assert " " in entry.display_name
        assert "_" not in entry.display_name

    def test_display_name_overridden_by_model_attribute(self) -> None:
        model = FakeStationForecastModel()
        model.display_name = "Custom Name"  # type: ignore[attr-defined]
        entry = build_registry_entry(ModelId("lstm_daily"), model, registered_at=_NOW)
        assert entry.display_name == "Custom Name"

    def test_description_defaults_to_empty_string(self) -> None:
        entry = build_registry_entry(
            ModelId("lstm_daily"), FakeStationForecastModel(), registered_at=_NOW
        )
        assert entry.description == ""


class TestBuildRegistryEntryGroupModel:
    def test_artifact_scope_is_group(self) -> None:
        model_id = ModelId("regional_lstm")
        model = FakeGroupForecastModel()
        entry = build_registry_entry(model_id, model, registered_at=_NOW)

        assert entry.artifact_scope == ArtifactScope.GROUP
        assert entry.id == model_id

    def test_supported_time_steps(self) -> None:
        from datetime import timedelta

        model = FakeGroupForecastModel()
        entry = build_registry_entry(
            ModelId("regional_lstm"), model, registered_at=_NOW
        )
        assert timedelta(hours=1) in entry.supported_time_steps


class TestRegisterModels:
    def test_register_two_models_store_has_both(self) -> None:
        store = FakeModelStore()
        models: dict[ModelId, object] = {
            ModelId("lstm_daily"): FakeStationForecastModel(),
            ModelId("regional_lstm"): FakeGroupForecastModel(),
        }
        entries = register_models(models, store, _CLOCK)  # type: ignore[arg-type]

        assert len(entries) == 2
        all_records = store.fetch_all_models()
        assert len(all_records) == 2
        ids = {r.id for r in all_records}
        assert ModelId("lstm_daily") in ids
        assert ModelId("regional_lstm") in ids

    def test_entries_match_model_attributes(self) -> None:
        store = FakeModelStore()
        models = {ModelId("lstm_daily"): FakeStationForecastModel()}
        entries = register_models(models, store, _CLOCK)  # type: ignore[arg-type]

        assert len(entries) == 1
        entry = entries[0]
        assert entry.id == ModelId("lstm_daily")
        assert entry.artifact_scope == ArtifactScope.STATION
        assert entry.registered_at == _NOW

    def test_record_stored_has_correct_display_name(self) -> None:
        store = FakeModelStore()
        models = {ModelId("lstm_daily"): FakeStationForecastModel()}
        register_models(models, store, _CLOCK)  # type: ignore[arg-type]

        record = store.fetch_model(ModelId("lstm_daily"))
        assert record is not None
        assert record.display_name == "Lstm Daily"


class TestRegisterIdempotent:
    def test_register_same_model_twice_no_error(self) -> None:
        store = FakeModelStore()
        models = {ModelId("lstm_daily"): FakeStationForecastModel()}

        register_models(models, store, _CLOCK)  # type: ignore[arg-type]
        register_models(models, store, _CLOCK)  # type: ignore[arg-type]

        all_records = store.fetch_all_models()
        assert len(all_records) == 1

    def test_second_registration_overwrites_first(self) -> None:
        store = FakeModelStore()
        models = {ModelId("lstm_daily"): FakeStationForecastModel()}

        register_models(models, store, _CLOCK)  # type: ignore[arg-type]
        register_models(models, store, _CLOCK)  # type: ignore[arg-type]

        record = store.fetch_model(ModelId("lstm_daily"))
        assert record is not None


class TestDiscoverModels:
    def test_no_entry_points_returns_empty_dict(self) -> None:
        result = discover_models()
        assert isinstance(result, dict)
        assert len(result) == 0
