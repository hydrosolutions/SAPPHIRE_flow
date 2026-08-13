from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import (
    ConfigurationError,
    UnsupportedModelRequirementError,
)
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
    # Plan 156: past-only (no future_known) — the 24h branch's temp_forecast
    # was DROPPED, not moved, so this stays the shape Plan 151 needs (one
    # future-forced branch + one past-only branch) rather than a second
    # future-forced branch, which Plan 156 now rejects at construction.
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
    )


def _multi_product_requirement() -> fi_boundary.InputRequirement:
    # Plan 156 (seam review, 2026-08-12): the 1h branch is the SOLE
    # future-forced branch; the 24h branch is past-only. Two future-forced
    # branches is exactly the shape Plan 156 rejects (see
    # test_multi_future_forced_time_step_requirement_rejected below) — this
    # fixture must stay on the allowed side of that rule.
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
    assert req.future_dynamic_features == frozenset({"precip_forecast", "wind"})
    assert req.lookback_steps == 10
    assert req.forecast_horizon_steps == 8
    # Plan 156: supported_time_steps is the FUTURE-FORCED branch(es) only —
    # never the past-only 24h branch — so no downstream `next(iter(...))`
    # site can arbitrarily land on a resolution the model cannot forecast at.
    assert req.supported_time_steps == frozenset({timedelta(hours=1)})
    assert req.spatial_input_type is SpatialRepresentation.POINT
    assert req.static_features == frozenset({"catchment_area", "elevation"})


def test_projection_excludes_target_history_from_past_dynamic_features() -> None:
    """M2 (Fix #2): discharge is a target AND its own past_known history.

    Target history must NOT be projected into ``past_dynamic_features`` (which is
    the FORCING channel); otherwise training_data tries to fetch "discharge" from
    the forcing source and the onboarding gate marks the model incompatible.
    """
    from tests.fakes.fake_fi_models import m2_fi_input_requirement

    adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(m2_fi_input_requirement())
    )

    req = adapter.data_requirements
    assert req.target_parameters == frozenset({"discharge"})
    # discharge is target history, not forcing — it must be excluded.
    assert "discharge" not in req.past_dynamic_features
    assert req.past_dynamic_features == frozenset()
    assert req.future_dynamic_features == frozenset({"precipitation", "temperature"})


def test_multi_spatial_input_raises_unsupported_requirement() -> None:
    """Review fix (2026-08-13): this raised ``ConfigurationError``, which
    ``discover_models`` RE-RAISES — so ONE model declaring a multi-spatial
    (valid FI, unrepresentable in SAP3) requirement darkened discovery for
    EVERY model. It is the same class as the multi-branch guard and must use
    the same skippable exception, or Plan 156's registry invariant only
    half-holds."""
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
        UnsupportedModelRequirementError,
        match="multi-spatial input not supported in v1",
    ):
        fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))


def test_no_future_known_input_raises_unsupported_requirement() -> None:
    """Review fix (2026-08-13): same registry-invariant reasoning as the
    multi-spatial case above — a past-only requirement is a legal FI shape
    SAP3 cannot represent, not an application misconfiguration."""
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
        UnsupportedModelRequirementError,
        match="cannot derive forecast horizon",
    ):
        fi_boundary.ForecastInterfaceAdapter(FakeFIForecastModel(requirement))


def _two_future_forced_branch_requirement() -> fi_boundary.InputRequirement:
    """The genuinely unsupportable shape (Plan 156): TWO time_step branches
    each declaring non-empty future_known — e.g. an MTS-LSTM config. Today
    this is silently flattened (features merged, lookback/horizon
    max-collapsed, one resolution picked arbitrarily); Plan 156 rejects it.
    """
    return fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: _dynamic_spec()}
            ),
            timedelta(hours=24): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
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
                    )
                }
            ),
        },
    )


def test_multi_future_forced_time_step_requirement_rejected() -> None:
    """Plan 156 T1, red-first criterion #1 (rejection half).

    Guard the import: ``UnsupportedModelRequirementError`` does not exist
    before the guard lands, and a bare ``from ... import`` would turn this
    into a collection ERROR rather than a RED assertion. ``getattr`` +
    ``pytest.fail`` keeps the failure a clean assertion either way.
    """
    from sapphire_flow import exceptions as sapphire_exceptions

    expected_error = getattr(
        sapphire_exceptions, "UnsupportedModelRequirementError", None
    )
    if expected_error is None:
        pytest.fail(
            "sapphire_flow.exceptions.UnsupportedModelRequirementError does "
            "not exist yet — a requirement with two future-forced time_step "
            "branches must be rejected at ForecastInterfaceAdapter "
            "construction (Plan 156 T1)"
        )

    with pytest.raises(expected_error, match="future_known"):
        fi_boundary.ForecastInterfaceAdapter(
            FakeFIForecastModel(_two_future_forced_branch_requirement())
        )


def test_multi_future_forced_time_step_error_names_both_resolutions() -> None:
    """The rejection error must NAME the offending resolutions (plan text:
    "rejected with a clear error naming the resolutions") — not just say
    "multiple time steps"."""
    from sapphire_flow import exceptions as sapphire_exceptions

    expected_error = getattr(
        sapphire_exceptions, "UnsupportedModelRequirementError", None
    )
    if expected_error is None:
        pytest.fail(
            "sapphire_flow.exceptions.UnsupportedModelRequirementError does "
            "not exist yet (Plan 156 T1)"
        )

    with pytest.raises(expected_error) as excinfo:
        fi_boundary.ForecastInterfaceAdapter(
            FakeFIForecastModel(_two_future_forced_branch_requirement())
        )

    message = str(excinfo.value)
    assert str(timedelta(hours=1)) in message
    assert str(timedelta(hours=24)) in message


def test_one_future_forced_plus_one_past_only_branch_is_accepted() -> None:
    """Plan 156 T1, red-first criterion #1 (acceptance half — guards against
    over-rejection). This is exactly the shape Plan 151 T2 needs: one
    future-forced branch (1h) plus a past-only branch (24h, no
    future_known)."""
    adapter = fi_boundary.ForecastInterfaceAdapter(
        FakeFIForecastModel(_multi_product_requirement())
    )

    assert adapter.data_requirements.supported_time_steps == frozenset(
        {timedelta(hours=1)}
    )


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
