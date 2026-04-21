from __future__ import annotations

import io
import random
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from sapphire_flow.models.linear_regression_daily import LinearRegressionDaily
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
_STATION_ID = StationId("smoke_test_station")

_N_ROWS = 30  # > lookback (7) + 1 = 8


def _make_training_data(n_rows: int = _N_ROWS) -> StationTrainingData:
    base = datetime(2020, 1, 1, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(n_rows)]
    rng = random.Random(1)
    past_targets = pl.DataFrame(
        {"timestamp": ts, "discharge": [max(0.1, rng.gauss(10.0, 2.0)) for _ in ts]}
    )
    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=pl.DataFrame({"timestamp": ts}),
        future_dynamic=pl.DataFrame({"timestamp": ts[:0]}),
        static=None,
        time_step=_STEP,
        val_start=None,
    )


def _make_predict_inputs(
    horizon: int = 5,
    n_past: int = 10,
) -> StationModelInputs:
    base = datetime(2020, 2, 1, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(n_past)]
    rng = random.Random(2)
    past_targets = pl.DataFrame(
        {"timestamp": ts, "discharge": [max(0.1, rng.gauss(10.0, 2.0)) for _ in ts]}
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=past_targets,
            past_dynamic=pl.DataFrame({"timestamp": ts}),
            future_dynamic=pl.DataFrame({"timestamp": ts[:0]}),
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=horizon,
        time_step=_STEP,
    )


class TestLinearRegressionDaily:
    def test_train_and_predict_round_trip(self) -> None:
        rng = random.Random(10)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        inputs = _make_predict_inputs()
        result, state = model.predict(artifact, inputs, rng)

        assert "discharge" in result
        assert state is None
        ensemble = result["discharge"]
        assert ensemble.parameter == "discharge"

    def test_serialize_deserialize_round_trip(self) -> None:
        rng = random.Random(20)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        raw = model.serialize_artifact(artifact)
        reloaded = model.deserialize_artifact(raw)

        inputs = _make_predict_inputs()
        rng1 = random.Random(123)
        rng2 = random.Random(123)
        result_orig, _ = model.predict(artifact, inputs, rng1)
        result_reload, _ = model.predict(reloaded, inputs, rng2)

        orig_values = result_orig["discharge"].values["value"].to_list()
        reload_values = result_reload["discharge"].values["value"].to_list()
        assert orig_values == pytest.approx(reload_values, rel=1e-6)

    def test_no_pickle_in_serialization(self) -> None:
        rng = random.Random(30)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        raw = model.serialize_artifact(artifact)

        loaded = np.load(io.BytesIO(raw), allow_pickle=False)
        assert "coefficients" in loaded
        assert "intercepts" in loaded
        assert "residuals" in loaded

    def test_horizon_guard(self) -> None:
        rng = random.Random(40)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)

        inputs = _make_predict_inputs(horizon=10)
        with pytest.raises(ValueError, match="forecast_horizon_steps"):
            model.predict(artifact, inputs, rng)

    def test_ensemble_members_count(self) -> None:
        rng = random.Random(50)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        inputs = _make_predict_inputs(horizon=5)
        result, _ = model.predict(artifact, inputs, rng)

        ensemble = result["discharge"]
        member_ids = ensemble.values["member_id"].unique().to_list()
        assert len(member_ids) == 50

    def test_train_rejects_insufficient_data(self) -> None:
        rng = random.Random(70)
        model = LinearRegressionDaily()
        # Only 7 rows — need at least 8 (lookback=7 + 1)
        data = _make_training_data(n_rows=7)
        with pytest.raises(ValueError, match="Not enough training rows"):
            model.train(data, {}, rng)

    def test_data_requirements_forecast_horizon_steps(self) -> None:
        assert LinearRegressionDaily().data_requirements.forecast_horizon_steps == 5

    def test_data_requirements_no_weather_features(self) -> None:
        req = LinearRegressionDaily().data_requirements
        assert req.past_dynamic_features == frozenset()
        assert req.future_dynamic_features == frozenset()

    def test_non_negative_predictions(self) -> None:
        rng = random.Random(60)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        inputs = _make_predict_inputs(horizon=5)
        result, _ = model.predict(artifact, inputs, rng)

        values = result["discharge"].values["value"].to_numpy()
        assert (values >= 0).all()

    def test_forecast_plausibility_on_rising_ramp(self) -> None:
        # Train on a slowly rising ramp: 10, 11, 12, ..., 10+n-1
        # Predict 5 days; assert mean within plausible range,
        # all non-negative, spread > 0
        rng = random.Random(99)
        model = LinearRegressionDaily()

        n_rows = 30
        base = datetime(2020, 1, 1, tzinfo=UTC)
        ts = [ensure_utc(base + timedelta(days=i)) for i in range(n_rows)]
        ramp_values = [10.0 + i * 0.5 for i in range(n_rows)]  # 10.0 → 24.5
        past_targets = pl.DataFrame({"timestamp": ts, "discharge": ramp_values})

        training = StationTrainingData(
            past_targets=past_targets,
            past_dynamic=pl.DataFrame({"timestamp": ts}),
            future_dynamic=pl.DataFrame({"timestamp": ts[:0]}),
            static=None,
            time_step=_STEP,
            val_start=None,
        )
        artifact = model.train(training, {}, rng)

        # Recent observations also follow the ramp (last 10 days)
        pred_base = datetime(2020, 2, 1, tzinfo=UTC)
        pred_ts = [ensure_utc(pred_base + timedelta(days=i)) for i in range(10)]
        recent_values = [22.0 + i * 0.5 for i in range(10)]  # 22.0 → 26.5
        past_targets_pred = pl.DataFrame(
            {"timestamp": pred_ts, "discharge": recent_values}
        )
        inputs = StationModelInputs(
            station_id=_STATION_ID,
            data=StationInputData(
                past_targets=past_targets_pred,
                past_dynamic=pl.DataFrame({"timestamp": pred_ts}),
                future_dynamic=pl.DataFrame({"timestamp": pred_ts[:0]}),
                static=None,
            ),
            issue_time=_NOW,
            forecast_horizon_steps=5,
            time_step=_STEP,
        )
        result, _ = model.predict(artifact, inputs, rng)

        ensemble = result["discharge"]
        values = ensemble.values["value"].to_numpy()

        # All 50 members non-negative
        assert (values >= 0).all()

        # Ensemble spread (std) > 0 (model generates uncertainty)
        assert values.std() > 0.0

        # Forecast mean within plausible range of recent observations
        # Recent obs end at ~26.5; forecast should be in [0, 100] range
        mean_forecast = values.mean()
        assert 0.0 < mean_forecast < 100.0

    def test_valid_times_generated_from_issue_time(self) -> None:
        rng = random.Random(80)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        horizon = 5
        inputs = _make_predict_inputs(horizon=horizon)
        result, _ = model.predict(artifact, inputs, rng)

        expected_times = [_NOW + (i + 1) * _STEP for i in range(horizon)]
        actual_times = (
            result["discharge"]
            .values.filter(pl.col("member_id") == 0)
            .sort("valid_time")["valid_time"]
            .to_list()
        )
        assert actual_times == expected_times
