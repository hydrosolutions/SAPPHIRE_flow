from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import (
    ConfigurationError,
    UnsupportedModelRequirementError,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.model import (
    GroupModelInputs,
    GroupTrainingData,
    StationInputData,
    StationModelInputs,
    StationTrainingData,
)

_STEP = timedelta(hours=1)
_ALT_STEP = timedelta(hours=6)
_ISSUE = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
_SID_A = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_SID_B = StationId(UUID("00000000-0000-0000-0000-000000000002"))
_GROUP_ID = StationGroupId(UUID("00000000-0000-0000-0000-000000000100"))


class RecordingFIForecastModel:
    def __init__(
        self,
        input_requirement: fi_boundary.InputRequirement,
        artifact_scope: fi_boundary.FIArtifactScope = fi_boundary.FIArtifactScope.GROUP,
    ) -> None:
        self._input_requirement = input_requirement
        self.artifact_scope = artifact_scope
        self.artifact = object()
        self.train_inputs: fi_boundary.ModelInputs | None = None
        self.train_config: object | None = None
        self.train_rng: random.Random | None = None

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
        self.train_inputs = inputs
        self.train_config = config
        self.train_rng = rng
        return self.artifact

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


def _requirement(time_step: timedelta = _STEP) -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={"discharge": _target(fi_boundary.Unit.M3_PER_S)},
        dynamic={
            time_step: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": _past(fi_boundary.Unit.M3_PER_S),
                                    "air_temperature": _past(fi_boundary.Unit.DEG_C),
                                },
                                "forcing": {
                                    "precipitation": _past(fi_boundary.Unit.MM),
                                },
                            },
                            future_known={
                                "nwp": {
                                    "precipitation_forecast": _future(
                                        fi_boundary.Unit.MM
                                    ),
                                    "temperature_forecast": _future(
                                        fi_boundary.Unit.DEG_C
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
        static={"catchment_area", "elevation", "name"},
    )


def _timestamps(*hours: int) -> list[UtcDatetime]:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    return [ensure_utc(base + timedelta(hours=hour)) for hour in hours]


def _time_frame(data: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _static_frame(
    *,
    catchment_area: float = 100.5,
    elevation: int = 450,
    name: str = "station-a",
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "catchment_area": [catchment_area],
            "elevation": [elevation],
            "name": [name],
            "unused_static": ["ignored"],
        }
    )


def _station_input_data(
    *,
    offset: float = 0.0,
    static: pl.DataFrame | None,
) -> StationInputData:
    issue_column = [_ISSUE, _ISSUE, _ISSUE]
    past_ts = _timestamps(2, 0, 1)
    future_ts = _timestamps(5, 3, 4)
    return StationInputData(
        past_targets=_time_frame(
            {
                "timestamp": past_ts,
                "discharge": [offset + 12.0, offset + 10.0, offset + 11.0],
                "issue_datetime": issue_column,
            }
        ),
        past_dynamic=_time_frame(
            {
                "timestamp": past_ts,
                "air_temperature": [offset + 2.0, offset + 0.0, offset + 1.0],
                "precipitation": [offset + 3.0, offset + 1.0, offset + 2.0],
                "issue_datetime": issue_column,
            }
        ),
        future_dynamic=_time_frame(
            {
                "timestamp": future_ts,
                "precipitation_forecast": [
                    offset + 6.0,
                    offset + 4.0,
                    offset + 5.0,
                ],
                "temperature_forecast": [
                    offset + 9.0,
                    offset + 7.0,
                    offset + 8.0,
                ],
                "issue_datetime": issue_column,
            }
        ),
        static=static,
    )


def _station_model_inputs(
    *,
    data: StationInputData | None = None,
    time_step: timedelta = _STEP,
) -> StationModelInputs:
    return StationModelInputs(
        station_id=_SID_A,
        data=data or _station_input_data(static=_static_frame()),
        issue_time=_ISSUE,
        forecast_horizon_steps=3,
        time_step=time_step,
    )


def _station_training_data(
    *,
    static: pl.DataFrame | None = None,
    time_step: timedelta = _STEP,
) -> StationTrainingData:
    data = _station_input_data(static=static)
    return StationTrainingData(
        past_targets=data.past_targets,
        past_dynamic=data.past_dynamic,
        future_dynamic=data.future_dynamic,
        static=data.static,
        time_step=time_step,
        val_start=None,
    )


def _stack_by_station(frames: dict[StationId, pl.DataFrame]) -> pl.DataFrame:
    parts = [
        frame.with_columns(pl.lit(str(station_id)).alias("station_id")).select(
            ["station_id", *frame.columns]
        )
        for station_id, frame in frames.items()
    ]
    return pl.concat(parts)


def _group_model_inputs() -> GroupModelInputs:
    data_a = _station_input_data(static=_static_frame(name="station-a"))
    data_b = _station_input_data(
        offset=100.0,
        static=_static_frame(catchment_area=200.5, elevation=550, name="station-b"),
    )
    assert data_a.static is not None
    assert data_b.static is not None
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
        static=_stack_by_station({_SID_A: data_a.static, _SID_B: data_b.static}),
        issue_time=_ISSUE,
        forecast_horizon_steps=3,
        time_step=_STEP,
    )


def _group_training_data() -> GroupTrainingData:
    inputs = _group_model_inputs()
    return GroupTrainingData(
        group_id=inputs.group_id,
        station_ids=inputs.station_ids,
        past_targets=inputs.past_targets,
        past_dynamic=inputs.past_dynamic,
        future_dynamic=inputs.future_dynamic,
        static=inputs.static,
        time_step=inputs.time_step,
        val_start=None,
    )


def _adapter(
    *,
    requirement: fi_boundary.InputRequirement | None = None,
) -> fi_boundary.ForecastInterfaceAdapter:
    codes = {_SID_A: "AARE", _SID_B: "RHINE"}
    return fi_boundary.ForecastInterfaceAdapter(
        RecordingFIForecastModel(requirement or _requirement()),
        station_code_resolver=lambda station_id: codes[station_id],
    )


def test_station_model_inputs_convert_with_fixed_key_without_resolver() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        RecordingFIForecastModel(
            _requirement(),
            artifact_scope=fi_boundary.FIArtifactScope.STATION,
        )
    )
    model_inputs = adapter._model_inputs_from_data(_station_model_inputs())

    assert set(model_inputs.stations) == {fi_boundary._STATION_SCOPE_KEY}
    assert str(_SID_A) not in model_inputs.stations

    station = model_inputs.stations[fi_boundary._STATION_SCOPE_KEY]
    assert station.static == {
        "catchment_area": 100.5,
        "elevation": 450,
        "name": "station-a",
    }

    dynamic = station.dynamic[_STEP].data[fi_boundary.FISpatialRepresentation.POINT]
    discharge = dynamic.past_known["obs"]["discharge"]
    assert discharge.unit == fi_boundary.Unit.M3_PER_S
    assert discharge.data.columns == ["datetime", "discharge"]
    assert isinstance(discharge.data.schema["datetime"], pl.Datetime)
    assert "issue_datetime" not in discharge.data.columns
    assert discharge.data["datetime"].n_unique() == discharge.data.height
    assert discharge.data["datetime"].to_list() == sorted(
        discharge.data["datetime"].to_list()
    )
    assert discharge.data["discharge"].to_list() == [10.0, 11.0, 12.0]

    temperature = dynamic.past_known["obs"]["air_temperature"]
    assert temperature.unit == fi_boundary.Unit.DEG_C
    assert temperature.data["air_temperature"].to_list() == [0.0, 1.0, 2.0]

    precipitation = dynamic.past_known["forcing"]["precipitation"]
    assert precipitation.unit == fi_boundary.Unit.MM
    assert precipitation.data["precipitation"].to_list() == [1.0, 2.0, 3.0]

    future_precipitation = dynamic.future_known["nwp"]["precipitation_forecast"]
    assert future_precipitation.unit == fi_boundary.Unit.MM
    assert future_precipitation.data.columns == [
        "datetime",
        "precipitation_forecast",
    ]
    assert future_precipitation.data["precipitation_forecast"].to_list() == [
        4.0,
        5.0,
        6.0,
    ]


def test_group_model_inputs_convert_one_station_entry_per_station() -> None:
    model_inputs = _adapter()._model_inputs_from_data(_group_model_inputs())

    assert set(model_inputs.stations) == {"AARE", "RHINE"}
    assert str(_SID_A) not in model_inputs.stations
    assert str(_SID_B) not in model_inputs.stations

    station_b = model_inputs.stations["RHINE"]
    dynamic_b = station_b.dynamic[_STEP].data[fi_boundary.FISpatialRepresentation.POINT]
    discharge_b = dynamic_b.past_known["obs"]["discharge"]
    assert discharge_b.data["discharge"].to_list() == [110.0, 111.0, 112.0]
    assert station_b.static == {
        "catchment_area": 200.5,
        "elevation": 550,
        "name": "station-b",
    }


def test_station_train_converts_with_fixed_key_without_resolver() -> None:
    fake = RecordingFIForecastModel(
        _requirement(),
        artifact_scope=fi_boundary.FIArtifactScope.STATION,
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(
        fake,
    )
    rng = random.Random(123)

    artifact = adapter.train(_station_training_data(static=None), {}, rng)

    assert artifact is fake.artifact
    assert fake.train_inputs is not None
    assert set(fake.train_inputs.stations) == {fi_boundary._STATION_SCOPE_KEY}
    assert fake.train_inputs.stations[fi_boundary._STATION_SCOPE_KEY].static == {}


def test_train_time_group_data_converts_via_for_station() -> None:
    model_inputs = _adapter()._model_inputs_from_data(_group_training_data())

    assert set(model_inputs.stations) == {"AARE", "RHINE"}
    station_a = model_inputs.stations["AARE"]
    dynamic_a = station_a.dynamic[_STEP].data[fi_boundary.FISpatialRepresentation.POINT]
    assert dynamic_a.future_known["nwp"]["temperature_forecast"].data[
        "temperature_forecast"
    ].to_list() == [7.0, 8.0, 9.0]


def test_time_step_not_declared_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="does not declare time_step"):
        _adapter()._model_inputs_from_data(_station_model_inputs(time_step=_ALT_STEP))


def test_missing_declared_dynamic_column_raises_configuration_error() -> None:
    data = _station_input_data(static=_static_frame())
    bad_data = StationInputData(
        past_targets=data.past_targets,
        past_dynamic=data.past_dynamic.drop("precipitation"),
        future_dynamic=data.future_dynamic,
        static=data.static,
    )

    with pytest.raises(
        ConfigurationError,
        match="missing ForecastInterface past_known input 'precipitation'",
    ):
        _adapter()._model_inputs_from_data(_station_model_inputs(data=bad_data))


def test_missing_declared_static_column_raises_configuration_error() -> None:
    data = _station_input_data(
        static=pl.DataFrame({"catchment_area": [100.5], "name": ["station-a"]})
    )

    with pytest.raises(
        ConfigurationError,
        match="missing ForecastInterface static input",
    ):
        _adapter()._model_inputs_from_data(_station_model_inputs(data=data))


def test_group_conversion_without_resolver_raises_configuration_error() -> None:
    adapter = fi_boundary.ForecastInterfaceAdapter(
        RecordingFIForecastModel(_requirement())
    )

    with pytest.raises(ConfigurationError, match="station_code_resolver required"):
        adapter._model_inputs_from_data(_group_model_inputs())

    with pytest.raises(ConfigurationError, match="station_code_resolver required"):
        adapter._model_inputs_from_data(_group_training_data())


def test_train_delegates_converted_inputs_config_rng_and_returns_artifact() -> None:
    fake = RecordingFIForecastModel(_requirement())
    adapter = fi_boundary.ForecastInterfaceAdapter(
        fake,
        station_code_resolver=lambda station_id: {
            _SID_A: "AARE",
            _SID_B: "RHINE",
        }[station_id],
    )
    params = {"learning_rate": 0.1}
    rng = random.Random(123)

    artifact = adapter.train(_group_training_data(), params, rng)

    assert artifact is fake.artifact
    assert fake.train_inputs is not None
    assert set(fake.train_inputs.stations) == {"AARE", "RHINE"}
    assert fake.train_config is params
    assert fake.train_rng is rng


def _one_future_forced_plus_past_only_requirement() -> fi_boundary.InputRequirement:
    """1h future-forced branch (`_requirement()`'s shape) plus a 24h
    past-only branch declaring ``soil_moisture`` — the shape Plan 156
    ACCEPTS at adapter construction (Plan 151 T2 needs it constructible),
    but that a review found is silently truncated at predict/train time."""
    base = _requirement()
    daily_spec = fi_boundary.DynamicInputSpec(
        past_known={"era5": {"soil_moisture": _past(fi_boundary.Unit.PERCENT)}}
    )
    return fi_boundary.InputRequirement(
        targets=base.targets,
        dynamic={
            **base.dynamic,
            timedelta(hours=24): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: daily_spec}
            ),
        },
        static=base.static,
    )


def test_past_only_second_branch_rejected_at_every_delivery_entry_point() -> None:
    """BLOCKER follow-up (Plan 156 review): before this guard, the 24h
    branch's ``soil_moisture`` was fetched into ``past_dynamic`` and
    NaN-checked (both flatten across ALL branches at projection time) yet
    silently OMITTED from what the model actually received — because
    `_station_inputs_from_frames` builds only ONE `StationInputs.dynamic`
    entry, the active 1h branch. That is an incomplete input producing a
    plausible but wrong result, exactly what Plan 156 exists to prevent.

    SECOND review round (2026-08-13): the original version of this test was
    named ``..._at_predict_time`` but only called ``train()`` — the one entry
    point where the guard's position happened to work. ``predict()`` and
    ``predict_batch()`` ran the flattened NaN gate FIRST, so they surfaced a
    misleading ``ModelOutputError``/``ConfigurationError`` instead. All three
    entry points are now asserted, and the model must never be invoked.
    """
    model = RecordingFIForecastModel(_one_future_forced_plus_past_only_requirement())
    adapter = fi_boundary.ForecastInterfaceAdapter(model)
    # Sanity: the requirement really is accepted at construction and really
    # does claim soil_moisture as a past feature — proving the omission
    # would otherwise be silent (a claimed-but-undelivered feature), not an
    # upfront rejection.
    assert "soil_moisture" in adapter.data_requirements.past_dynamic_features

    with pytest.raises(UnsupportedModelRequirementError, match="time_step branch"):
        adapter.train(_station_training_data(static=None), {}, random.Random(1))

    # predict() — a STATION-scoped adapter, since predict() rejects GROUP up
    # front. The supplied frames carry ONLY the active 1h branch's variables
    # (no `soil_moisture`), which is precisely the case that previously
    # surfaced as ConfigurationError("missing ... soil_moisture") from the
    # flattened NaN gate, because that gate ran BEFORE the guard.
    station_model = RecordingFIForecastModel(
        _one_future_forced_plus_past_only_requirement(),
        artifact_scope=fi_boundary.FIArtifactScope.STATION,
    )
    station_adapter = fi_boundary.ForecastInterfaceAdapter(station_model)
    with pytest.raises(UnsupportedModelRequirementError, match="time_step branch"):
        station_adapter.predict(b"artifact", _station_model_inputs(), random.Random(1))

    # predict_batch() — the GROUP path, and the entry point Plan 152's
    # aquacast model will actually use. Added after an independent check
    # (2026-08-13) caught that this test CLAIMED to cover "every delivery
    # entry point" while asserting only two of three — the same
    # name-promises-more-than-body defect that let the original blocker
    # through.
    group_adapter = adapter.with_station_code_resolver(
        lambda sid: {_SID_A: "AARE", _SID_B: "RHINE"}[sid]
    )
    with pytest.raises(UnsupportedModelRequirementError, match="time_step branch"):
        group_adapter.predict_batch(
            b"artifact", _group_model_inputs(), random.Random(1)
        )

    # The fake implements no `predict`, so reaching the model at all would
    # raise AttributeError rather than the guard's error — the assertions
    # above therefore also prove the model was never invoked.
