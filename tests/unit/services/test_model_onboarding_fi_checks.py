from __future__ import annotations

from datetime import timedelta
from typing import Any

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.services.model_onboarding import validate_compatibility_for_unit
from sapphire_flow.types.ids import ModelId
from tests.conftest import make_station_config, make_training_unit
from tests.fakes.fake_stores import FakeStationStore

_MODEL_ID = ModelId("fi_model")
_STEP = timedelta(days=1)
_CANONICAL_UNITS = {
    "discharge": "m³/s",
    "precipitation": "mm",
    "temperature_forecast": "°C",
}


class FakeFIForecastModel:
    def __init__(
        self,
        input_requirement: fi_boundary.InputRequirement,
        artifact_scope: fi_boundary.FIArtifactScope = (
            fi_boundary.FIArtifactScope.STATION
        ),
    ) -> None:
        self._input_requirement = input_requirement
        self.artifact_scope = artifact_scope

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


def _target(unit: fi_boundary.Unit) -> fi_boundary.TargetSpec:
    return fi_boundary.TargetSpec(
        unit=unit,
        representations=frozenset({fi_boundary.OutputRepresentation.DETERMINISTIC}),
    )


def _past(unit: fi_boundary.Unit) -> fi_boundary.PastKnownVariable:
    return fi_boundary.PastKnownVariable(lookback=3, max_nan=0, unit=unit)


def _future(unit: fi_boundary.Unit) -> fi_boundary.FutureKnownVariable:
    return fi_boundary.FutureKnownVariable(future_steps=2, max_nan=0, unit=unit)


def _requirement(
    *,
    precip_unit: fi_boundary.Unit = fi_boundary.Unit.MM,
    spatial_type: fi_boundary.FISpatialRepresentation = (
        fi_boundary.FISpatialRepresentation.POINT
    ),
) -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            _STEP: fi_boundary.SpatialInputSpec(
                data={
                    spatial_type: fi_boundary.DynamicInputSpec(
                        past_known={
                            "obs": {
                                "precipitation": _past(precip_unit),
                            }
                        },
                        future_known={
                            "nwp": {
                                "temperature_forecast": _future(fi_boundary.Unit.DEG_C),
                            }
                        },
                    )
                }
            )
        },
        static=frozenset(),
    )


def _adapter(
    requirement: fi_boundary.InputRequirement,
) -> fi_boundary.ForecastInterfaceAdapter:
    return fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))


def _report(
    model: fi_boundary.ForecastInterfaceAdapter,
    *,
    canonical_units: dict[str, str] | None = None,
    station_code: str = "TEST-001",
) -> Any:
    station = make_station_config(code=station_code)
    station_store = FakeStationStore()
    station_store.store_station(station)
    unit = make_training_unit(model_id=_MODEL_ID, station_id=station.id)

    return validate_compatibility_for_unit(
        model_id=_MODEL_ID,
        model=model,
        unit=unit,
        station_store=station_store,
        group_store=None,  # type: ignore[arg-type]
        available_past_features=frozenset({"precipitation", "temperature_forecast"}),
        available_future_features=frozenset({"precipitation", "temperature_forecast"}),
        available_static_by_station={station.id: frozenset()},
        requested_time_step=_STEP,
        canonical_units=canonical_units or _CANONICAL_UNITS,
    )


def test_fi_unit_match_is_compatible() -> None:
    report = _report(_adapter(_requirement()))

    assert report.is_compatible
    assert report.fi_unit_mismatches == frozenset()
    assert report.fi_unsupported_units == frozenset()
    assert report.spatial_type_supported
    assert report.station_codes_resolvable


def test_fi_unit_mismatch_rejects_model() -> None:
    report = _report(_adapter(_requirement(precip_unit=fi_boundary.Unit.CM)))

    assert report.fi_unit_mismatches == frozenset({"precipitation"})
    assert not report.is_compatible


def test_fi_unsupported_unit_rejects_model() -> None:
    report = _report(_adapter(_requirement(precip_unit=fi_boundary.Unit.MM_PER_DAY)))

    assert report.fi_unsupported_units == frozenset({"precipitation"})
    assert not report.is_compatible


def test_fi_gridded_spatial_type_rejects_model() -> None:
    report = _report(
        _adapter(_requirement(spatial_type=fi_boundary.FISpatialRepresentation.GRIDDED))
    )

    assert not report.spatial_type_supported
    assert not report.is_compatible


def test_fi_empty_station_code_rejects_model() -> None:
    report = _report(_adapter(_requirement()), station_code="")

    assert not report.station_codes_resolvable
    assert not report.is_compatible
