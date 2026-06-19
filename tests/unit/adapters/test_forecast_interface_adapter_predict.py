from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ConfigurationError, ModelOutputError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.model import (
    GroupModelInputs,
    StationInputData,
    StationModelInputs,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

_STEP = timedelta(hours=1)
_ISSUE = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
_SID_A = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_SID_B = StationId(UUID("00000000-0000-0000-0000-000000000002"))
_GROUP_ID = StationGroupId(UUID("00000000-0000-0000-0000-000000000100"))
_CODES = {_SID_A: "gauge-a", _SID_B: "gauge-b"}


class FakeFIForecastModel:
    def __init__(
        self,
        result: fi_boundary.ModelResult,
        artifact_scope: fi_boundary.FIArtifactScope = (
            fi_boundary.FIArtifactScope.STATION
        ),
    ) -> None:
        self._input_requirement = _requirement()
        self.artifact_scope = artifact_scope
        self.result = result
        self.predict_inputs: fi_boundary.ModelInputs | None = None
        self.predict_issue_datetime: datetime | None = None
        self.predict_artifact: object | None = None
        self.predict_rng: random.Random | None = None

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
        self.predict_artifact = artifact
        self.predict_inputs = inputs
        self.predict_issue_datetime = issue_datetime
        self.predict_rng = rng
        return self.result

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
    return fi_boundary.PastKnownVariable(lookback=3, max_nan=0, unit=unit)


def _future(unit: fi_boundary.Unit) -> fi_boundary.FutureKnownVariable:
    return fi_boundary.FutureKnownVariable(future_steps=3, max_nan=0, unit=unit)


def _requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={
            "discharge": _target(fi_boundary.Unit.M3_PER_S),
            "water_level": _target(fi_boundary.Unit.M),
        },
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
                                    "precipitation_forecast": _future(
                                        fi_boundary.Unit.MM
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
    )


def _timestamps(*hours: int) -> list[UtcDatetime]:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    return [ensure_utc(base + timedelta(hours=hour)) for hour in hours]


def _time_frame(data: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _station_input_data(*, offset: float = 0.0) -> StationInputData:
    return StationInputData(
        past_targets=_time_frame(
            {
                "timestamp": _timestamps(0, 1, 2),
                "discharge": [offset + 10.0, offset + 11.0, offset + 12.0],
            }
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {
                "timestamp": _timestamps(3, 4, 5),
                "precipitation_forecast": [
                    offset + 1.0,
                    offset + 2.0,
                    offset + 3.0,
                ],
            }
        ),
        static=None,
    )


def _station_model_inputs() -> StationModelInputs:
    return StationModelInputs(
        station_id=_SID_A,
        data=_station_input_data(),
        issue_time=_ISSUE,
        forecast_horizon_steps=3,
        time_step=_STEP,
    )


def _stack_by_station(frames: dict[StationId, pl.DataFrame]) -> pl.DataFrame:
    return pl.concat(
        [
            frame.with_columns(pl.lit(str(station_id)).alias("station_id")).select(
                ["station_id", *frame.columns]
            )
            for station_id, frame in frames.items()
        ]
    )


def _group_model_inputs() -> GroupModelInputs:
    data_a = _station_input_data()
    data_b = _station_input_data(offset=100.0)
    return GroupModelInputs(
        group_id=_GROUP_ID,
        station_ids=(_SID_A, _SID_B),
        past_targets=_stack_by_station(
            {_SID_A: data_a.past_targets, _SID_B: data_b.past_targets}
        ),
        past_dynamic=_stack_by_station(
            {_SID_A: data_a.past_dynamic, _SID_B: data_b.past_dynamic}
        ),
        future_dynamic=_stack_by_station(
            {_SID_A: data_a.future_dynamic, _SID_B: data_b.future_dynamic}
        ),
        static=None,
        issue_time=_ISSUE,
        forecast_horizon_steps=3,
        time_step=_STEP,
    )


def _output_frame(values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issue_datetime": [_ISSUE] * len(values),
            "datetime": _timestamps(7, 8, 9)[: len(values)],
            "value": values,
        }
    ).with_columns(
        pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
        pl.col("datetime").cast(pl.Datetime("us", "UTC")),
    )


def _metadata(
    unit: fi_boundary.Unit = fi_boundary.Unit.M3_PER_S,
) -> fi_boundary.VariableMetadata:
    return fi_boundary.VariableMetadata(
        unit=unit,
        timedelta=_STEP,
        forecast_horizon=3,
        offset=0,
    )


def _success_variable(
    values: list[float] | None = None,
    *,
    unit: fi_boundary.Unit = fi_boundary.Unit.M3_PER_S,
    status: fi_boundary.VariableStatus = fi_boundary.VariableStatus.SUCCESS,
    flags: frozenset[fi_boundary.ForecastFlag] = frozenset(),
) -> fi_boundary.VariableOutput:
    return fi_boundary.VariableOutput(
        metadata=_metadata(unit),
        deterministic=fi_boundary.DeterministicData(
            data=_output_frame(values or [100.0, 101.0, 102.0])
        ),
        flags=flags,
        status=status,
    )


def _failure_variable() -> fi_boundary.VariableOutput:
    return fi_boundary.VariableOutput(
        metadata=_metadata(),
        flags=frozenset(),
        status=fi_boundary.VariableStatus.FAILURE,
    )


def _low_floor_quantile_data() -> fi_boundary.QuantileData:
    levels = [0.05, 0.25, 0.50, 0.75, 0.95]
    data: dict[str, list[object]] = {
        "issue_datetime": [_ISSUE, _ISSUE, _ISSUE],
        "datetime": _timestamps(7, 8, 9),
    }
    for level in levels:
        data[str(level)] = [100.0 + step + level for step in range(3)]

    return fi_boundary.QuantileData(
        quantile_levels=levels,
        data=pl.DataFrame(data).with_columns(
            pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
            pl.col("datetime").cast(pl.Datetime("us", "UTC")),
        ),
    )


def _low_floor_quantile_variable() -> fi_boundary.VariableOutput:
    return fi_boundary.VariableOutput(
        metadata=_metadata(),
        quantiles=_low_floor_quantile_data(),
        flags=frozenset(),
        status=fi_boundary.VariableStatus.SUCCESS,
    )


def _model_output(
    variables: dict[str, dict[str, fi_boundary.VariableOutput]],
) -> fi_boundary.ModelOutput:
    if not variables:
        return fi_boundary.ModelOutput.model_construct(
            model_name="fake-fi-model",
            issue_datetime=_ISSUE,
            variables=variables,
        )
    return fi_boundary.ModelOutput(
        model_name="fake-fi-model",
        issue_datetime=_ISSUE,
        variables=variables,
    )


def _success_result(
    variables: dict[str, dict[str, fi_boundary.VariableOutput]],
) -> fi_boundary.ModelSuccess:
    return fi_boundary.ModelSuccess(output=_model_output(variables))


def _failure_result() -> fi_boundary.ModelFailure:
    return fi_boundary.ModelFailure(
        model_name="fake-fi-model",
        issue_datetime=_ISSUE,
        cause=fi_boundary.FailureCause.MODEL_ERROR,
        message="boom",
    )


def _adapter(
    result: fi_boundary.ModelResult,
    *,
    artifact_scope: fi_boundary.FIArtifactScope = fi_boundary.FIArtifactScope.STATION,
) -> tuple[fi_boundary.ForecastInterfaceAdapter, FakeFIForecastModel]:
    model = FakeFIForecastModel(result=result, artifact_scope=artifact_scope)
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model,
        station_code_resolver=lambda station_id: _CODES[station_id],
    )
    return adapter, model


def test_station_predict_success_returns_ensembles_and_none_state() -> None:
    inputs = _station_model_inputs()
    adapter, model = _adapter(
        _success_result(
            {
                "station": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                }
            }
        )
    )
    rng = random.Random(123)
    artifact = object()

    ensembles, new_state = adapter.predict(
        artifact,
        inputs,
        rng,
        prior_state=b"ignored",
    )

    assert new_state is None
    assert set(ensembles) == {"discharge"}
    assert ensembles["discharge"].station_id == inputs.station_id
    assert ensembles["discharge"].values["value"].to_list() == [1.0, 2.0, 3.0]
    assert model.predict_artifact is artifact
    assert model.predict_rng is rng
    assert model.predict_issue_datetime == _ISSUE
    assert model.predict_inputs is not None
    assert set(model.predict_inputs.stations) == {"station"}


def test_station_predict_rejects_group_scoped_adapter() -> None:
    adapter, _model = _adapter(
        _success_result(
            {
                "station": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                }
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    with pytest.raises(ConfigurationError, match="dispatch must key on artifact_scope"):
        adapter.predict(object(), _station_model_inputs(), random.Random(123))


def test_station_predict_model_failure_raises_model_output_error() -> None:
    adapter, _model = _adapter(_failure_result())

    with pytest.raises(ModelOutputError, match="MODEL_ERROR: boom"):
        adapter.predict(object(), _station_model_inputs(), random.Random(123))


def test_station_predict_empty_variables_raises_model_output_error() -> None:
    adapter, _model = _adapter(_success_result({}))

    with pytest.raises(ModelOutputError, match="empty variables"):
        adapter.predict(object(), _station_model_inputs(), random.Random(123))


def test_station_predict_all_failure_variables_raises_model_output_error() -> None:
    adapter, _model = _adapter(
        _success_result({"station": {"discharge": _failure_variable()}})
    )

    with pytest.raises(ModelOutputError, match="no usable output"):
        adapter.predict(object(), _station_model_inputs(), random.Random(123))


def test_station_predict_skips_failure_and_emits_partial_variables() -> None:
    adapter, _model = _adapter(
        _success_result(
            {
                "station": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                    "water_level": _failure_variable(),
                    "storage": _success_variable(
                        [4.0, 5.0, 6.0],
                        unit=fi_boundary.Unit.M,
                        status=fi_boundary.VariableStatus.PARTIAL,
                        flags=frozenset({fi_boundary.ForecastFlag.DATA_AVAILABILITY}),
                    ),
                }
            }
        )
    )

    ensembles, new_state = adapter.predict(
        object(), _station_model_inputs(), random.Random(123)
    )

    assert new_state is None
    assert set(ensembles) == {"discharge", "storage"}
    assert ensembles["storage"].units == "m"
    assert ensembles["storage"].values["value"].to_list() == [4.0, 5.0, 6.0]


def test_station_predict_sub_floor_quantiles_raise_model_output_error() -> None:
    adapter, _model = _adapter(
        _success_result({"station": {"discharge": _low_floor_quantile_variable()}})
    )

    with pytest.raises(ModelOutputError, match="at least 7 quantile levels"):
        adapter.predict(object(), _station_model_inputs(), random.Random(123))


def test_group_predict_batch_success_returns_station_id_keyed_results() -> None:
    adapter, model = _adapter(
        _success_result(
            {
                "gauge-a": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                },
                "gauge-b": {
                    "discharge": _success_variable([10.0, 20.0, 30.0]),
                    "water_level": _success_variable(
                        [5.0, 6.0, 7.0],
                        unit=fi_boundary.Unit.M,
                    ),
                },
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    result = adapter.predict_batch(object(), _group_model_inputs(), random.Random(123))

    assert set(result) == {_SID_A, _SID_B}
    ensembles_a, state_a = result[_SID_A]
    ensembles_b, state_b = result[_SID_B]
    assert state_a is None
    assert state_b is None
    assert set(ensembles_a) == {"discharge"}
    assert set(ensembles_b) == {"discharge", "water_level"}
    assert ensembles_a["discharge"].station_id == _SID_A
    assert ensembles_b["discharge"].station_id == _SID_B
    assert ensembles_b["water_level"].units == "m"
    assert model.predict_inputs is not None
    assert set(model.predict_inputs.stations) == {"gauge-a", "gauge-b"}


def test_group_predict_batch_rejects_station_scoped_adapter() -> None:
    adapter, _model = _adapter(
        _success_result(
            {
                "gauge-a": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                },
                "gauge-b": {
                    "discharge": _success_variable([10.0, 20.0, 30.0]),
                },
            }
        )
    )

    with pytest.raises(ConfigurationError, match="dispatch must key on artifact_scope"):
        adapter.predict_batch(object(), _group_model_inputs(), random.Random(123))


def test_group_predict_batch_missing_requested_station_raises() -> None:
    adapter, _model = _adapter(
        _success_result(
            {
                "gauge-a": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                },
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    with pytest.raises(ModelOutputError, match="omitted requested stations"):
        adapter.predict_batch(object(), _group_model_inputs(), random.Random(123))


def test_group_predict_batch_omits_station_with_only_failure_variables() -> None:
    adapter, _model = _adapter(
        _success_result(
            {
                "gauge-a": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                },
                "gauge-b": {
                    "discharge": _failure_variable(),
                },
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    result = adapter.predict_batch(object(), _group_model_inputs(), random.Random(123))

    assert set(result) == {_SID_A}
    assert result[_SID_A][0]["discharge"].station_id == _SID_A
    assert result[_SID_A][1] is None


def test_group_predict_batch_every_station_failed_raises_model_output_error() -> None:
    adapter, _model = _adapter(
        _success_result(
            {
                "gauge-a": {"discharge": _failure_variable()},
                "gauge-b": {"discharge": _failure_variable()},
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    with pytest.raises(ModelOutputError, match="no usable output"):
        adapter.predict_batch(object(), _group_model_inputs(), random.Random(123))


def test_group_predict_batch_empty_output_raises_model_output_error() -> None:
    adapter, _model = _adapter(
        _success_result({}),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    with pytest.raises(ModelOutputError, match="empty variables"):
        adapter.predict_batch(object(), _group_model_inputs(), random.Random(123))
