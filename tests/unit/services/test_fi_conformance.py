from __future__ import annotations

import random

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelSmokeTestError
from sapphire_flow.services.model_onboarding import assert_model_conforms
from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
from sapphire_flow.types.model import ModelDataRequirements
from tests.fakes.fake_fi_models import (
    REFERENCE_FI_HORIZON,
    REFERENCE_FI_STEP,
    ReferenceFIForecastModel,
)

_STEP = REFERENCE_FI_STEP
_HORIZON = REFERENCE_FI_HORIZON


class _MissingPredictStationModel:
    artifact_scope = ArtifactScope.STATION
    data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"discharge"}),
        future_dynamic_features=frozenset({"precipitation_forecast"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=3,
        forecast_horizon_steps=_HORIZON,
        spatial_input_type=SpatialRepresentation.POINT,
    )

    def train(
        self, data: object, params: dict[str, object], rng: random.Random
    ) -> bytes:
        del data, params, rng
        return b"artifact"

    def serialize_artifact(self, artifact: object) -> bytes:
        del artifact
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> bytes:
        return raw


def test_conformance_passes_for_station_fi_adapter() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        ReferenceFIForecastModel(fi_boundary.FIArtifactScope.STATION)
    )

    assert_model_conforms(adapter, random.Random(123))


def test_conformance_passes_for_group_fi_adapter() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        ReferenceFIForecastModel(fi_boundary.FIArtifactScope.GROUP),
        station_code_resolver=lambda station_id: f"station-{station_id}",
    )

    assert_model_conforms(adapter, random.Random(123))


def test_conformance_fails_for_non_deterministic_fi_adapter() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        ReferenceFIForecastModel(
            fi_boundary.FIArtifactScope.STATION,
            use_global_random=True,
        )
    )

    with pytest.raises(ModelSmokeTestError, match="serialized artifacts differ"):
        assert_model_conforms(adapter, random.Random(123))


def test_conformance_fails_for_missing_protocol_member() -> None:
    model = _MissingPredictStationModel()

    with pytest.raises(ModelSmokeTestError, match="StationForecastModel"):
        assert_model_conforms(model, random.Random(123))  # type: ignore[arg-type]
