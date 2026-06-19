from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
from tests.fakes.fake_models import FakeStationForecastModel

if TYPE_CHECKING:
    import random

    from sapphire_flow.types.ids import StationId

_STEP = timedelta(hours=1)


class RawFIForecastModel:
    def __init__(
        self,
        artifact_scope: fi_boundary.FIArtifactScope = (
            fi_boundary.FIArtifactScope.STATION
        ),
    ) -> None:
        self.artifact_scope = artifact_scope
        self._input_requirement = _input_requirement()

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return self._input_requirement

    def train(
        self,
        inputs: fi_boundary.ModelInputs,
        *,
        config: object,
        rng: random.Random,
    ) -> object:
        return object()

    def predict(
        self,
        artifact: object,
        *,
        inputs: fi_boundary.ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi_boundary.ModelResult:
        return fi_boundary.ModelSuccess(
            output=fi_boundary.ModelOutput(
                issue_datetime=issue_datetime,
                variables={},
            )
        )

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


def _target(unit: fi_boundary.Unit) -> fi_boundary.TargetSpec:
    return fi_boundary.TargetSpec(
        unit=unit,
        representations=frozenset({fi_boundary.OutputRepresentation.DETERMINISTIC}),
    )


def _past(unit: fi_boundary.Unit) -> fi_boundary.PastKnownVariable:
    return fi_boundary.PastKnownVariable(lookback=2, max_nan=0, unit=unit)


def _future(unit: fi_boundary.Unit) -> fi_boundary.FutureKnownVariable:
    return fi_boundary.FutureKnownVariable(future_steps=3, max_nan=0, unit=unit)


def _input_requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            _STEP: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": _past(fi_boundary.Unit.M3_PER_S),
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation": _future(fi_boundary.Unit.MM),
                                }
                            },
                        )
                    )
                }
            )
        },
        static=frozenset({"elevation"}),
    )


def _station_code_resolver(station_id: StationId) -> str:
    return f"station-{station_id}"


def test_is_fi_model_discriminates_raw_fi_from_native_model() -> None:
    assert fi_boundary.is_fi_model(RawFIForecastModel())
    assert not fi_boundary.is_fi_model(FakeStationForecastModel())


def test_adapt_if_fi_wraps_raw_fi_model_and_projects_sap3_contract() -> None:
    adapted = fi_boundary.adapt_if_fi(
        RawFIForecastModel(fi_boundary.FIArtifactScope.GROUP),
        station_code_resolver=_station_code_resolver,
    )

    assert isinstance(adapted, fi_boundary.ForecastInterfaceAdapter)
    assert adapted.artifact_scope is ArtifactScope.GROUP
    assert adapted.data_requirements.target_parameters == frozenset({"discharge"})
    assert adapted.data_requirements.past_dynamic_features == frozenset({"discharge"})
    assert adapted.data_requirements.future_dynamic_features == frozenset(
        {"precipitation"}
    )
    assert adapted.data_requirements.static_features == frozenset({"elevation"})
    assert adapted.data_requirements.supported_time_steps == frozenset({_STEP})
    assert adapted.data_requirements.lookback_steps == 2
    assert adapted.data_requirements.forecast_horizon_steps == 3
    assert adapted.data_requirements.spatial_input_type is SpatialRepresentation.POINT


def test_adapt_if_fi_returns_native_model_unchanged() -> None:
    native_model = FakeStationForecastModel()

    assert fi_boundary.adapt_if_fi(native_model) is native_model


def test_adapt_if_fi_raises_loudly_for_fi_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fi_boundary, "SUPPORTED_FI_VERSION", "0.0.0")

    with pytest.raises(
        ConfigurationError,
        match="ForecastInterface version mismatch",
    ):
        fi_boundary.adapt_if_fi(
            RawFIForecastModel(),
            station_code_resolver=_station_code_resolver,
        )
