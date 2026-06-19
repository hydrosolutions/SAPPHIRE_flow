from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelOutputError
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


class RecordingFIForecastModel:
    def __init__(
        self,
        result: fi_boundary.ModelResult,
        *,
        artifact_scope: fi_boundary.FIArtifactScope = (
            fi_boundary.FIArtifactScope.STATION
        ),
    ) -> None:
        self._input_requirement = _requirement()
        self.artifact_scope = artifact_scope
        self.result = result
        self.predict_inputs: fi_boundary.ModelInputs | None = None

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
        self.predict_inputs = inputs
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


def _past(
    unit: fi_boundary.Unit,
    *,
    max_nan: int,
) -> fi_boundary.PastKnownVariable:
    return fi_boundary.PastKnownVariable(lookback=3, max_nan=max_nan, unit=unit)


def _future(
    unit: fi_boundary.Unit,
    *,
    max_nan: int,
) -> fi_boundary.FutureKnownVariable:
    return fi_boundary.FutureKnownVariable(future_steps=3, max_nan=max_nan, unit=unit)


def _requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            _STEP: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": _past(
                                        fi_boundary.Unit.M3_PER_S,
                                        max_nan=2,
                                    ),
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation_forecast": _future(
                                        fi_boundary.Unit.MM,
                                        max_nan=1,
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


def _station_input_data(
    *,
    discharge: list[object] | None = None,
    precipitation_forecast: list[object] | None = None,
) -> StationInputData:
    return StationInputData(
        past_targets=_time_frame(
            {
                "timestamp": _timestamps(0, 1, 2),
                "discharge": discharge or [10.0, 11.0, 12.0],
            }
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {
                "timestamp": _timestamps(3, 4, 5),
                "precipitation_forecast": precipitation_forecast or [1.0, 2.0, 3.0],
            }
        ),
        static=None,
    )


def _station_model_inputs(data: StationInputData) -> StationModelInputs:
    return StationModelInputs(
        station_id=_SID_A,
        data=data,
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


def _group_model_inputs(
    *,
    data_a: StationInputData,
    data_b: StationInputData,
) -> GroupModelInputs:
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


def _success_variable(values: list[float] | None = None) -> fi_boundary.VariableOutput:
    return fi_boundary.VariableOutput(
        metadata=fi_boundary.VariableMetadata(
            unit=fi_boundary.Unit.M3_PER_S,
            timedelta=_STEP,
            forecast_horizon=3,
            offset=0,
        ),
        deterministic=fi_boundary.DeterministicData(
            data=_output_frame(values or [100.0, 101.0, 102.0])
        ),
        flags=frozenset(),
        status=fi_boundary.VariableStatus.SUCCESS,
    )


def _success_result(
    variables: dict[str, dict[str, fi_boundary.VariableOutput]],
) -> fi_boundary.ModelSuccess:
    output = fi_boundary.ModelOutput(
        model_name="fake-fi-model",
        issue_datetime=_ISSUE,
        variables=variables,
    )
    return fi_boundary.ModelSuccess(output=output)


def _adapter(
    result: fi_boundary.ModelResult,
    *,
    artifact_scope: fi_boundary.FIArtifactScope = fi_boundary.FIArtifactScope.STATION,
) -> tuple[fi_boundary.ForecastInterfaceAdapter, RecordingFIForecastModel]:
    model = RecordingFIForecastModel(result, artifact_scope=artifact_scope)
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model,
        station_code_resolver=lambda station_id: _CODES[station_id],
    )
    return adapter, model


def _past_discharge_series(model_inputs: fi_boundary.ModelInputs) -> pl.DataFrame:
    station = model_inputs.stations[fi_boundary._STATION_SCOPE_KEY]
    dynamic = station.dynamic[_STEP].data[fi_boundary.FISpatialRepresentation.POINT]
    return dynamic.past_known["obs"]["discharge"].data


def _group_precipitation_series(
    model_inputs: fi_boundary.ModelInputs,
    station_key: str,
) -> pl.DataFrame:
    station = model_inputs.stations[station_key]
    dynamic = station.dynamic[_STEP].data[fi_boundary.FISpatialRepresentation.POINT]
    return dynamic.future_known["nwp"]["precipitation_forecast"].data


def _nan_count(series: pl.Series) -> int:
    return int(series.is_nan().fill_null(False).sum())


def test_station_predict_allows_within_tolerance_missing_values_unchanged() -> None:
    data = _station_input_data(
        discharge=[10.0, None, float("nan")],
        precipitation_forecast=[1.0, 2.0, float("nan")],
    )
    adapter, model = _adapter(
        _success_result(
            {
                "station": {
                    "discharge": _success_variable(),
                }
            }
        )
    )

    ensembles, new_state = adapter.predict(
        object(),
        _station_model_inputs(data),
        random.Random(123),
    )

    assert new_state is None
    assert set(ensembles) == {"discharge"}
    assert model.predict_inputs is not None
    discharge = _past_discharge_series(model.predict_inputs)
    assert discharge.height == 3
    assert discharge["discharge"].null_count() == 1
    assert _nan_count(discharge["discharge"]) == 1


def test_station_predict_over_tolerance_raises_before_model_call() -> None:
    data = _station_input_data(
        precipitation_forecast=[1.0, None, float("nan")],
    )
    adapter, model = _adapter(
        _success_result(
            {
                "station": {
                    "discharge": _success_variable(),
                }
            }
        )
    )

    with pytest.raises(
        ModelOutputError,
        match="precipitation_forecast=2",
    ):
        adapter.predict(object(), _station_model_inputs(data), random.Random(123))

    assert model.predict_inputs is None


def test_group_predict_batch_skips_over_tolerance_station() -> None:
    data_a = _station_input_data(
        precipitation_forecast=[1.0, 2.0, float("nan")],
    )
    data_b = _station_input_data(
        discharge=[20.0, 21.0, 22.0],
        precipitation_forecast=[1.0, None, float("nan")],
    )
    adapter, model = _adapter(
        _success_result(
            {
                "gauge-a": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                }
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    result = adapter.predict_batch(
        object(),
        _group_model_inputs(data_a=data_a, data_b=data_b),
        random.Random(123),
    )

    assert set(result) == {_SID_A}
    assert model.predict_inputs is not None
    assert set(model.predict_inputs.stations) == {"gauge-a"}
    precipitation = _group_precipitation_series(model.predict_inputs, "gauge-a")
    assert precipitation["precipitation_forecast"].null_count() == 0
    assert _nan_count(precipitation["precipitation_forecast"]) == 1


def test_group_predict_batch_all_stations_over_tolerance_raises() -> None:
    data_a = _station_input_data(
        precipitation_forecast=[1.0, None, float("nan")],
    )
    data_b = _station_input_data(
        discharge=[None, float("nan"), None],
    )
    adapter, model = _adapter(
        _success_result(
            {
                "gauge-a": {
                    "discharge": _success_variable([1.0, 2.0, 3.0]),
                }
            }
        ),
        artifact_scope=fi_boundary.FIArtifactScope.GROUP,
    )

    with pytest.raises(ModelOutputError, match="all stations"):
        adapter.predict_batch(
            object(),
            _group_model_inputs(data_a=data_a, data_b=data_b),
            random.Random(123),
        )

    assert model.predict_inputs is None
