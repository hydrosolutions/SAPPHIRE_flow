from __future__ import annotations

import csv
import hashlib
import io
import json
import random
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.adapters.deployment_smoke_weather import (
    DeploymentSmokeWeatherAdapter,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.models.deployment_smoke import DeploymentSmokeModel
from sapphire_flow.preprocessing.converters import point_forecast_to_records
from sapphire_flow.services.operational_inputs import build_future_dynamic_frame
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import PointForecast

_ISSUE = ensure_utc(datetime(2025, 1, 3, tzinfo=UTC))
_STEP = timedelta(hours=1)
_RESOURCES = files("sapphire_flow").joinpath("data/deployment_smoke")


def _model() -> DeploymentSmokeModel:
    return DeploymentSmokeModel(_RESOURCES.joinpath("model-config.json").read_bytes())


def _inputs(
    *, discharge: list[float] | None = None, precipitation: list[float] | None = None
) -> fi.ModelInputs:
    past = pl.DataFrame(
        {
            "datetime": [_ISSUE - n * _STEP for n in (3, 2, 1)],
            "discharge": discharge if discharge is not None else [8.0, 9.0, 10.0],
        }
    )
    future = pl.DataFrame(
        {
            "datetime": [_ISSUE + n * _STEP for n in (1, 2, 3)],
            "precipitation": precipitation
            if precipitation is not None
            else [1.0, 2.0, 4.0],
        }
    )
    return fi.ModelInputs(
        stations={
            "demo-001": fi.StationInputs(
                dynamic={
                    _STEP: fi.SpatialInputs(
                        data={
                            fi.FISpatialRepresentation.POINT: fi.DynamicInputs(
                                past_known={
                                    "obs": {
                                        "discharge": fi.InputSeries(
                                            unit=fi.Unit.M3_PER_S, data=past
                                        )
                                    }
                                },
                                future_known={
                                    "nwp": {
                                        "precipitation": fi.InputSeries(
                                            unit=fi.Unit.MM, data=future
                                        )
                                    }
                                },
                            )
                        }
                    )
                }
            )
        }
    )


def _predict(inputs: fi.ModelInputs) -> fi.ModelResult:
    model = _model()
    artifact = model.deserialize_artifact(
        _RESOURCES.joinpath("artifact.json").read_bytes()
    )
    return model.predict(
        artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(1)
    )


class TestDeploymentSmokeModel:
    def test_packaged_artifact_round_trip_and_fi_contract(self) -> None:
        model = _model()
        assert isinstance(model, fi.ForecastModel)
        manifest = json.loads(_RESOURCES.joinpath("manifest.json").read_bytes())
        assert model.config_hash == manifest["expected_config_hash"]
        artifact = model.train(_inputs(), config={}, rng=random.Random(9))
        assert (
            model.deserialize_artifact(model.serialize_artifact(artifact)) == artifact
        )
        assert (
            model.deserialize_artifact(
                _RESOURCES.joinpath("artifact.json").read_bytes()
            )
            == artifact
        )
        for name, digest in manifest["sha256"].items():
            assert (
                hashlib.sha256(_RESOURCES.joinpath(name).read_bytes()).hexdigest()
                == digest
            )

    def test_prediction_consumes_both_inputs_with_twenty_members(self) -> None:
        result = _predict(_inputs())
        assert isinstance(result, fi.ModelSuccess)
        output = result.output.variables["demo-001"]["discharge"]
        assert output.metadata.offset == 2
        assert output.metadata.timedelta == _STEP
        assert output.metadata.unit is fi.Unit.M3_PER_S
        assert output.trajectories is not None
        assert output.trajectories.num_samples == 20
        assert output.trajectories.data["1"].to_list() == pytest.approx(
            [10.1, 10.2, 10.4]
        )
        assert output.trajectories.data["20"].to_list() == pytest.approx(
            [10.29, 10.39, 10.59]
        )

    @pytest.mark.parametrize(
        ("discharge", "precipitation", "expected"),
        [
            ([8.0, 9.0, 12.0], [1.0, 2.0, 4.0], [12.1, 12.2, 12.4]),
            ([8.0, 9.0, 10.0], [1.0, 7.0, 4.0], [10.1, 10.7, 10.4]),
        ],
    )
    def test_independent_input_changes_change_expected_output(
        self, discharge: list[float], precipitation: list[float], expected: list[float]
    ) -> None:
        result = _predict(_inputs(discharge=discharge, precipitation=precipitation))
        assert isinstance(result, fi.ModelSuccess)
        traces = result.output.variables["demo-001"]["discharge"].trajectories
        assert traces is not None
        assert traces.data["1"].to_list() == pytest.approx(expected)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
    def test_invalid_values_return_typed_failure(self, value: float) -> None:
        result = _predict(_inputs(precipitation=[1.0, value, 4.0]))
        assert isinstance(result, fi.ModelFailure)
        assert result.cause is fi.FailureCause.INPUT_DATA

    @pytest.mark.parametrize("kind", ["short", "gap", "missing", "unit"])
    def test_invalid_input_coverage_returns_failure(self, kind: str) -> None:
        inputs = _inputs()
        branch = (
            inputs.stations["demo-001"]
            .dynamic[_STEP]
            .data[fi.FISpatialRepresentation.POINT]
        )
        series = branch.future_known["nwp"]["precipitation"]
        if kind == "short":
            series.data = series.data.head(2)
        elif kind == "gap":
            series.data = series.data.with_columns(
                pl.col("datetime") + timedelta(hours=1)
            )
        elif kind == "missing":
            branch.future_known.clear()
        else:
            series.unit = fi.Unit.M3_PER_S
        result = _predict(inputs)
        assert isinstance(result, fi.ModelFailure)
        assert result.cause is fi.FailureCause.INPUT_DATA

    def test_corrupt_artifact_cannot_claim_matching_configuration(self) -> None:
        artifact = json.loads(_RESOURCES.joinpath("artifact.json").read_bytes())
        artifact["precipitation_factor"] = 99.0
        with pytest.raises(ValueError, match="configuration mismatch"):
            _model().deserialize_artifact(json.dumps(artifact).encode())

    def test_wrong_artifact_returns_failure(self) -> None:
        result = _model().predict(
            object(), inputs=_inputs(), issue_datetime=_ISSUE, rng=random.Random(1)
        )
        assert isinstance(result, fi.ModelFailure)
        assert result.cause is fi.FailureCause.CONFIGURATION

    def test_finite_inputs_that_overflow_return_failure(self) -> None:
        model = DeploymentSmokeModel(
            b'{"schema_version":1,"precipitation_factor":1e308,"member_increment":0.01}'
        )
        artifact = model.train(_inputs(), config={}, rng=random.Random(1))
        result = model.predict(
            artifact, inputs=_inputs(), issue_datetime=_ISSUE, rng=random.Random(1)
        )
        assert isinstance(result, fi.ModelFailure)
        assert "overflow" in result.message

    def test_prediction_is_deterministic_across_injected_rngs(self) -> None:
        model = _model()
        artifact = model.train(_inputs(), config={}, rng=random.Random(1))
        outputs = [
            model.predict(
                artifact,
                inputs=_inputs(),
                issue_datetime=_ISSUE,
                rng=random.Random(seed),
            )
            for seed in (1, 99)
        ]
        assert all(isinstance(output, fi.ModelSuccess) for output in outputs)
        frames = [
            output.output.variables["demo-001"]["discharge"].trajectories
            for output in outputs
            if isinstance(output, fi.ModelSuccess)
        ]
        assert frames[0] is not None and frames[1] is not None
        assert frames[0].data.equals(frames[1].data)

    def test_weather_assembly_and_fi_adapter_preserve_fixture_contract(self) -> None:
        discharge_rows = list(
            csv.DictReader(
                io.StringIO(_RESOURCES.joinpath("discharge_hourly.csv").read_text())
            )
        )
        expected = json.loads(_RESOURCES.joinpath("expected-result.json").read_text())
        sid = StationId(UUID(int=1))
        binding = StationWeatherSource(
            station_id=sid,
            nwp_source="icon_ch2_eps",
            extraction_type=SpatialRepresentation.POINT,
            role=WeatherSourceRole.FORECAST,
            status=WeatherSourceStatus.ACTIVE,
        )
        weather = DeploymentSmokeWeatherAdapter(
            _RESOURCES.joinpath("weather.csv").read_bytes(), cycle_time=_ISSUE
        ).fetch_forecasts([binding], _ISSUE)[sid]
        assert isinstance(weather, PointForecast)
        records = point_forecast_to_records(
            sid, weather, lambda: _ISSUE, lambda: UUID(int=2)
        )
        future = build_future_dynamic_frame(
            records,
            time_step=_STEP,
            issue_time=_ISSUE,
            forecast_horizon_steps=3,
            future_dynamic_features=frozenset({"precipitation"}),
            ensemble_mode=EnsembleMode.SINGLE,
            feature_horizons={"precipitation": 3},
        )
        model = _model()
        adapter = fi.adapt_if_fi(model)
        assert isinstance(adapter, fi.ForecastInterfaceAdapter)
        assert adapter.config_hash == model.config_hash
        artifact = adapter.deserialize_artifact(
            _RESOURCES.joinpath("artifact.json").read_bytes()
        )
        inputs = StationModelInputs(
            station_id=sid,
            data=StationInputData(
                past_targets=pl.DataFrame(
                    {
                        "timestamp": [
                            datetime.fromisoformat(row["time"])
                            for row in discharge_rows[-3:]
                        ],
                        "discharge": [
                            float(row["value"]) for row in discharge_rows[-3:]
                        ],
                    }
                ),
                past_dynamic=pl.DataFrame(
                    {"timestamp": pl.Series([], dtype=pl.Datetime("us", "UTC"))}
                ),
                future_dynamic=future,
                static=None,
            ),
            issue_time=_ISSUE,
            forecast_horizon_steps=3,
            time_step=_STEP,
        )
        ensembles, state = adapter.predict(artifact, inputs, random.Random(5))
        ensemble = ensembles["discharge"]
        assert state is None
        assert ensemble.member_count == 20
        assert (
            ensemble.member_count
            >= DeploymentConfig(max_retention_days=600).min_operational_ensemble_size
        )
        assert ensemble.forecast_horizon_steps == 3
        assert ensemble.values.height == 60
        for step in expected["forecast"]:
            actual = ensemble.values.filter(
                pl.col("valid_time") == datetime.fromisoformat(step["valid_time"])
            ).sort("member_id")
            assert actual["member_id"].to_list() == expected["member_ids"]
            assert actual["value"].to_list() == pytest.approx(
                step["values"], abs=1e-8, rel=0
            )
        assert future["timestamp"].to_list() == [_ISSUE + n * _STEP for n in (1, 2, 3)]

    def test_local_runtime_evidence_does_not_claim_release_or_host_port_qualification(
        self,
    ) -> None:
        manifest = json.loads(_RESOURCES.joinpath("manifest.json").read_bytes())
        assert manifest["status"] == "pending_release_qualification"
        assert manifest["pending"]
        assert manifest["qc_qualification"]["loaded_status"] == "not established"
        runtime = manifest["runtime_qualification"]
        assert runtime["loaded_status"] == "qc_passed"
        assert runtime["observations_passed"] == 72
        assert runtime["api_comparisons"] == 60
        assert runtime["host_port_verified"] is False
        assert runtime["cleanup_verified"] is True
