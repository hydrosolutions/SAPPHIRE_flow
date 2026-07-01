from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelOutputError, ModelSmokeTestError
from sapphire_flow.services.model_onboarding import (
    assert_model_conforms,
    assert_operational_floors,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    ArtifactScope,
    EnsembleRepresentation,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.model import (
    GroupModelInputs,
    GroupTrainingData,
    StationInputData,
    StationModelInputs,
    StationTrainingData,
)
from tests.conftest import make_deployment_config
from tests.fakes.fake_fi_models import (
    REFERENCE_FI_HORIZON,
    REFERENCE_FI_STEP,
    ReferenceFIForecastModel,
)

_SID_A = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_SID_B = StationId(UUID("00000000-0000-0000-0000-000000000002"))
_GROUP_ID = StationGroupId(UUID("00000000-0000-0000-0000-000000000100"))
_STATION_CODES = {_SID_A: "gauge-a", _SID_B: "gauge-b"}
_ISSUE = ensure_utc(datetime(2025, 1, 1, 3, tzinfo=UTC))


def test_fi_adapter_capstone_smoke_composes_onboarding_and_prediction() -> None:
    config = make_deployment_config()
    fi_model = ReferenceFIForecastModel(
        fi_boundary.FIArtifactScope.STATION,
        member_count=20,
    )

    assert fi_boundary.is_fi_model(fi_model)
    adapter = _adapt_station(fi_model)
    assert adapter.artifact_scope is ArtifactScope.STATION
    assert adapter.data_requirements.target_parameters == frozenset({"discharge"})
    # discharge is the target → excluded from the forcing channel (past_targets).
    assert adapter.data_requirements.past_dynamic_features == frozenset()
    assert adapter.data_requirements.future_dynamic_features == frozenset(
        {"precipitation_forecast"}
    )
    assert adapter.data_requirements.supported_time_steps == frozenset(
        {REFERENCE_FI_STEP}
    )
    assert adapter.data_requirements.forecast_horizon_steps == REFERENCE_FI_HORIZON
    assert adapter.data_requirements.spatial_input_type is SpatialRepresentation.POINT

    assert_model_conforms(adapter, random.Random(101))
    assert_operational_floors(adapter, config, random.Random(202))

    low_floor_adapter = _adapt_station(ReferenceFIForecastModel(member_count=8))
    with pytest.raises(ModelSmokeTestError) as low_floor_exc:
        assert_operational_floors(low_floor_adapter, config, random.Random(303))
    assert "observed_count=8" in str(low_floor_exc.value)
    assert "required_floor=20" in str(low_floor_exc.value)

    deterministic_adapter = _adapt_station(ReferenceFIForecastModel(deterministic=True))
    with pytest.raises(ModelSmokeTestError) as deterministic_exc:
        assert_operational_floors(deterministic_adapter, config, random.Random(404))
    assert "observed_count=1" in str(deterministic_exc.value)
    assert "representation=members" in str(deterministic_exc.value)

    training_data = _station_training_data()
    artifact = adapter.train(training_data, {}, random.Random(11))
    raw_artifact = adapter.serialize_artifact(artifact)
    reloaded_artifact = adapter.deserialize_artifact(raw_artifact)
    inputs = _station_model_inputs(_SID_A, training_data)

    ensembles, state = adapter.predict(
        reloaded_artifact,
        inputs,
        random.Random(12),
    )

    assert state is None
    assert raw_artifact
    assert set(ensembles) == {"discharge"}
    discharge = ensembles["discharge"]
    assert discharge.station_id == _SID_A
    assert discharge.representation is EnsembleRepresentation.MEMBERS
    assert discharge.member_count == 20
    assert discharge.units == "m³/s"
    assert discharge.forecast_horizon_steps == REFERENCE_FI_HORIZON
    assert discharge.values.schema["valid_time"] == pl.Datetime("us", "UTC")

    failure_adapter = _adapt_station(ReferenceFIForecastModel(model_failure=True))
    failure_artifact = failure_adapter.train(training_data, {}, random.Random(13))
    with pytest.raises(ModelOutputError, match="MODEL_ERROR: boom"):
        failure_adapter.predict(failure_artifact, inputs, random.Random(14))

    group_model = ReferenceFIForecastModel(
        fi_boundary.FIArtifactScope.GROUP,
        member_count=20,
    )
    group_adapter = fi_boundary.adapt_if_fi(
        group_model,
        station_code_resolver=lambda station_id: _STATION_CODES[station_id],
    )
    assert isinstance(group_adapter, fi_boundary.ForecastInterfaceAdapter)
    assert group_adapter.artifact_scope is ArtifactScope.GROUP

    group_training_data = _group_training_data()
    group_artifact = group_adapter.train(
        group_training_data,
        {},
        random.Random(21),
    )
    group_reloaded_artifact = group_adapter.deserialize_artifact(
        group_adapter.serialize_artifact(group_artifact)
    )
    group_results = group_adapter.predict_batch(
        group_reloaded_artifact,
        _group_model_inputs(group_training_data),
        random.Random(22),
    )

    assert set(group_results) == {_SID_A, _SID_B}
    for station_id, (station_ensembles, station_state) in group_results.items():
        assert station_state is None
        assert set(station_ensembles) == {"discharge"}
        station_discharge = station_ensembles["discharge"]
        assert station_discharge.station_id == station_id
        assert station_discharge.representation is EnsembleRepresentation.MEMBERS
        assert station_discharge.member_count == 20
        assert station_discharge.units == "m³/s"


def _adapt_station(
    model: ReferenceFIForecastModel,
) -> fi_boundary.ForecastInterfaceAdapter:
    adapted = fi_boundary.adapt_if_fi(model)
    assert isinstance(adapted, fi_boundary.ForecastInterfaceAdapter)
    return adapted


def _station_training_data(offset: float = 0.0) -> StationTrainingData:
    return StationTrainingData(
        past_targets=_time_frame(
            {
                "timestamp": _timestamps(0, 1, 2),
                "discharge": [offset + 10.0, offset + 11.0, offset + 12.0],
            }
        ),
        past_dynamic=_time_frame({"timestamp": _timestamps(0, 1, 2)}),
        future_dynamic=_time_frame(
            {
                "timestamp": _timestamps(4, 5, 6),
                "precipitation_forecast": [
                    offset + 1.0,
                    offset + 2.0,
                    offset + 3.0,
                ],
            }
        ),
        static=None,
        time_step=REFERENCE_FI_STEP,
        val_start=None,
    )


def _station_model_inputs(
    station_id: StationId,
    training_data: StationTrainingData,
) -> StationModelInputs:
    return StationModelInputs(
        station_id=station_id,
        data=StationInputData(
            past_targets=training_data.past_targets,
            past_dynamic=training_data.past_dynamic,
            future_dynamic=training_data.future_dynamic,
            static=training_data.static,
        ),
        issue_time=_ISSUE,
        forecast_horizon_steps=REFERENCE_FI_HORIZON,
        time_step=REFERENCE_FI_STEP,
    )


def _group_training_data() -> GroupTrainingData:
    data_a = _station_training_data()
    data_b = _station_training_data(offset=100.0)
    return GroupTrainingData(
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
        time_step=REFERENCE_FI_STEP,
        val_start=None,
    )


def _group_model_inputs(training_data: GroupTrainingData) -> GroupModelInputs:
    return GroupModelInputs(
        group_id=training_data.group_id,
        station_ids=training_data.station_ids,
        past_targets=training_data.past_targets,
        past_dynamic=training_data.past_dynamic,
        future_dynamic=training_data.future_dynamic,
        static=training_data.static,
        issue_time=_ISSUE,
        forecast_horizon_steps=REFERENCE_FI_HORIZON,
        time_step=REFERENCE_FI_STEP,
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


def _timestamps(*hours: int) -> list[UtcDatetime]:
    return [
        ensure_utc(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=hour))
        for hour in hours
    ]


def _time_frame(data: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
