from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import forecast_interface as fi
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
from sapphire_flow.types.time_grid import ModelTimeGrid, PeriodLabel, TimeGrid

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


class PredictingRecordingFIForecastModel(RecordingFIForecastModel):
    """Everything FI's `RetrainableModel` protocol requires EXCEPT `retrain`.

    🔴 Plan 405 T3 — this class exists because of a near-miss. The protocol's members
    are `artifact_scope`, `deserialize_artifact`, `input_requirement`, `predict`,
    `retrain`, `serialize_artifact`, `train` — and `RecordingFIForecastModel` has no
    `predict`. A refusal test built on it would have raised `ConfigurationError` for
    the WRONG REASON (a missing `predict`, not a missing `retrain`) and looked
    identical to a passing one. ⇒ The refusal case must satisfy every member but the
    one under test."""

    def predict(
        self,
        artifact: object,
        *,
        inputs: fi_boundary.ModelInputs,
        issue_datetime: object,
        rng: random.Random,
    ) -> object:
        raise NotImplementedError("not exercised: these tests never predict")


class RetrainRecordingFIForecastModel(PredictingRecordingFIForecastModel):
    """The same surface WITH `retrain`, recording the call, so the adapter's
    `retrain` is asserted on DELEGATION rather than on method presence."""

    def __init__(
        self,
        input_requirement: fi_boundary.InputRequirement,
        artifact_scope: fi_boundary.FIArtifactScope = fi_boundary.FIArtifactScope.GROUP,
    ) -> None:
        super().__init__(input_requirement, artifact_scope)
        self.retrain_calls: list[dict[str, object]] = []
        self.retrained_artifact = object()

    def retrain(
        self,
        base_artifact: object,
        inputs: fi_boundary.ModelInputs,
        *,
        config: object,
        rng: random.Random,
    ) -> object:
        self.retrain_calls.append(
            {
                "base_artifact": base_artifact,
                "inputs": inputs,
                "config": config,
                "rng": rng,
            }
        )
        return self.retrained_artifact


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
    assert discharge.data.columns == ["datetime", "discharge", "available_at_us"]
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


def test_adapter_retrain_delivers_the_donor_and_a_non_default_config() -> None:
    """Plan 405 T3 — `ForecastInterfaceAdapter.retrain`, which no test touched.

    Measured before writing this: with the adapter's `retrain` body replaced by
    `raise AssertionError`, the selection
    (`tests/unit/services/test_training.py tests/unit/flows/test_train_models.py
    tests/unit/adapters/ tests/unit/models/`) still passed — 883 tests, 0 failures.
    """
    fake = RetrainRecordingFIForecastModel(
        _requirement(),
        artifact_scope=fi_boundary.FIArtifactScope.STATION,
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(fake)
    rng = random.Random(7)
    donor = object()
    config = {"finetuning": {"strategy": "last_layer", "lr": 0.0001}}

    produced = adapter.retrain(
        donor,  # type: ignore[arg-type]
        _station_training_data(static=None),
        config,  # type: ignore[arg-type]
        rng,
    )

    assert produced is fake.retrained_artifact
    assert len(fake.retrain_calls) == 1
    call = fake.retrain_calls[0]
    # 🔑 The donor object itself, and the caller's config byte-identical — a
    # hardcoded `{}` (which every onboarding site still passes) would fail here.
    assert call["base_artifact"] is donor
    assert call["config"] == config
    assert call["rng"] is rng
    # …and the data was CONVERTED to FI inputs, not handed over raw. ⚠️ Asserted on
    # CONTENT, not just the type: `isinstance(..., ModelInputs)` alone would hold for
    # an empty conversion, and a reviewer was right that it read stronger than it was.
    converted = call["inputs"]
    assert isinstance(converted, fi_boundary.ModelInputs)
    assert set(converted.stations) == {fi_boundary._STATION_SCOPE_KEY}  # noqa: SLF001
    [dynamic] = converted.stations[fi_boundary._STATION_SCOPE_KEY].dynamic.values()  # noqa: SLF001
    assert dynamic.data, "the converted inputs carry no dynamic data at all"


def test_adapter_retrain_refuses_a_wrapped_model_that_cannot_retrain() -> None:
    """The library-level backstop: FI's own `RetrainableModel` contract.

    ⛔ Not a fall-back to `train` — Plan 399 D2. The service layer refuses first;
    this is what holds if a caller reaches the adapter directly.
    """
    fake = PredictingRecordingFIForecastModel(
        _requirement(),
        artifact_scope=fi_boundary.FIArtifactScope.STATION,
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(fake)
    # 🔴 The refusal must be about `retrain` ALONE: this fake satisfies every other
    # member of FI's protocol, so the isinstance check can fail for one reason only.
    assert not hasattr(fake, "retrain")
    for member in (
        "artifact_scope",
        "deserialize_artifact",
        "input_requirement",
        "predict",
        "serialize_artifact",
        "train",
    ):
        assert hasattr(fake, member), (
            f"fake is missing {member}, so the refusal below could fire for that "
            "instead of for the missing retrain"
        )

    with pytest.raises(ConfigurationError, match="RetrainableModel"):
        adapter.retrain(
            object(),  # type: ignore[arg-type]
            _station_training_data(static=None),
            {},  # type: ignore[arg-type]
            random.Random(0),
        )


class GridRecordingModel(PredictingRecordingFIForecastModel):
    def __init__(self, grid: fi.GridDeclaration) -> None:
        requirement = _requirement(timedelta(days=1))
        requirement.dynamic[timedelta(days=1)] = requirement.dynamic[
            timedelta(days=1)
        ].model_copy(
            update={
                "anchor": fi.TimeAnchor(
                    origin=timedelta(hours=18), label=fi.PeriodLabel.START
                )
            }
        )
        super().__init__(requirement, fi.ArtifactScope.STATION)
        self.loaded_artifact = GridArtifact(grid)

    def deserialize_artifact(self, raw: bytes) -> object:
        return self.loaded_artifact

    def predict(
        self,
        artifact: object,
        *,
        inputs: fi.ModelInputs,
        issue_datetime: object,
        rng: random.Random,
    ) -> fi.ModelResult:
        raise AssertionError("incompatible input must not reach prediction")

    def retrain(
        self,
        base_artifact: object,
        inputs: fi.ModelInputs,
        *,
        config: object,
        rng: random.Random,
    ) -> object:
        raise AssertionError("incompatible artifact must not reach retraining")


class GridArtifact:
    def __init__(self, grid: fi.GridDeclaration) -> None:
        self.training_grid = fi.TrainingGridDeclaration(grid=grid)


_DECLARED_GRID = ModelTimeGrid(
    grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
    label=PeriodLabel.START,
)
_FI_GRID = fi.TimeGrid(timedelta(days=1), timedelta(hours=18), fi.PeriodLabel.START)


def _declared_grid_inputs() -> StationModelInputs:
    source = _station_input_data(static=_static_frame())

    def daily(frame: pl.DataFrame) -> pl.DataFrame:
        epoch = datetime(2025, 1, 1, tzinfo=UTC)
        timestamps = [
            epoch + timedelta(hours=18, days=(stamp - epoch).total_seconds() / 3600)
            for stamp in frame["timestamp"].to_list()
        ]
        return frame.with_columns(pl.Series("timestamp", timestamps))

    return StationModelInputs(
        station_id=_SID_A,
        data=StationInputData(
            past_targets=daily(source.past_targets),
            past_dynamic=daily(source.past_dynamic),
            future_dynamic=daily(source.future_dynamic),
            static=source.static,
        ),
        issue_time=ensure_utc(datetime(2025, 1, 5, tzinfo=UTC)),
        forecast_horizon_steps=3,
        time_step=timedelta(days=1),
        time_grid=_DECLARED_GRID,
    )


class TestDeclaredTimeGrid:
    @pytest.mark.parametrize(
        "anchor",
        [
            fi.TimeAnchor(origin=timedelta(hours=18)),
            fi.TimeAnchor(label=fi.PeriodLabel.START),
        ],
    )
    def test_partial_anchor_is_not_treated_as_legacy(
        self, anchor: fi.TimeAnchor
    ) -> None:
        model = GridRecordingModel(_FI_GRID)
        step = timedelta(days=1)
        model.input_requirement.dynamic[step] = model.input_requirement.dynamic[
            step
        ].model_copy(update={"anchor": anchor})
        with pytest.raises(UnsupportedModelRequirementError, match="both a known"):
            fi_boundary.ForecastInterfaceAdapter(model)

    def test_projects_anchor_and_delivers_grid_without_relabelling(self) -> None:
        adapter = fi_boundary.ForecastInterfaceAdapter(GridRecordingModel(_FI_GRID))
        inputs = _declared_grid_inputs()
        delivered = adapter.evidence_inputs(inputs)
        assert adapter.data_requirements.time_grid == _DECLARED_GRID
        assert delivered.grid == _FI_GRID
        series = (
            delivered.stations["station"]
            .dynamic[timedelta(days=1)]
            .data[fi.SpatialRepresentation.POINT]
            .past_known["obs"]["discharge"]
        )
        assert series.data["datetime"].to_list() == sorted(
            inputs.data.past_targets["timestamp"].to_list()
        )

    @pytest.mark.parametrize(
        "grid",
        [
            None,
            ModelTimeGrid(
                grid=TimeGrid(step=timedelta(days=1), phase=timedelta(0)),
                label=PeriodLabel.START,
            ),
            replace(_DECLARED_GRID, label=PeriodLabel.END),
        ],
    )
    def test_refuses_missing_or_mismatched_delivered_grid(
        self, grid: ModelTimeGrid | None
    ) -> None:
        adapter = fi_boundary.ForecastInterfaceAdapter(GridRecordingModel(_FI_GRID))
        with pytest.raises(ConfigurationError, match="delivered time grid differs"):
            adapter.evidence_inputs(replace(_declared_grid_inputs(), time_grid=grid))

    def test_refuses_off_grid_series_even_with_matching_declaration(self) -> None:
        adapter = fi_boundary.ForecastInterfaceAdapter(GridRecordingModel(_FI_GRID))
        inputs = _declared_grid_inputs()
        shifted = inputs.data.past_targets.with_columns(
            pl.col("timestamp") + timedelta(minutes=15)
        )
        with pytest.raises(ValueError, match="grid"):
            adapter.evidence_inputs(
                replace(inputs, data=replace(inputs.data, past_targets=shifted))
            )

    def test_accepts_matching_artifact_grid(self) -> None:
        model = GridRecordingModel(_FI_GRID)
        adapter = fi_boundary.ForecastInterfaceAdapter(model)
        assert adapter.deserialize_artifact(b"checkpoint") is model.loaded_artifact

    @pytest.mark.parametrize(
        "grid",
        [
            fi.Unknown.UNKNOWN,
            fi.TimeGrid(timedelta(days=1), timedelta(0), fi.PeriodLabel.START),
            fi.TimeGrid(timedelta(days=1), timedelta(hours=18), fi.PeriodLabel.END),
        ],
    )
    def test_refuses_unknown_or_mismatched_artifact_at_load_and_predict(
        self, grid: fi.GridDeclaration
    ) -> None:
        model = GridRecordingModel(grid)
        adapter = fi_boundary.ForecastInterfaceAdapter(model)
        with pytest.raises(ConfigurationError, match="artifact training grid"):
            adapter.deserialize_artifact(b"checkpoint")
        with pytest.raises(ConfigurationError, match="artifact training grid"):
            adapter.predict(
                model.loaded_artifact, _declared_grid_inputs(), random.Random(0)
            )
        inputs = _declared_grid_inputs()
        training = StationTrainingData(
            past_targets=inputs.data.past_targets,
            past_dynamic=inputs.data.past_dynamic,
            future_dynamic=inputs.data.future_dynamic,
            static=inputs.data.static,
            time_step=inputs.time_step,
            val_start=None,
            time_grid=inputs.time_grid,
        )
        with pytest.raises(ConfigurationError, match="artifact training grid"):
            adapter.retrain(model.loaded_artifact, training, {}, random.Random(0))

    def test_group_delivery_preserves_grid(self) -> None:
        model = GridRecordingModel(_FI_GRID)
        model.artifact_scope = fi.ArtifactScope.GROUP
        adapter = fi_boundary.ForecastInterfaceAdapter(
            model, station_code_resolver=lambda station_id: str(station_id)
        )
        inputs = _declared_grid_inputs()

        def stack(frame: pl.DataFrame) -> pl.DataFrame:
            return frame.with_columns(pl.lit(str(_SID_A)).alias("station_id"))

        group = GroupModelInputs(
            group_id=_GROUP_ID,
            station_ids=(_SID_A,),
            past_targets=stack(inputs.data.past_targets),
            past_dynamic=stack(inputs.data.past_dynamic),
            future_dynamic=stack(inputs.data.future_dynamic),
            static=stack(inputs.data.static)
            if inputs.data.static is not None
            else None,
            issue_time=inputs.issue_time,
            forecast_horizon_steps=inputs.forecast_horizon_steps,
            time_step=inputs.time_step,
            time_grid=inputs.time_grid,
        )
        assert adapter.evidence_inputs(group).grid == _FI_GRID


class TestInputAvailability:
    @pytest.mark.parametrize("mode", ["absent", "null", "known"])
    @pytest.mark.parametrize("group", [False, True])
    def test_past_availability_is_variable_specific_and_sorted(
        self, mode: str, group: bool
    ) -> None:
        adapter = _adapter()
        inputs = _group_model_inputs() if group else _station_model_inputs()
        frame = inputs.past_targets if group else inputs.data.past_targets
        if mode != "absent":
            expression = (
                pl.col("timestamp").dt.epoch("us") + 123456
                if mode == "known"
                else pl.lit(None)
            )
            frame = frame.with_columns(expression.alias("available_at_us:discharge"))
        inputs = (
            replace(inputs, past_targets=frame)
            if group
            else replace(inputs, data=replace(inputs.data, past_targets=frame))
        )
        delivered = adapter.evidence_inputs(inputs)
        for station in delivered.stations.values():
            dynamic = station.dynamic[_STEP].data[
                fi_boundary.FISpatialRepresentation.POINT
            ]
            discharge = dynamic.past_known["obs"]["discharge"].data
            assert discharge.schema["available_at_us"] == pl.Int64
            if mode == "known":
                assert (
                    discharge["available_at_us"].to_list()
                    == (discharge["datetime"].dt.epoch("us") + 123456).to_list()
                )
            else:
                assert discharge["available_at_us"].null_count() == discharge.height
            assert (
                dynamic.past_known["obs"]["air_temperature"]
                .data["available_at_us"]
                .null_count()
                == 3
            )
            assert (
                "available_at_us"
                not in dynamic.future_known["nwp"][
                    "precipitation_forecast"
                ].data.columns
            )

    def test_rejects_noninteger_availability(self) -> None:
        inputs = _station_model_inputs()
        data = replace(
            inputs.data,
            past_targets=inputs.data.past_targets.with_columns(
                pl.lit(1.5).alias("available_at_us:discharge")
            ),
        )
        with pytest.raises(ConfigurationError, match="integer UTC epoch microseconds"):
            _adapter().evidence_inputs(replace(inputs, data=data))


def test_training_preserves_known_and_unknown_availability() -> None:
    model = RecordingFIForecastModel(
        _requirement(), artifact_scope=fi_boundary.FIArtifactScope.STATION
    )
    adapter = fi_boundary.ForecastInterfaceAdapter(model)
    data = _station_training_data(static=_static_frame())
    data = replace(
        data,
        past_targets=data.past_targets.with_columns(
            pl.Series("available_at_us:discharge", [100, None, 200], dtype=pl.Int64)
        ),
    )
    adapter.train(data, {}, random.Random(0))
    assert model.train_inputs is not None
    station = next(iter(model.train_inputs.stations.values()))
    series = (
        station.dynamic[_STEP]
        .data[fi_boundary.FISpatialRepresentation.POINT]
        .past_known["obs"]["discharge"]
    )
    assert series.data["available_at_us"].to_list() == [None, 200, 100]
