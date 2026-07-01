from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.services.model_onboarding import onboard_model
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ParameterDefinition
from sapphire_flow.types.enums import (
    AggregationMethod,
    OnboardingOutcome,
    ParameterDomain,
)
from sapphire_flow.types.ids import ModelId
from tests.conftest import (
    make_deployment_config,
    make_station_config,
    make_training_unit,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeParameterStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_MODEL_ID = ModelId("fi_model")
_STEP = timedelta(days=1)
_EPOCH = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))


class _RawFIModel:
    def __init__(self, requirement: fi_boundary.InputRequirement) -> None:
        self._input_requirement = requirement
        self.artifact_scope = fi_boundary.FIArtifactScope.STATION

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return self._input_requirement

    def train(self, data: object, params: dict[str, object], rng: object) -> object:
        raise NotImplementedError

    def predict(self, artifact: object, inputs: object, rng: object) -> object:
        raise NotImplementedError

    def serialize_artifact(self, artifact: object) -> bytes:
        if not isinstance(artifact, bytes):
            raise TypeError("fake artifact must be bytes")
        return artifact

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


def _requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            _STEP: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={},
                            future_known={
                                "nwp": {
                                    "precipitation": fi_boundary.FutureKnownVariable(
                                        future_steps=2,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    ),
                                    "temperature": fi_boundary.FutureKnownVariable(
                                        future_steps=2,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.DEG_C,
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
        static=frozenset(),
    )


def _adapter() -> fi_boundary.ForecastInterfaceAdapter:
    return fi_boundary.ForecastInterfaceAdapter(_RawFIModel(_requirement()))


def _param(name: str, unit: str) -> ParameterDefinition:
    return ParameterDefinition(
        name=name,
        display_name=name,
        unit=unit,
        parameter_domain=ParameterDomain.RIVER,
        aggregation_method=AggregationMethod.MEAN,
        created_at=_EPOCH,
    )


def _seeded_parameter_store() -> FakeParameterStore:
    store = FakeParameterStore()
    store.seed(
        [
            _param("discharge", "m³/s"),
            _param("precipitation", "mm"),
            _param("temperature", "°C"),
        ]
    )
    return store


def _run(parameter_store: FakeParameterStore | None):
    station = make_station_config(code="TEST-001")
    station_store = FakeStationStore()
    station_store.store_station(station)
    unit = make_training_unit(model_id=_MODEL_ID, station_id=station.id)

    group_store = FakeStationGroupStore()
    result = onboard_model(
        model_id=_MODEL_ID,
        model=_adapter(),
        units=(unit,),
        model_store=FakeModelStore(),
        station_store=station_store,
        group_store=group_store,
        artifact_store=FakeModelArtifactStore(group_store),
        obs_store=FakeObservationStore(),
        basin_store=FakeBasinStore(),
        hindcast_store=FakeHindcastStore(),
        skill_store=FakeSkillStore(),
        flow_regime_store=FakeFlowRegimeConfigStore(),
        forcing_source=FakeWeatherReanalysisSource(),
        config=make_deployment_config(),
        clock=lambda: _EPOCH,
        rng=random.Random(42),
        skip_smoke_test=True,
        parameter_store=parameter_store,
    )
    return result.units[0]


def test_onboard_model_skips_fi_when_canonical_units_absent() -> None:
    # RED baseline: without a parameter_store the canonical unit catalog is empty,
    # so every declared FI unit is flagged unsupported and the model is skipped.
    outcome = _run(parameter_store=None)

    assert outcome.outcome == OnboardingOutcome.SKIPPED_COMPAT
    assert not outcome.compatibility.is_compatible
    assert outcome.compatibility.fi_unsupported_units == frozenset(
        {"discharge", "precipitation", "temperature"}
    )


def test_onboard_model_passes_fi_unit_gate_with_parameter_store() -> None:
    # GREEN: threading canonical_units from the parameter store lets the FI unit
    # gate pass, so the model is no longer skipped on compatibility.
    outcome = _run(parameter_store=_seeded_parameter_store())

    assert outcome.compatibility.is_compatible
    assert outcome.compatibility.fi_unsupported_units == frozenset()
    assert outcome.outcome != OnboardingOutcome.SKIPPED_COMPAT
