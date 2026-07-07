from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from sapphire_flow.exceptions import InsufficientObservationsError
from sapphire_flow.models.persistence_fallback import (
    PersistenceArtifact,
    PersistenceFallbackModel,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.model import (
    StationInputData,
    StationModelInputs,
    StationTrainingData,
)

_STEP = timedelta(days=1)
_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG = random.Random(42)
_STATION_ID = StationId("test_station")
_LAST_VALUE = 100.0


@pytest.fixture
def model() -> PersistenceFallbackModel:
    return PersistenceFallbackModel()


@pytest.fixture
def artifact() -> PersistenceArtifact:
    return PersistenceArtifact(target_parameters=frozenset({"discharge"}))


@pytest.fixture
def training_data() -> StationTrainingData:
    base = datetime(2020, 1, 1, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(5)]
    past_targets = pl.DataFrame({"timestamp": ts, "discharge": [10.0] * 5})
    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=pl.DataFrame({"timestamp": ts}),
        future_dynamic=pl.DataFrame({"timestamp": ts}),
        static=None,
        time_step=_STEP,
        val_start=None,
    )


@pytest.fixture
def predict_inputs() -> StationModelInputs:
    base = datetime(2020, 2, 1, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(3)]
    past_targets = pl.DataFrame(
        {"timestamp": ts, "discharge": [80.0, 90.0, _LAST_VALUE]}
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=past_targets,
            past_dynamic=pl.DataFrame({"timestamp": ts}),
            future_dynamic=pl.DataFrame({"timestamp": ts}),
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=5,
        time_step=_STEP,
    )


class TestTrain:
    def test_forecast_horizon_declared(self) -> None:
        assert PersistenceFallbackModel().data_requirements.forecast_horizon_steps == 5

    def test_returns_persistence_artifact(
        self, model: PersistenceFallbackModel, training_data: StationTrainingData
    ) -> None:
        result = model.train(training_data, {}, _RNG)
        assert isinstance(result, PersistenceArtifact)

    def test_artifact_has_correct_target_parameters(
        self, model: PersistenceFallbackModel, training_data: StationTrainingData
    ) -> None:
        result = model.train(training_data, {}, _RNG)
        assert isinstance(result, PersistenceArtifact)
        assert result.target_parameters == frozenset({"discharge"})

    def test_custom_target_parameters(self, training_data: StationTrainingData) -> None:
        m = PersistenceFallbackModel(
            target_parameters=frozenset({"discharge", "water_level"})
        )
        base = datetime(2020, 1, 1, tzinfo=UTC)
        ts = [ensure_utc(base + timedelta(days=i)) for i in range(5)]
        td = StationTrainingData(
            past_targets=pl.DataFrame(
                {"timestamp": ts, "discharge": [1.0] * 5, "water_level": [2.0] * 5}
            ),
            past_dynamic=pl.DataFrame({"timestamp": ts}),
            future_dynamic=pl.DataFrame({"timestamp": ts}),
            static=None,
            time_step=_STEP,
            val_start=None,
        )
        result = m.train(td, {}, _RNG)
        assert isinstance(result, PersistenceArtifact)
        assert result.target_parameters == frozenset({"discharge", "water_level"})


class TestPredict:
    def test_returns_ensemble_for_each_parameter(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        result, state = model.predict(artifact, predict_inputs, _RNG)
        assert state is None
        assert "discharge" in result

    def test_median_equals_last_observation(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        result, _ = model.predict(artifact, predict_inputs, _RNG)
        ensemble = result["discharge"]
        medians = (
            ensemble.values.filter(pl.col("quantile") == 0.5)
            .sort("valid_time")["value"]
            .to_list()
        )
        assert all(abs(v - _LAST_VALUE) < 1e-9 for v in medians)

    def test_spread_widens_with_lead_time(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        result, _ = model.predict(artifact, predict_inputs, _RNG)
        ensemble = result["discharge"]
        sorted_times = ensemble.values["valid_time"].unique().sort().to_list()

        def spread_at(t: object) -> float:
            sub = ensemble.values.filter(pl.col("valid_time") == t)
            return float(sub["value"].max() - sub["value"].min())  # type: ignore[arg-type]

        spreads = [spread_at(t) for t in sorted_times]
        assert spreads == sorted(spreads)
        assert spreads[0] < spreads[-1]

    def test_spread_at_step1_is_5pct(
        self,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        model = PersistenceFallbackModel(spread_pct_per_step=0.05)
        result, _ = model.predict(artifact, predict_inputs, _RNG)
        ensemble = result["discharge"]
        first_time = ensemble.values["valid_time"].min()
        sub = ensemble.values.filter(pl.col("valid_time") == first_time)
        q05 = float(sub.filter(pl.col("quantile") == 0.05)["value"][0])
        q95 = float(sub.filter(pl.col("quantile") == 0.95)["value"][0])
        # spread at step 1 = abs(100.0) * 0.05 * 1 = 5.0
        # q05 = 100 + 5 * (2*0.05 - 1) = 100 - 4.5 = 95.5
        # q95 = 100 + 5 * (2*0.95 - 1) = 100 + 4.5 = 104.5
        expected_spread = abs(_LAST_VALUE) * 0.05 * 1
        assert abs(q95 - q05 - 2 * 0.9 * expected_spread) < 1e-9

    def test_spread_at_step5_is_25pct(
        self,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        model = PersistenceFallbackModel(spread_pct_per_step=0.05)
        result, _ = model.predict(artifact, predict_inputs, _RNG)
        ensemble = result["discharge"]
        last_time = ensemble.values["valid_time"].max()
        sub = ensemble.values.filter(pl.col("valid_time") == last_time)
        q50 = float(sub.filter(pl.col("quantile") == 0.50)["value"][0])
        # median at any step == last_value
        assert abs(q50 - _LAST_VALUE) < 1e-9
        # spread = abs(100) * 0.05 * 5 = 25
        q05 = float(sub.filter(pl.col("quantile") == 0.05)["value"][0])
        q95 = float(sub.filter(pl.col("quantile") == 0.95)["value"][0])
        expected_spread = abs(_LAST_VALUE) * 0.05 * 5
        assert abs(q95 - q05 - 2 * 0.9 * expected_spread) < 1e-9

    def test_forecast_horizon_matches_inputs(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        result, _ = model.predict(artifact, predict_inputs, _RNG)
        ensemble = result["discharge"]
        assert ensemble.forecast_horizon_steps == predict_inputs.forecast_horizon_steps

    def test_empty_observations_raise_narrow_error(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        empty_inputs = StationModelInputs(
            station_id=predict_inputs.station_id,
            data=StationInputData(
                past_targets=pl.DataFrame(
                    schema={
                        "timestamp": pl.Datetime("us", "UTC"),
                        "discharge": pl.Float64,
                    }
                ),
                past_dynamic=predict_inputs.data.past_dynamic,
                future_dynamic=predict_inputs.data.future_dynamic,
                static=predict_inputs.data.static,
            ),
            issue_time=predict_inputs.issue_time,
            forecast_horizon_steps=predict_inputs.forecast_horizon_steps,
            time_step=predict_inputs.time_step,
        )

        with pytest.raises(
            InsufficientObservationsError, match="at least one observation"
        ):
            model.predict(artifact, empty_inputs, _RNG)


class TestSerialize:
    def test_round_trip(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
    ) -> None:
        raw = model.serialize_artifact(artifact)
        restored = model.deserialize_artifact(raw)
        assert isinstance(restored, PersistenceArtifact)
        assert restored.target_parameters == artifact.target_parameters

    def test_serialized_is_valid_json(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
    ) -> None:
        raw = model.serialize_artifact(artifact)
        parsed = json.loads(raw)
        assert "target_parameters" in parsed
        assert isinstance(parsed["target_parameters"], list)

    def test_no_pickle(
        self,
        model: PersistenceFallbackModel,
        artifact: PersistenceArtifact,
    ) -> None:
        raw = model.serialize_artifact(artifact)
        # Pickle magic bytes start with 0x80
        assert raw[:1] != b"\x80"
        # Must be decodable as UTF-8 text (JSON)
        raw.decode("utf-8")


class TestCustomSpread:
    def test_custom_spread_pct_changes_spread(
        self,
        artifact: PersistenceArtifact,
        predict_inputs: StationModelInputs,
    ) -> None:
        model_narrow = PersistenceFallbackModel(spread_pct_per_step=0.01)
        model_wide = PersistenceFallbackModel(spread_pct_per_step=0.20)

        result_narrow, _ = model_narrow.predict(artifact, predict_inputs, _RNG)
        result_wide, _ = model_wide.predict(artifact, predict_inputs, _RNG)

        def total_spread(ensembles: dict) -> float:
            vals = ensembles["discharge"].values
            return float(vals["value"].max() - vals["value"].min())  # type: ignore[operator]

        assert total_spread(result_narrow) < total_spread(result_wide)
