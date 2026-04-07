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

_N_ROWS = 30  # > lookback (7) + horizon (5) = 12


def _make_training_data(
    n_rows: int = _N_ROWS, horizon_steps: int = 5
) -> StationTrainingData:
    base = datetime(2020, 1, 1, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(n_rows)]
    rng = random.Random(1)
    past_targets = pl.DataFrame(
        {"timestamp": ts, "discharge": [max(0.1, rng.gauss(10.0, 2.0)) for _ in ts]}
    )
    past_dynamic = pl.DataFrame(
        {
            "timestamp": ts,
            "precipitation": [max(0.0, rng.gauss(3.0, 1.0)) for _ in ts],
            "temperature": [rng.gauss(15.0, 5.0) for _ in ts],
        }
    )
    future_ts = ts[:horizon_steps]
    future_dynamic = pl.DataFrame(
        {
            "timestamp": future_ts,
            "precipitation": [max(0.0, rng.gauss(3.0, 1.0)) for _ in future_ts],
            "temperature": [rng.gauss(15.0, 5.0) for _ in future_ts],
        }
    )
    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
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
    past_dynamic = pl.DataFrame(
        {
            "timestamp": ts,
            "precipitation": [max(0.0, rng.gauss(3.0, 1.0)) for _ in ts],
            "temperature": [rng.gauss(15.0, 5.0) for _ in ts],
        }
    )
    future_ts = [ensure_utc(base + timedelta(days=n_past + i)) for i in range(horizon)]
    future_dynamic = pl.DataFrame(
        {
            "timestamp": future_ts,
            "precipitation": [max(0.0, rng.gauss(3.0, 1.0)) for _ in future_ts],
            "temperature": [rng.gauss(15.0, 5.0) for _ in future_ts],
        }
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=past_targets,
            past_dynamic=past_dynamic,
            future_dynamic=future_dynamic,
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
        data = _make_training_data(horizon_steps=3)
        artifact = model.train(data, {}, rng)

        inputs = _make_predict_inputs(horizon=10)
        with pytest.raises(ValueError, match="forecast_horizon_steps"):
            model.predict(artifact, inputs, rng)

    def test_ensemble_members_count(self) -> None:
        rng = random.Random(50)
        model = LinearRegressionDaily()
        data = _make_training_data(horizon_steps=3)
        artifact = model.train(data, {}, rng)
        inputs = _make_predict_inputs(horizon=3)
        result, _ = model.predict(artifact, inputs, rng)

        ensemble = result["discharge"]
        member_ids = ensemble.values["member_id"].unique().to_list()
        assert len(member_ids) == 50

    def test_non_negative_predictions(self) -> None:
        rng = random.Random(60)
        model = LinearRegressionDaily()
        data = _make_training_data()
        artifact = model.train(data, {}, rng)
        inputs = _make_predict_inputs(horizon=5)
        result, _ = model.predict(artifact, inputs, rng)

        values = result["discharge"].values["value"].to_numpy()
        assert (values >= 0).all()
