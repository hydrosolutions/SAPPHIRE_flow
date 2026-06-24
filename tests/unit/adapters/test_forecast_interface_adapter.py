from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation

_DEFAULT_SCOPE = fi_boundary.FIArtifactScope.STATION


class FakeFIForecastModel:
    def __init__(
        self,
        input_requirement: fi_boundary.InputRequirement,
        artifact_scope: fi_boundary.FIArtifactScope = _DEFAULT_SCOPE,
    ) -> None:
        self._input_requirement = input_requirement
        self.artifact_scope = artifact_scope
        self.serialized_artifacts: list[Any] = []
        self.deserialized_payloads: list[bytes] = []

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return self._input_requirement

    def train(self, data: object, params: dict[str, object], rng: object) -> object:
        raise NotImplementedError

    def predict(self, artifact: object, inputs: object, rng: object) -> object:
        raise NotImplementedError

    def serialize_artifact(self, artifact: object) -> bytes:
        self.serialized_artifacts.append(artifact)
        if not isinstance(artifact, bytes):
            raise TypeError("fake artifact must be bytes")
        return b"serialized:" + artifact

    def deserialize_artifact(self, raw: bytes) -> object:
        self.deserialized_payloads.append(raw)
        return raw.removeprefix(b"serialized:")


def _target(unit: fi_boundary.Unit) -> fi_boundary.TargetSpec:
    return fi_boundary.TargetSpec(
        unit=unit,
        representations=frozenset({fi_boundary.OutputRepresentation.DETERMINISTIC}),
    )


def _past(
    *,
    lookback: int,
    max_nan: int,
    unit: fi_boundary.Unit,
) -> fi_boundary.PastKnownVariable:
    return fi_boundary.PastKnownVariable(
        lookback=lookback,
        max_nan=max_nan,
        unit=unit,
    )


def _future(
    *,
    future_steps: int,
    max_nan: int,
    unit: fi_boundary.Unit,
) -> fi_boundary.FutureKnownVariable:
    return fi_boundary.FutureKnownVariable(
        future_steps=future_steps,
        max_nan=max_nan,
        unit=unit,
    )


def _dynamic_spec() -> fi_boundary.DynamicInputSpec:
    return fi_boundary.DynamicInputSpec(
        past_known={
            "obs": {
                "precip": _past(
                    lookback=3,
                    max_nan=1,
                    unit=fi_boundary.Unit.MM,
                ),
                "temp": _past(
                    lookback=6,
                    max_nan=2,
                    unit=fi_boundary.Unit.DEG_C,
                ),
            },
            "radar": {
                "precip": _past(
                    lookback=5,
                    max_nan=1,
                    unit=fi_boundary.Unit.MM,
                ),
                "snow_depth": _past(
                    lookback=2,
                    max_nan=0,
                    unit=fi_boundary.Unit.CM,
                ),
            },
        },
        future_known={
            "nwp": {
                "precip_forecast": _future(
                    future_steps=5,
                    max_nan=0,
                    unit=fi_boundary.Unit.MM,
                ),
                "wind": _future(
                    future_steps=8,
                    max_nan=3,
                    unit=fi_boundary.Unit.M_PER_S,
                ),
            },
        },
    )


def _daily_dynamic_spec() -> fi_boundary.DynamicInputSpec:
    return fi_boundary.DynamicInputSpec(
        past_known={
            "era5": {
                "soil_moisture": _past(
                    lookback=10,
                    max_nan=4,
                    unit=fi_boundary.Unit.PERCENT,
                ),
            },
        },
        future_known={
            "nwp": {
                "temp_forecast": _future(
                    future_steps=6,
                    max_nan=2,
                    unit=fi_boundary.Unit.DEG_C,
                ),
            },
        },
    )


def _multi_product_requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={
            "discharge": _target(fi_boundary.Unit.M3_PER_S),
            "water_level": _target(fi_boundary.Unit.M),
        },
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: _dynamic_spec()}
            ),
            timedelta(hours=24): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: _daily_dynamic_spec()}
            ),
        },
        static={"catchment_area", "elevation"},
    )


def test_adapter_matches_station_and_group_protocols_by_scope() -> None:
    station_adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(
            _multi_product_requirement(),
            artifact_scope=fi_boundary.FIArtifactScope.STATION,
        )
    )
    group_adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(
            _multi_product_requirement(),
            artifact_scope=fi_boundary.FIArtifactScope.GROUP,
        )
    )

    assert station_adapter.artifact_scope is ArtifactScope.STATION
    assert isinstance(station_adapter, StationForecastModel)
    assert group_adapter.artifact_scope is ArtifactScope.GROUP
    assert isinstance(group_adapter, GroupForecastModel)


def test_projects_multi_product_multi_variable_input_requirement() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(_multi_product_requirement())
    )

    req = adapter.data_requirements
    assert req.target_parameters == frozenset({"discharge", "water_level"})
    assert req.past_dynamic_features == frozenset(
        {"precip", "temp", "snow_depth", "soil_moisture"}
    )
    assert req.future_dynamic_features == frozenset(
        {"precip_forecast", "wind", "temp_forecast"}
    )
    assert req.lookback_steps == 10
    assert req.forecast_horizon_steps == 8
    assert req.supported_time_steps == frozenset(
        {timedelta(hours=1), timedelta(hours=24)}
    )
    assert req.spatial_input_type is SpatialRepresentation.POINT
    assert req.static_features == frozenset({"catchment_area", "elevation"})


def test_multi_spatial_input_raises_configuration_error() -> None:
    dynamic_spec = _dynamic_spec()
    requirement = fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: dynamic_spec,
                    fi_boundary.FISpatialRepresentation.GRIDDED: dynamic_spec,
                }
            )
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="multi-spatial input not supported in v1",
    ):
        fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))


def test_no_future_known_input_raises_configuration_error() -> None:
    requirement = fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "precip": _past(
                                        lookback=3,
                                        max_nan=1,
                                        unit=fi_boundary.Unit.MM,
                                    )
                                }
                            }
                        )
                    )
                }
            )
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="cannot derive forecast horizon",
    ):
        fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))


def test_declared_units_returns_sap3_canonical_strings() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(_multi_product_requirement())
    )

    assert adapter.declared_units() == {
        "discharge": "m³/s",
        "water_level": "m",
        "precip": "mm",
        "temp": "°C",
        "snow_depth": "cm",
        "precip_forecast": "mm",
        "wind": "m/s",
        "soil_moisture": "%",
        "temp_forecast": "°C",
    }
    assert adapter.unsupported_units() == frozenset()


def test_declared_units_skips_unmapped_units_and_reports_unsupported_names() -> None:
    requirement = fi_boundary.InputRequirement(
        targets={
            "discharge": _target(fi_boundary.Unit.M3_PER_S),
            "runoff_rate": _target(fi_boundary.Unit.MM_PER_DAY),
        },
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "precip_rate": _past(
                                        lookback=3,
                                        max_nan=1,
                                        unit=fi_boundary.Unit.MM_PER_DAY,
                                    ),
                                    "temp": _past(
                                        lookback=6,
                                        max_nan=2,
                                        unit=fi_boundary.Unit.DEG_C,
                                    ),
                                }
                            },
                            future_known={
                                "nwp": {
                                    "temp_forecast": _future(
                                        future_steps=5,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.DEG_C,
                                    )
                                }
                            },
                        )
                    )
                }
            )
        },
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))

    assert adapter.declared_units() == {
        "discharge": "m³/s",
        "temp": "°C",
        "temp_forecast": "°C",
    }
    assert adapter.unsupported_units() == frozenset({"runoff_rate", "precip_rate"})


def test_declared_units_rejects_conflicting_units() -> None:
    requirement = fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "precip": _past(
                                        lookback=3,
                                        max_nan=1,
                                        unit=fi_boundary.Unit.MM,
                                    )
                                },
                                "radar": {
                                    "precip": _past(
                                        lookback=3,
                                        max_nan=1,
                                        unit=fi_boundary.Unit.CM,
                                    )
                                },
                            },
                            future_known={
                                "nwp": {
                                    "precip_forecast": _future(
                                        future_steps=5,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    )
                                }
                            },
                        )
                    )
                }
            )
        },
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))

    with pytest.raises(ConfigurationError, match="conflicting ForecastInterface unit"):
        adapter.declared_units()


def test_max_nan_tolerances_returns_declared_ints() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(_multi_product_requirement())
    )

    assert adapter.max_nan_tolerances() == {
        "precip": 1,
        "temp": 2,
        "snow_depth": 0,
        "precip_forecast": 0,
        "wind": 3,
        "soil_moisture": 4,
        "temp_forecast": 2,
    }


def test_max_nan_tolerances_rejects_conflicting_values() -> None:
    requirement = fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "precip": _past(
                                        lookback=3,
                                        max_nan=1,
                                        unit=fi_boundary.Unit.MM,
                                    )
                                },
                                "radar": {
                                    "precip": _past(
                                        lookback=3,
                                        max_nan=2,
                                        unit=fi_boundary.Unit.MM,
                                    )
                                },
                            },
                            future_known={
                                "nwp": {
                                    "precip_forecast": _future(
                                        future_steps=5,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    )
                                }
                            },
                        )
                    )
                }
            )
        },
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))

    with pytest.raises(
        ConfigurationError, match="conflicting ForecastInterface max_nan"
    ):
        adapter.max_nan_tolerances()


def test_serialize_artifact_and_deserialize_artifact_delegate() -> None:
    fake_model = FakeFIForecastModel(_multi_product_requirement())
    adapter = fi_boundary.ForecastInterfaceAdapter(fake_model)

    raw = adapter.serialize_artifact(b"artifact")
    artifact = adapter.deserialize_artifact(raw)

    assert raw == b"serialized:artifact"
    assert artifact == b"artifact"
    assert fake_model.serialized_artifacts == [b"artifact"]
    assert fake_model.deserialized_payloads == [b"serialized:artifact"]


def test_adapter_init_raises_when_fi_version_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fi_boundary, "SUPPORTED_FI_VERSION", "0.0.0")

    with pytest.raises(
        ConfigurationError,
        match="supported forecastinterface==0.0.0",
    ):
        fi_boundary.ForecastInterfaceAdapter(
            FakeFIForecastModel(_multi_product_requirement())
        )
