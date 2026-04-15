from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from sapphire_flow.models.climatology_fallback import (
    ClimatologyArtifact,
    ClimatologyFallbackModel,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.model import (
    StationInputData,
    StationModelInputs,
    StationTrainingData,
)

_STEP = timedelta(days=1)
_RNG = random.Random(42)
_STATION_ID = StationId("test_station")
_ISSUE_TIME = ensure_utc(datetime(2025, 6, 15, tzinfo=UTC))


def _make_training_data(n_rows: int = 400) -> StationTrainingData:
    base = datetime(2022, 1, 1, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(n_rows)]
    rng = random.Random(1)
    past_targets = pl.DataFrame(
        {"timestamp": ts, "discharge": [max(0.1, rng.gauss(10.0, 2.0)) for _ in ts]}
    )
    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=pl.DataFrame({"timestamp": ts}),
        future_dynamic=pl.DataFrame({"timestamp": ts[:5]}),
        static=None,
        time_step=_STEP,
        val_start=None,
    )


def _make_predict_inputs(horizon: int = 5) -> StationModelInputs:
    base = datetime(2025, 6, 14, tzinfo=UTC)
    ts = [ensure_utc(base + timedelta(days=i)) for i in range(3)]
    past_targets = pl.DataFrame({"timestamp": ts, "discharge": [5.0, 6.0, 7.0]})
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=past_targets,
            past_dynamic=pl.DataFrame({"timestamp": ts}),
            future_dynamic=pl.DataFrame({"timestamp": ts}),
            static=None,
        ),
        issue_time=_ISSUE_TIME,
        forecast_horizon_steps=horizon,
        time_step=_STEP,
    )


class TestClimatologyFallbackModelTrain:
    def test_forecast_horizon_declared(self) -> None:
        assert ClimatologyFallbackModel().data_requirements.forecast_horizon_steps == 5

    def test_happy_path_produces_artifact(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        assert isinstance(artifact, ClimatologyArtifact)

    def test_artifact_has_expected_quantile_levels(self) -> None:
        model = ClimatologyFallbackModel()
        artifact: ClimatologyArtifact = model.train(_make_training_data(400), {}, _RNG)
        levels = sorted(artifact.quantiles["quantile"].unique().to_list())
        assert levels == pytest.approx([0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95])

    def test_artifact_has_expected_columns(self) -> None:
        model = ClimatologyFallbackModel()
        artifact: ClimatologyArtifact = model.train(_make_training_data(400), {}, _RNG)
        assert set(artifact.quantiles.columns) == {
            "day_of_year",
            "quantile",
            "value",
            "parameter",
        }

    def test_artifact_covers_all_days_of_year(self) -> None:
        model = ClimatologyFallbackModel()
        artifact: ClimatologyArtifact = model.train(_make_training_data(730), {}, _RNG)
        doys = artifact.quantiles["day_of_year"].unique().sort().to_list()
        assert len(doys) >= 365

    def test_raises_on_insufficient_data(self) -> None:
        model = ClimatologyFallbackModel()
        with pytest.raises(ValueError, match="Insufficient training data"):
            model.train(_make_training_data(100), {}, _RNG)

    def test_exactly_365_rows_passes(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(365), {}, _RNG)
        assert isinstance(artifact, ClimatologyArtifact)


class TestClimatologyFallbackModelPredict:
    def test_returns_forecast_ensemble(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        results, state = model.predict(artifact, _make_predict_inputs(5), _RNG)
        assert "discharge" in results
        assert state is None

    def test_ensemble_has_correct_horizon(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        results, _ = model.predict(artifact, _make_predict_inputs(5), _RNG)
        ensemble = results["discharge"]
        assert ensemble.forecast_horizon_steps == 5

    def test_ensemble_is_quantile_representation(self) -> None:
        from sapphire_flow.types.enums import EnsembleRepresentation

        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        results, _ = model.predict(artifact, _make_predict_inputs(5), _RNG)
        assert results["discharge"].representation == EnsembleRepresentation.QUANTILES

    def test_ensemble_values_have_expected_columns(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        results, _ = model.predict(artifact, _make_predict_inputs(5), _RNG)
        cols = set(results["discharge"].values.columns)
        assert "valid_time" in cols
        assert "quantile" in cols
        assert "value" in cols
        assert "member_id" not in cols

    def test_forecast_ensemble_station_id_matches(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        results, _ = model.predict(artifact, _make_predict_inputs(3), _RNG)
        assert results["discharge"].station_id == _STATION_ID


class TestClimatologyFallbackModelSerialize:
    def test_round_trip_preserves_quantiles(self) -> None:
        model = ClimatologyFallbackModel()
        artifact: ClimatologyArtifact = model.train(_make_training_data(400), {}, _RNG)
        raw = model.serialize_artifact(artifact)
        restored: ClimatologyArtifact = model.deserialize_artifact(raw)
        assert artifact.quantiles.equals(restored.quantiles)

    def test_serialized_bytes_are_not_empty(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        raw = model.serialize_artifact(artifact)
        assert len(raw) > 0

    def test_no_pickle_opcodes(self) -> None:
        model = ClimatologyFallbackModel()
        artifact = model.train(_make_training_data(400), {}, _RNG)
        raw = model.serialize_artifact(artifact)
        # Polars IPC starts with the Arrow IPC magic bytes "ARROW1\0\0"
        assert raw[:6] == b"ARROW1"
        # Pickle starts with \x80 (highest protocol) — must not be present at start
        assert raw[:1] != b"\x80"
