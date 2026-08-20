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
from sapphire_flow.types.enums import ForcingRoute
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
        requirement: fi_boundary.InputRequirement | None = None,
    ) -> None:
        self._input_requirement = requirement or _requirement()
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


def _colliding_requirement() -> fi_boundary.InputRequirement:
    """A past_known and a future_known variable declared with the SAME bare
    name ("precipitation") — the Plan 129 SeasonalPrecipRunoffRegression
    shape (past_known reanalysis/precipitation vs future_known
    nwp/precipitation), reproduced generically to lock the adapter's max_nan
    gate as temporality-aware regardless of the concrete model.
    """
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
                                        fi_boundary.Unit.M3_PER_S, max_nan=2
                                    ),
                                },
                                "reanalysis": {
                                    "precipitation": _past(
                                        fi_boundary.Unit.MM, max_nan=0
                                    ),
                                },
                            },
                            future_known={
                                "nwp": {
                                    "precipitation": _future(
                                        fi_boundary.Unit.MM, max_nan=0
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


def _colliding_station_input_data(
    *,
    past_precipitation: list[object] | None = None,
    future_precipitation: list[object] | None = None,
) -> StationInputData:
    return StationInputData(
        past_targets=_time_frame(
            {"timestamp": _timestamps(0, 1, 2), "discharge": [10.0, 11.0, 12.0]}
        ),
        past_dynamic=_time_frame(
            {
                "timestamp": _timestamps(0, 1, 2),
                "precipitation": past_precipitation or [1.0, 2.0, 3.0],
            }
        ),
        future_dynamic=_time_frame(
            {
                "timestamp": _timestamps(3, 4, 5),
                "precipitation": future_precipitation or [1.0, 2.0, 3.0],
            }
        ),
        static=None,
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


class TestNanGateIsTemporalityAwareAcrossCollidingNames:
    """Locks the Plan 129 post-implementation-review blocker: a model
    declaring a past_known AND a future_known variable with the SAME bare
    name (e.g. SeasonalPrecipRunoffRegression's past_known
    reanalysis/precipitation vs the base's future_known nwp/precipitation)
    must have BOTH independently NaN-gated — clean past data must never mask
    a NaN in the future channel of the same name, or vice versa.
    """

    def test_clean_past_does_not_mask_nan_in_colliding_future_variable(self) -> None:
        data = _colliding_station_input_data(
            past_precipitation=[1.0, 2.0, 3.0],  # clean past
            future_precipitation=[1.0, float("nan"), 3.0],  # dirty future
        )
        model = RecordingFIForecastModel(
            _success_result({"station": {"discharge": _success_variable()}}),
            requirement=_colliding_requirement(),
        )
        adapter = fi_boundary.ForecastInterfaceAdapter(
            model, station_code_resolver=lambda station_id: _CODES[station_id]
        )

        with pytest.raises(ModelOutputError, match="future_known.precipitation"):
            adapter.predict(object(), _station_model_inputs(data), random.Random(123))

        assert model.predict_inputs is None

    def test_clean_future_does_not_mask_nan_in_colliding_past_variable(self) -> None:
        data = _colliding_station_input_data(
            past_precipitation=[1.0, float("nan"), 3.0],  # dirty past
            future_precipitation=[1.0, 2.0, 3.0],  # clean future
        )
        model = RecordingFIForecastModel(
            _success_result({"station": {"discharge": _success_variable()}}),
            requirement=_colliding_requirement(),
        )
        adapter = fi_boundary.ForecastInterfaceAdapter(
            model, station_code_resolver=lambda station_id: _CODES[station_id]
        )

        with pytest.raises(ModelOutputError, match="past_known.precipitation"):
            adapter.predict(object(), _station_model_inputs(data), random.Random(123))

        assert model.predict_inputs is None

    def test_colliding_names_both_clean_succeeds(self) -> None:
        data = _colliding_station_input_data(
            past_precipitation=[1.0, 2.0, 3.0],
            future_precipitation=[1.0, 2.0, 3.0],
        )
        model = RecordingFIForecastModel(
            _success_result({"station": {"discharge": _success_variable()}}),
            requirement=_colliding_requirement(),
        )
        adapter = fi_boundary.ForecastInterfaceAdapter(
            model, station_code_resolver=lambda station_id: _CODES[station_id]
        )

        ensembles, _new_state = adapter.predict(
            object(), _station_model_inputs(data), random.Random(123)
        )

        assert set(ensembles) == {"discharge"}
        assert model.predict_inputs is not None


def _two_horizon_requirement() -> fi_boundary.InputRequirement:
    """Plan 151 T6 (D9): one future_known PRODUCT with two variables at
    DIFFERENT `future_steps` — the flagship "precip 2 steps + temp 10 steps"
    shape. Single branch, single product — legal under D4 (which restricts
    PRODUCT/mode count per branch, never per-variable horizon divergence
    within one product) — so this reaches `predict()` directly, no
    construct-only caveat needed.
    """
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
                                        fi_boundary.Unit.M3_PER_S, max_nan=2
                                    ),
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precip": fi_boundary.FutureKnownVariable(
                                        future_steps=2,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    ),
                                    "temp": fi_boundary.FutureKnownVariable(
                                        future_steps=10,
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
    )


def _two_horizon_station_input_data() -> StationInputData:
    # 10 future hourly buckets, fully populated (no nulls) for BOTH
    # features -- the raw track fetch reaches temp's 10-step need, so precip
    # (which only declares 2) genuinely has 10 REAL values available too.
    # Without the per-variable slice, precip would receive all 10 -- more
    # than it ever declared, not fewer, so this is NOT a NaN-tolerance
    # failure; it is an over-delivery the model never asked for.
    timestamps = _timestamps(*range(3, 13))
    return StationInputData(
        past_targets=_time_frame(
            {"timestamp": _timestamps(0, 1, 2), "discharge": [10.0, 11.0, 12.0]}
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {
                "timestamp": timestamps,
                "precip": [float(i) for i in range(10)],
                "temp": [float(i) for i in range(10)],
            }
        ),
        static=None,
    )


def _future_known_series(
    model_inputs: fi_boundary.ModelInputs, name: str
) -> pl.DataFrame:
    station = model_inputs.stations[fi_boundary._STATION_SCOPE_KEY]
    dynamic = station.dynamic[_STEP].data[fi_boundary.FISpatialRepresentation.POINT]
    return dynamic.future_known["nwp"][name].data


def test_predict_slices_each_future_known_variable_to_its_own_horizon() -> None:
    """Fails today: `_future_known_inputs` hands every future_known variable
    the WHOLE `future_dynamic` frame regardless of its own declared
    `future_steps` — precip (future_steps=2) would receive all 10 rows
    instead of the earliest 2, silently over-delivering data the model never
    declared (Plan 151 D9)."""
    data = _two_horizon_station_input_data()
    model = RecordingFIForecastModel(
        _success_result({"station": {"discharge": _success_variable()}}),
        requirement=_two_horizon_requirement(),
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model, station_code_resolver=lambda station_id: _CODES[station_id]
    )
    inputs = StationModelInputs(
        station_id=_SID_A,
        data=data,
        issue_time=_ISSUE,
        forecast_horizon_steps=10,
        time_step=_STEP,
        # Plan 151 D10: the D9 slice is a PER-TRACK-route behaviour; the
        # legacy superset route keeps its over-delivered frame (see
        # `TestForcingRouteGatesTheFutureStepsSlice` below).
        forcing_route=ForcingRoute.PER_TRACK,
    )

    adapter.predict(object(), inputs, random.Random(123))

    assert model.predict_inputs is not None
    precip = _future_known_series(model.predict_inputs, "precip")
    temp = _future_known_series(model.predict_inputs, "temp")
    assert precip.height == 2
    assert precip["precip"].to_list() == [0.0, 1.0]
    assert temp.height == 10


def _two_horizon_station_input_data_with_precip(
    precip_values: list[float],
) -> StationInputData:
    timestamps = _timestamps(*range(3, 13))
    return StationInputData(
        past_targets=_time_frame(
            {"timestamp": _timestamps(0, 1, 2), "discharge": [10.0, 11.0, 12.0]}
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {
                "timestamp": timestamps,
                "precip": precip_values,
                "temp": [float(i) for i in range(10)],
            }
        ),
        static=None,
    )


def test_nan_gate_slices_future_known_variable_to_its_own_horizon_before_counting() -> (
    None
):
    """Review fold-in (minor — T8 finding 8): locks the NaN-GATE side of the
    D9 slice specifically, not just the InputSeries data the test above
    covers -- removing the gate-side slice in
    `_variables_over_nan_tolerance` would leave every OTHER added test
    green. precip declares max_nan=0/future_steps=2; its first 2 raw values
    are clean but every value AFTER its own horizon is NaN. Without the
    NaN-gate-side slice, the gate counts NaNs over the WHOLE 10-row frame
    and raises, even though the model never declared (or would ever
    receive) those trailing rows."""
    precip_values = [0.0, 1.0, *([float("nan")] * 8)]
    data = _two_horizon_station_input_data_with_precip(precip_values)
    model = RecordingFIForecastModel(
        _success_result({"station": {"discharge": _success_variable()}}),
        requirement=_two_horizon_requirement(),
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model, station_code_resolver=lambda station_id: _CODES[station_id]
    )
    inputs = StationModelInputs(
        station_id=_SID_A,
        data=data,
        issue_time=_ISSUE,
        forecast_horizon_steps=10,
        time_step=_STEP,
        # Plan 151 D10: the D9 slice is a PER-TRACK-route behaviour; the
        # legacy superset route keeps its over-delivered frame (see
        # `TestForcingRouteGatesTheFutureStepsSlice` below).
        forcing_route=ForcingRoute.PER_TRACK,
    )

    # Must NOT raise -- the trailing NaNs sit outside precip's own horizon.
    adapter.predict(object(), inputs, random.Random(123))


def test_nan_gate_still_fails_on_nan_inside_own_horizon() -> None:
    """Paired negative case: a NaN INSIDE precip's own declared 2-step
    horizon must still fail `max_nan=0` -- proving the slice narrows the
    counted WINDOW, not the tolerance itself."""
    precip_values = [0.0, float("nan"), *([1.0] * 8)]
    data = _two_horizon_station_input_data_with_precip(precip_values)
    model = RecordingFIForecastModel(
        _success_result({"station": {"discharge": _success_variable()}}),
        requirement=_two_horizon_requirement(),
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model, station_code_resolver=lambda station_id: _CODES[station_id]
    )
    inputs = StationModelInputs(
        station_id=_SID_A,
        data=data,
        issue_time=_ISSUE,
        forecast_horizon_steps=10,
        time_step=_STEP,
        # Plan 151 D10: the D9 slice is a PER-TRACK-route behaviour; the
        # legacy superset route keeps its over-delivered frame (see
        # `TestForcingRouteGatesTheFutureStepsSlice` below).
        forcing_route=ForcingRoute.PER_TRACK,
    )

    with pytest.raises(ModelOutputError, match="max_nan"):
        adapter.predict(object(), inputs, random.Random(123))


def _staggered_two_horizon_station_input_data() -> StationInputData:
    """Precip (future_steps=2) and temp (future_steps=10) each have their
    OWN, non-overlapping timestamp set -- unlike every fixture above, where
    both features share the identical 10-row timestamp grid and only differ
    in which trailing rows are NaN. Pivoting these two feature-local grids
    into ONE `future_dynamic` frame (a real union join, not a shared range)
    produces STRUCTURAL nulls for precip at every timestamp only temp
    declares: temp's own 10 timestamps are hours 3-12; precip's own 2 real
    values sit at hours 4 and 5 (a SUBSET of temp's grid, chosen so the
    union's row order does not already match precip's true order)."""
    union_timestamps = _timestamps(*range(3, 13))
    return StationInputData(
        past_targets=_time_frame(
            {"timestamp": _timestamps(0, 1, 2), "discharge": [10.0, 11.0, 12.0]}
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {
                "timestamp": union_timestamps,
                "precip": [None, 5.0, 6.0, None, None, None, None, None, None, None],
                "temp": [float(i) for i in range(10)],
            }
        ),
        static=None,
    )


def test_predict_slices_a_variable_to_its_own_timestamps_not_the_union_frame() -> None:
    """Locks T8 review finding 1 (blocker): `_slice_to_future_steps` must
    take the first `future_steps` rows belonging to THIS variable, not the
    first N rows of the `future_dynamic` UNION frame. temp's longer 10-step
    horizon widens the union frame to hours 3-12; precip (future_steps=2,
    max_nan=0) only has REAL values at hours 4 and 5 -- everywhere else in
    the union it is a structural null the pivot created, never a value
    precip itself declared. Taking `head(2)` of the union's timestamp-sorted
    rows (pre-fix) picks hour 3 (structurally null for precip) and hour 4
    (real) -- dropping the real hour-5 value and reporting a spurious
    missing value that falsely trips `max_nan=0` before this test even
    reaches the InputSeries assertions below."""
    data = _staggered_two_horizon_station_input_data()
    model = RecordingFIForecastModel(
        _success_result({"station": {"discharge": _success_variable()}}),
        requirement=_two_horizon_requirement(),
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model, station_code_resolver=lambda station_id: _CODES[station_id]
    )
    inputs = StationModelInputs(
        station_id=_SID_A,
        data=data,
        issue_time=_ISSUE,
        forecast_horizon_steps=10,
        time_step=_STEP,
        forcing_route=ForcingRoute.PER_TRACK,
    )

    # Must NOT raise: precip's own two declared values (hours 4 and 5) are
    # both present, so max_nan=0 is satisfied once the slice is
    # variable-local rather than union-frame-positional.
    adapter.predict(object(), inputs, random.Random(123))

    assert model.predict_inputs is not None
    precip = _future_known_series(model.predict_inputs, "precip")
    temp = _future_known_series(model.predict_inputs, "temp")
    assert precip.height == 2
    assert precip["precip"].to_list() == [5.0, 6.0]
    assert precip["datetime"].to_list() == _timestamps(4, 5)
    assert temp.height == 10


def _short_horizon_requirement() -> fi_boundary.InputRequirement:
    """A station-scope model declaring a SINGLE future_known feature at
    `future_steps=2` — the shorter-horizon half of a co-assigned pair. The
    legacy superset assembler sizes ONE frame per station to the MAX horizon
    across co-assigned models (`services/operational_inputs.py:495`), so this
    model is handed a LONGER frame than it declared.
    """
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
                                        fi_boundary.Unit.M3_PER_S, max_nan=0
                                    ),
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precip": fi_boundary.FutureKnownVariable(
                                        future_steps=2,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
    )


def _over_delivered_station_input_data(
    precip_values: list[float] | None = None,
) -> StationInputData:
    # 5 future buckets for a model that declared 2 — the superset MAX from a
    # co-assigned longer-horizon model.
    values = [0.0, 1.0, 2.0, 3.0, 4.0] if precip_values is None else precip_values
    return StationInputData(
        past_targets=_time_frame(
            {"timestamp": _timestamps(0, 1, 2), "discharge": [10.0, 11.0, 12.0]}
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {"timestamp": _timestamps(3, 4, 5, 6, 7), "precip": values}
        ),
        static=None,
    )


def _predict_over_delivered(
    *,
    forcing_route: ForcingRoute,
    precip_values: list[float] | None = None,
    time_step: timedelta = _STEP,
    data: StationInputData | None = None,
) -> RecordingFIForecastModel:
    model = RecordingFIForecastModel(
        _success_result({"station": {"discharge": _success_variable()}}),
        requirement=_short_horizon_requirement(),
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(
        model, station_code_resolver=lambda station_id: _CODES[station_id]
    )
    inputs = StationModelInputs(
        station_id=_SID_A,
        data=data
        if data is not None
        else _over_delivered_station_input_data(precip_values),
        issue_time=_ISSUE,
        forecast_horizon_steps=5,
        time_step=time_step,
        forcing_route=forcing_route,
    )
    adapter.predict(object(), inputs, random.Random(123))
    return model


class TestForcingRouteGatesTheFutureStepsSlice:
    """Plan 151 D10: `StationModelInputs.forcing_route` is the EXPLICIT
    legacy-vs-per-track discriminant that decides whether D9's per-variable
    `future_steps` slice applies at the FI boundary. Wiring the slice
    unconditionally into the shared `predict()` route silently truncates the
    LEGACY superset route, whose over-delivery is contractual
    (`models/nwp_regression.py`: "Over-delivery (more than `_HORIZON`
    aligned steps) is tolerated and forecast in full").
    """

    def test_legacy_route_delivers_the_whole_over_delivered_frame(self) -> None:
        model = _predict_over_delivered(
            forcing_route=ForcingRoute.LEGACY_SUPERSET,
        )

        assert model.predict_inputs is not None
        precip = _future_known_series(model.predict_inputs, "precip")
        assert precip.height == 5
        assert precip["precip"].to_list() == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_per_track_route_slices_to_the_models_own_declared_steps(self) -> None:
        model = _predict_over_delivered(forcing_route=ForcingRoute.PER_TRACK)

        assert model.predict_inputs is not None
        precip = _future_known_series(model.predict_inputs, "precip")
        assert precip.height == 2
        assert precip["precip"].to_list() == [0.0, 1.0]

    def test_legacy_route_nan_gate_counts_the_whole_over_delivered_frame(self) -> None:
        # Gate-side mirror: on the legacy route a NaN BEYOND the declared
        # `future_steps` is still counted (pre-Plan-151 behaviour), because
        # the whole over-delivered frame is what gets DELIVERED there — the
        # gate covers exactly what the model receives. (What the model then
        # does with a NaN is its own business: `NwpRegression` returns
        # `ModelFailure` for delivered NaNs, `models/nwp_regression.py:372`;
        # over-delivered rows are forecast in full only when usable.)
        with pytest.raises(ModelOutputError, match="future_known.precip"):
            _predict_over_delivered(
                forcing_route=ForcingRoute.LEGACY_SUPERSET,
                precip_values=[0.0, 1.0, float("nan"), 3.0, 4.0],
            )


# The requirement above declares exactly one time step (`_STEP`, 1h). A legacy
# caller carrying any OTHER `time_step` is a misconfiguration -- but pre-T6 the
# NaN gate never looked at `time_step` at all, so the FIRST error such a caller
# saw was the input error (missing column / max_nan), raised from the gate.
_MISMATCHED_STEP = timedelta(hours=6)


class TestLegacyRouteNanGateNeverResolvesTimeStep:
    """Plan 151 D10 + the pre-T6 byte-for-byte guarantee: on
    `ForcingRoute.LEGACY_SUPERSET` the gate must use the FLATTENED tolerance
    helpers and must NOT resolve `time_step` (`_dynamic_spec_for_time_step`).

    Resolving it makes a mismatched legacy `time_step` raise
    ConfigurationError("does not declare time_step") BEFORE the pre-T6
    missing-column / max_nan error -- an error-path divergence on the
    deployed control-only route. The route, not the presence of `time_step`,
    is the discriminant.
    """

    def test_legacy_route_raises_the_nan_error_not_the_time_step_error(self) -> None:
        with pytest.raises(ModelOutputError, match="max_nan"):
            _predict_over_delivered(
                forcing_route=ForcingRoute.LEGACY_SUPERSET,
                precip_values=[0.0, 1.0, float("nan"), 3.0, 4.0],
                time_step=_MISMATCHED_STEP,
            )

    def test_legacy_route_raises_the_missing_column_error_not_the_time_step_error(
        self,
    ) -> None:
        data = StationInputData(
            past_targets=_time_frame(
                {"timestamp": _timestamps(0, 1, 2), "discharge": [10.0, 11.0, 12.0]}
            ),
            past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
            # `precip` absent entirely -- the pre-T6 first failure.
            future_dynamic=_time_frame({"timestamp": _timestamps(3, 4, 5, 6, 7)}),
            static=None,
        )

        with pytest.raises(
            ConfigurationError,
            match="missing ForecastInterface future_known input 'precip'",
        ):
            _predict_over_delivered(
                forcing_route=ForcingRoute.LEGACY_SUPERSET,
                time_step=_MISMATCHED_STEP,
                data=data,
            )

    def test_per_track_route_still_resolves_and_rejects_a_mismatched_time_step(
        self,
    ) -> None:
        # Contrast case: the per-track route DOES derive its tolerances from
        # the selected branch, so an undeclared `time_step` is a genuine
        # configuration error there.
        with pytest.raises(ConfigurationError, match="does not declare time_step"):
            _predict_over_delivered(
                forcing_route=ForcingRoute.PER_TRACK,
                precip_values=[0.0, 1.0, float("nan"), 3.0, 4.0],
                time_step=_MISMATCHED_STEP,
            )


class TestPerTrackRouteWithoutATimeStepFailsLoudly:
    """Plan 151 D10 fixer round 3: `ForcingRoute.PER_TRACK` is the EXPLICIT
    discriminant, and nothing may silently override it.

    `_variables_over_nan_tolerance` accepts `time_step: timedelta | None`, so
    a type-correct call carrying `PER_TRACK` with no `time_step` is
    representable. Gating the per-track arm on `... and time_step is not
    None` made such a call fall through to the FLATTENED legacy branch — an
    invalid, untested fallback selected silently, inverting the repo's
    fail-loud principle. `StationModelInputs.time_step` is statically
    non-optional (`types/model.py:66`) but carries no runtime validation, so
    the type system alone does not close this.

    Not flow-reachable today (`services/track_assembly.py:140,:197` always
    build a real `timedelta`); this locks the boundary so it stays that way.
    """

    def _adapter(self) -> fi_boundary.ForecastInterfaceAdapter:
        return fi_boundary.ForecastInterfaceAdapter(
            RecordingFIForecastModel(
                _success_result({"station": {"discharge": _success_variable()}}),
                requirement=_short_horizon_requirement(),
            ),
            station_code_resolver=lambda station_id: _CODES[station_id],
        )

    def test_per_track_route_without_a_time_step_raises_configuration_error(
        self,
    ) -> None:
        data = _over_delivered_station_input_data()

        with pytest.raises(ConfigurationError, match="without a time_step"):
            self._adapter()._variables_over_nan_tolerance(
                past_targets=data.past_targets,
                past_dynamic=data.past_dynamic,
                future_dynamic=data.future_dynamic,
                time_step=None,
                forcing_route=ForcingRoute.PER_TRACK,
            )

    def test_legacy_route_without_a_time_step_still_uses_the_flattened_maps(
        self,
    ) -> None:
        # Contrast case proving the raise is scoped to PER_TRACK: the legacy
        # (and GROUP) call sites pass NO `time_step` by design and must keep
        # returning the flattened result.
        data = _over_delivered_station_input_data()

        assert (
            self._adapter()._variables_over_nan_tolerance(
                past_targets=data.past_targets,
                past_dynamic=data.past_dynamic,
                future_dynamic=data.future_dynamic,
                forcing_route=ForcingRoute.LEGACY_SUPERSET,
            )
            == {}
        )
