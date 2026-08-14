from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.model_registry import (
    build_registry_entry,
    discover_models,
    register_models,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AlertEligibility,
    ArtifactScope,
    ModelTier,
    SpatialRepresentation,
)
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
        reqs = entry.data_requirements
        assert reqs.past_dynamic_features == frozenset({"precipitation", "temperature"})
        assert entry.data_requirements.static_features == frozenset()
        assert entry.data_requirements.spatial_input_type == SpatialRepresentation.POINT
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
        # Review fix (2026-08-13): the shared fake is DAILY — an hourly-only
        # fake made daily consumers domain-invalid and expanded one test file
        # from 1.7s to 4m05s. See tests/fakes/fake_models.py.
        assert entry.data_requirements.supported_time_steps == frozenset(
            {timedelta(hours=24)}
        )


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
    def test_discovers_registered_entry_points(self) -> None:
        result = discover_models()
        assert isinstance(result, dict)
        assert "linear_regression_daily" in result

    def test_undeclared_model_fails_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _EntryPoint:
            name = "unknown_model"

            def load(self) -> type[FakeStationForecastModel]:
                return FakeStationForecastModel

        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: [_EntryPoint()],
        )

        with pytest.raises(ConfigurationError, match="ModelTier"):
            discover_models()

    def test_model_declaring_both_facets_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class DeclaredModel(FakeStationForecastModel):
            model_tier = ModelTier.SKILL
            alert_eligibility = AlertEligibility.SKILL_FORECAST

        class _EntryPoint:
            name = "declared_model"

            def load(self) -> type[DeclaredModel]:
                return DeclaredModel

        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: [_EntryPoint()],
        )

        result = discover_models()

        assert result[ModelId("declared_model")].model_tier is ModelTier.SKILL
        assert (
            result[ModelId("declared_model")].alert_eligibility
            is AlertEligibility.SKILL_FORECAST
        )

    def test_multi_resolution_fi_model_does_not_darken_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 156 T1, red-first criterion #2.

        ``discover_models()`` re-raises ``ConfigurationError`` for every
        entry point (a registry-wide blackout, ``:93-95``). A multi-
        FUTURE-FORCED-resolution FI model must not use that path: one bad
        model must not prevent good models from being discovered.
        """
        from datetime import timedelta

        from sapphire_flow.adapters import forecast_interface as fi_boundary

        class DeclaredModel(FakeStationForecastModel):
            model_tier = ModelTier.SKILL
            alert_eligibility = AlertEligibility.SKILL_FORECAST

        class _MultiResolutionFakeFIModel:
            """Structurally satisfies forecast_interface.ForecastModel (the
            runtime_checkable Protocol) with an InputRequirement declaring
            non-empty future_known in TWO time_step branches — the
            unsupported shape Plan 156 rejects. Declares ModelTier/
            AlertEligibility so classification is NOT why it is excluded —
            isolating the multi-resolution guard as the sole reason."""

            model_tier = ModelTier.SKILL
            alert_eligibility = AlertEligibility.SKILL_FORECAST

            def __init__(self) -> None:
                self.artifact_scope = fi_boundary.FIArtifactScope.STATION
                future = fi_boundary.FutureKnownVariable(
                    future_steps=5, max_nan=0, unit=fi_boundary.Unit.MM
                )
                self._input_requirement = fi_boundary.InputRequirement(
                    targets={
                        "discharge": fi_boundary.TargetSpec(
                            unit=fi_boundary.Unit.M3_PER_S,
                            representations=frozenset(
                                {fi_boundary.OutputRepresentation.DETERMINISTIC}
                            ),
                        )
                    },
                    dynamic={
                        timedelta(hours=1): fi_boundary.SpatialInputSpec(
                            data={
                                fi_boundary.FISpatialRepresentation.POINT: (
                                    fi_boundary.DynamicInputSpec(
                                        future_known={"nwp": {"precip": future}}
                                    )
                                )
                            }
                        ),
                        timedelta(hours=24): fi_boundary.SpatialInputSpec(
                            data={
                                fi_boundary.FISpatialRepresentation.POINT: (
                                    fi_boundary.DynamicInputSpec(
                                        future_known={"nwp": {"temp": future}}
                                    )
                                )
                            }
                        ),
                    },
                )

            @property
            def input_requirement(self) -> fi_boundary.InputRequirement:
                return self._input_requirement

            def train(self, inputs: object, *, config: object, rng: object) -> object:
                raise NotImplementedError

            def predict(
                self,
                artifact: object,
                *,
                inputs: object,
                issue_datetime: object,
                rng: object,
            ) -> object:
                raise NotImplementedError

            def serialize_artifact(self, artifact: object) -> bytes:
                raise NotImplementedError

            def deserialize_artifact(self, raw: bytes) -> object:
                raise NotImplementedError

        class _GoodEntryPointBefore:
            name = "good_model_before"

            def load(self) -> type[DeclaredModel]:
                return DeclaredModel

        class _GoodEntryPointAfter:
            name = "good_model_after"

            def load(self) -> type[DeclaredModel]:
                return DeclaredModel

        class _BadEntryPoint:
            name = "bad_multi_resolution_model"

            def load(self) -> type[_MultiResolutionFakeFIModel]:
                return _MultiResolutionFakeFIModel

        # The bad entry point sits BETWEEN two good ones: a subtly broken
        # implementation that `break`s or `return`s early on the bad model
        # (instead of `continue`-ing the loop) would still pass a
        # before-only assertion because the first good model was already
        # recorded — the after-model catches that class of bug.
        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: [
                _GoodEntryPointBefore(),
                _BadEntryPoint(),
                _GoodEntryPointAfter(),
            ],
        )

        result = discover_models()

        assert ModelId("good_model_before") in result
        assert ModelId("good_model_after") in result
        assert ModelId("bad_multi_resolution_model") not in result

    def test_static_naming_survives_the_fi_adapter_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 155 fixer round (blocker finding — Codex traced the path
        and found "raw model = caravan, adapted model = native").
        `discover_models()` wraps every REAL FI model in
        `ForecastInterfaceAdapter` (`adapters/forecast_interface.py`)
        before anything downstream ever sees it, and that adapter forwards
        nothing by default (see its `config_hash` property's own
        docstring: "this class has no `__getattr__` passthrough"). A raw
        FI model declaring `StaticNaming.CARAVAN` must still resolve as
        `CARAVAN` via `declared_static_naming` on the RETURNED, ADAPTED
        instance -- not silently fall back to the adapter's un-forwarded
        `NATIVE` default."""
        from datetime import timedelta

        from sapphire_flow.adapters import forecast_interface as fi_boundary
        from sapphire_flow.services.caravan_statics import declared_static_naming
        from sapphire_flow.types.enums import StaticNaming

        class _CaravanFakeFIModel:
            """Structurally satisfies forecast_interface.ForecastModel (the
            runtime_checkable Protocol) -- a single past-only dynamic
            branch keeps this well clear of Plan 156's multi-future-forced
            guard, isolating the static_naming propagation as the sole
            thing under test."""

            model_tier = ModelTier.SKILL
            alert_eligibility = AlertEligibility.SKILL_FORECAST
            static_naming = StaticNaming.CARAVAN

            def __init__(self) -> None:
                self.artifact_scope = fi_boundary.FIArtifactScope.STATION
                self._input_requirement = fi_boundary.InputRequirement(
                    targets={
                        "discharge": fi_boundary.TargetSpec(
                            unit=fi_boundary.Unit.M3_PER_S,
                            representations=frozenset(
                                {fi_boundary.OutputRepresentation.DETERMINISTIC}
                            ),
                        )
                    },
                    dynamic={
                        timedelta(hours=24): fi_boundary.SpatialInputSpec(
                            data={
                                fi_boundary.FISpatialRepresentation.POINT: (
                                    fi_boundary.DynamicInputSpec(
                                        past_known={
                                            "obs": {
                                                "discharge": (
                                                    fi_boundary.PastKnownVariable(
                                                        lookback=10,
                                                        max_nan=0,
                                                        unit=fi_boundary.Unit.M3_PER_S,
                                                    )
                                                )
                                            }
                                        },
                                        future_known={
                                            "nwp": {
                                                "precip": (
                                                    fi_boundary.FutureKnownVariable(
                                                        future_steps=5,
                                                        max_nan=0,
                                                        unit=fi_boundary.Unit.MM,
                                                    )
                                                )
                                            }
                                        },
                                    )
                                )
                            }
                        )
                    },
                    static={"area"},
                )

            @property
            def input_requirement(self) -> fi_boundary.InputRequirement:
                return self._input_requirement

            def train(self, inputs: object, *, config: object, rng: object) -> object:
                raise NotImplementedError

            def predict(
                self,
                artifact: object,
                *,
                inputs: object,
                issue_datetime: object,
                rng: object,
            ) -> object:
                raise NotImplementedError

            def serialize_artifact(self, artifact: object) -> bytes:
                raise NotImplementedError

            def deserialize_artifact(self, raw: bytes) -> object:
                raise NotImplementedError

        class _EntryPoint:
            name = "caravan_fake_model"

            def load(self) -> type[_CaravanFakeFIModel]:
                return _CaravanFakeFIModel

        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: [_EntryPoint()],
        )

        result = discover_models()
        adapted = result[ModelId("caravan_fake_model")]

        # This must be a REAL adapter instance, not the raw model itself --
        # otherwise the test would not be exercising the boundary at all.
        assert isinstance(adapted, fi_boundary.ForecastInterfaceAdapter)
        assert declared_static_naming(adapted) is StaticNaming.CARAVAN
