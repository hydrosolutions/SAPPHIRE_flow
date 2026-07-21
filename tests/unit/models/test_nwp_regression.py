"""LOCKED failing-first tests for the epic-088 M2 NWP-consuming regression models.

Two ``forecastinterface`` ``ForecastModel`` implementations share a base and are
compared for skill in M3:

* ``NwpRegression`` — daily discharge on FUTURE precip/temp windows + PAST
  discharge lags.
* ``NwpRainfallRunoff`` — weather-only: daily discharge on FUTURE precip/temp
  windows only.

Both are ``ArtifactScope.STATION``, deterministic single-trajectory (the 21-member
ensemble is assembled downstream in M3, NOT inside the model), serialize via
``np.savez_compressed``, and run on a daily ``timedelta(days=1)`` step.

These tests are RED until ``sapphire_flow.models.nwp_regression`` exists.
"""

from __future__ import annotations

import io
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np
import polars as pl
import pytest
from forecast_interface import (
    AggregationMethod,
    EnsembleMode,
    FailureCause,
    ModelFailure,
)

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.models.nwp_regression import (
    NwpRainfallRunoff,
    NwpRegression,
    NwpRegressionArtifact,
)
from sapphire_flow.protocols.forecast_model import StationForecastModel
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import ArtifactScope
from sapphire_flow.types.ids import StationId

# --------------------------------------------------------------------------- #
# Constants / deterministic fixtures
# --------------------------------------------------------------------------- #

_STEP = timedelta(days=1)
_SID = StationId(UUID("00000000-0000-0000-0000-000000000042"))
_ISSUE = ensure_utc(datetime(2024, 6, 1, tzinfo=UTC))
_LOOKBACK = 7  # discharge-lag window declared by the with-lags variant
_VARIANTS = [NwpRegression, NwpRainfallRunoff]

_N_TRAIN = 24
_TRAIN_BASE = datetime(2024, 1, 1, tzinfo=UTC)


def _train_series() -> tuple[list[datetime], list[float], list[float], list[float]]:
    """Deterministic aligned daily (discharge, precip, temp) with a known structure.

    discharge[t] = 2 + 0.4*discharge[t-1] + 5*precip[t] + 0.5*temp[t]

    Encodes a POSITIVE precipitation slope (monotonicity) AND a POSITIVE
    autoregressive lag term (so the with-lags variant responds to past discharge).
    Precip/temp are seeded-random to avoid lag/forcing collinearity.
    """
    rng = random.Random(20240701)
    ts = [_TRAIN_BASE + i * _STEP for i in range(_N_TRAIN)]
    precip = [round(rng.uniform(0.0, 20.0), 4) for _ in range(_N_TRAIN)]
    temp = [round(rng.uniform(-5.0, 25.0), 4) for _ in range(_N_TRAIN)]
    discharge = [10.0] * _N_TRAIN
    for i in range(1, _N_TRAIN):
        discharge[i] = 2.0 + 0.4 * discharge[i - 1] + 5.0 * precip[i] + 0.5 * temp[i]
    return ts, discharge, precip, temp


# --------------------------------------------------------------------------- #
# FI-direct ModelInputs builders (we control the bundle → full precision)
# --------------------------------------------------------------------------- #


def _fi_frame(ts: list[datetime], name: str, vals: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"datetime": ts, name: vals}).with_columns(
        pl.col("datetime").cast(pl.Datetime("us", "UTC"))
    )


def _series(
    ts: list[datetime], name: str, vals: list[float], unit: fi_boundary.Unit
) -> fi_boundary.InputSeries:
    return fi_boundary.InputSeries(unit=unit, data=_fi_frame(ts, name, vals))


def _dynamic_spec(
    model: object,
) -> tuple[
    timedelta, fi_boundary.FISpatialRepresentation, fi_boundary.DynamicInputSpec
]:
    req = model.input_requirement  # type: ignore[attr-defined]
    step, spatial = next(iter(req.dynamic.items()))
    rep, spec = next(iter(spatial.data.items()))
    return step, rep, spec


def _declared_horizon(model: object) -> int:
    _step, _rep, spec = _dynamic_spec(model)
    variables = next(iter(spec.future_known.values()))
    return next(iter(variables.values())).future_steps


def _future_known_from_spec(
    spec: fi_boundary.DynamicInputSpec,
    ts: list[datetime],
    precip: list[float],
    temp: list[float],
) -> dict[str, dict[str, fi_boundary.InputSeries]]:
    # Read product/variable placement + declared units from the model itself.
    out: dict[str, dict[str, fi_boundary.InputSeries]] = {}
    for product, variables in spec.future_known.items():
        inner: dict[str, fi_boundary.InputSeries] = {}
        for name, var in variables.items():
            vals = precip if name == "precipitation" else temp
            inner[name] = _series(ts, name, vals, var.unit)
        out[product] = inner
    return out


def _model_inputs(
    model: object,
    *,
    step: timedelta,
    rep: fi_boundary.FISpatialRepresentation,
    past_known: dict[str, dict[str, fi_boundary.InputSeries]],
    future_known: dict[str, dict[str, fi_boundary.InputSeries]],
) -> fi_boundary.ModelInputs:
    dynamic = fi_boundary.DynamicInputs(
        past_known=past_known, future_known=future_known
    )
    station = fi_boundary.StationInputs(
        dynamic={step: fi_boundary.SpatialInputs(data={rep: dynamic})},
        static={},
    )
    return fi_boundary.ModelInputs(stations={"station": station})


def _fi_train_inputs(
    model: object,
    ts: list[datetime],
    discharge: list[float],
    precip: list[float],
    temp: list[float],
) -> fi_boundary.ModelInputs:
    step, rep, spec = _dynamic_spec(model)
    # The training TARGET (discharge) is always delivered under obs/discharge,
    # for BOTH variants. The weather-only variant reads it ONLY as the fit target
    # (it declares no past_known and uses no discharge feature).
    past_known = {
        "obs": {
            "discharge": _series(ts, "discharge", discharge, fi_boundary.Unit.M3_PER_S)
        }
    }
    future_known = _future_known_from_spec(spec, ts, precip, temp)
    return _model_inputs(
        model, step=step, rep=rep, past_known=past_known, future_known=future_known
    )


def _fi_predict_inputs(
    model: object,
    *,
    issue: datetime,
    horizon: int,
    precip: list[float],
    temp: list[float],
    lag_discharge: list[float] | None,
) -> fi_boundary.ModelInputs:
    step, rep, spec = _dynamic_spec(model)
    future_ts = [issue + (k + 1) * step for k in range(horizon)]
    future_known = _future_known_from_spec(spec, future_ts, precip, temp)
    past_known: dict[str, dict[str, fi_boundary.InputSeries]] = {}
    if lag_discharge is not None:
        lb = len(lag_discharge)
        past_ts = [issue - (lb - 1 - i) * step for i in range(lb)]
        past_known = {
            "obs": {
                "discharge": _series(
                    past_ts, "discharge", lag_discharge, fi_boundary.Unit.M3_PER_S
                )
            }
        }
    return _model_inputs(
        model, step=step, rep=rep, past_known=past_known, future_known=future_known
    )


def _fit(model: object, *, seed: int = 0) -> object:
    ts, discharge, precip, temp = _train_series()
    return model.train(  # type: ignore[attr-defined]
        _fi_train_inputs(model, ts, discharge, precip, temp),
        config={},
        rng=random.Random(seed),
    )


def _fi_det_values(result: fi_boundary.ModelResult) -> list[float]:
    assert isinstance(result, fi_boundary.ModelSuccess)
    station_vars = next(iter(result.output.variables.values()))
    deterministic = station_vars["discharge"].deterministic
    assert deterministic is not None
    return deterministic.data.sort("datetime")["value"].to_list()


# --------------------------------------------------------------------------- #
# SAP3-adapter StationModelInputs builders (drive the real M2 -> M3 boundary)
# --------------------------------------------------------------------------- #


def _sap_frame(ts: list[datetime], **cols: list[object]) -> pl.DataFrame:
    return pl.DataFrame({"timestamp": ts, **cols}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _station_inputs(
    *,
    issue: datetime,
    horizon: int,
    precip: list[object],
    temp: list[object],
    lag_discharge: list[float],
) -> fi_boundary.StationModelInputs:
    from sapphire_flow.types.model import StationInputData, StationModelInputs

    future_ts = [issue + (k + 1) * _STEP for k in range(horizon)]
    lb = len(lag_discharge)
    past_ts = [issue - (lb - 1 - i) * _STEP for i in range(lb)]
    data = StationInputData(
        past_targets=_sap_frame(past_ts, discharge=list(lag_discharge)),
        past_dynamic=_sap_frame(past_ts),
        future_dynamic=_sap_frame(future_ts, precipitation=precip, temperature=temp),
        static=None,
    )
    return StationModelInputs(
        station_id=_SID,
        data=data,
        issue_time=ensure_utc(issue),
        forecast_horizon_steps=horizon,
        time_step=_STEP,
    )


def _adapter(model: object) -> fi_boundary.ForecastInterfaceAdapter:
    adapted = fi_boundary.adapt_if_fi(model, station_code_resolver=lambda _sid: "gauge")
    assert isinstance(adapted, fi_boundary.ForecastInterfaceAdapter)
    return adapted


def _adapter_predict_values(
    adapter: fi_boundary.ForecastInterfaceAdapter,
    artifact: object,
    inputs: fi_boundary.StationModelInputs,
    *,
    seed: int = 0,
) -> list[float]:
    ensembles, state = adapter.predict(artifact, inputs, random.Random(seed))
    assert state is None
    ensemble = ensembles["discharge"]
    return ensemble.values.sort("valid_time")["value"].to_list()


# --------------------------------------------------------------------------- #
# 1. Requirements + units (via the FI adapter)
# --------------------------------------------------------------------------- #


class TestRequirementsAndUnits:
    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_adapter_projection_and_units(self, variant: type) -> None:
        model = variant()
        adapter = _adapter(model)

        req = adapter.data_requirements
        assert req.future_dynamic_features == frozenset(
            {"precipitation", "temperature"}
        )
        assert req.target_parameters == frozenset({"discharge"})
        assert adapter.artifact_scope is ArtifactScope.STATION
        assert _STEP in req.supported_time_steps

        # Adapter satisfies SAP3's StationForecastModel contract.
        assert isinstance(adapter, StationForecastModel)

        declared = adapter.declared_units()
        assert declared["precipitation"] == "mm"
        assert declared["temperature"] == "°C"
        assert declared["discharge"] == "m³/s"
        assert adapter.unsupported_units() == frozenset()

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_future_forcing_declared_with_aggregation_and_ensemble_mode(
        self, variant: type
    ) -> None:
        _step, _rep, spec = _dynamic_spec(variant())
        future = {
            name: var
            for variables in spec.future_known.values()
            for name, var in variables.items()
        }
        assert set(future) == {"precipitation", "temperature"}

        assert future["precipitation"].unit is fi_boundary.Unit.MM
        assert future["precipitation"].aggregation is AggregationMethod.SUM
        assert future["precipitation"].ensemble_mode is EnsembleMode.ENSEMBLE

        assert future["temperature"].unit is fi_boundary.Unit.DEG_C
        assert future["temperature"].aggregation is AggregationMethod.MEAN
        assert future["temperature"].ensemble_mode is EnsembleMode.ENSEMBLE

    def test_with_lags_declares_discharge_past_known(self) -> None:
        model = NwpRegression()
        _step, _rep, spec = _dynamic_spec(model)
        past = {
            name: var
            for variables in spec.past_known.values()
            for name, var in variables.items()
        }
        assert "discharge" in past
        assert past["discharge"].unit is fi_boundary.Unit.M3_PER_S
        assert past["discharge"].lookback >= _LOOKBACK

        # discharge is the TARGET → excluded from the forcing channel, but its
        # lookback still counts toward lookback_steps (delivered from past_targets).
        req = _adapter(model).data_requirements
        assert "discharge" not in req.past_dynamic_features
        assert req.lookback_steps >= _LOOKBACK

    def test_weather_only_declares_minimal_target_past_known(self) -> None:
        model = NwpRainfallRunoff()
        _step, _rep, spec = _dynamic_spec(model)
        past_names = {
            name for variables in spec.past_known.values() for name in variables
        }
        # Declares the training TARGET only (lookback=1), delivered from
        # past_targets so the fit target is available at train time.
        assert past_names == {"discharge"}
        past = {
            name: var
            for variables in spec.past_known.values()
            for name, var in variables.items()
        }
        assert past["discharge"].lookback == 1

        req = _adapter(model).data_requirements
        # discharge is the target → excluded from the forcing channel.
        assert req.past_dynamic_features == frozenset()
        assert req.lookback_steps == 1


# --------------------------------------------------------------------------- #
# 2. Train round-trips (known-answer serialize/deserialize)
# --------------------------------------------------------------------------- #


class TestForecastHorizonIsFiveDays:
    """M3 owner decision: lower ``future_steps`` 7 -> 5 to match ICON-CH2-EPS's
    5-day / 120h coverage. ``max_nan`` stays 0. RED until ``_HORIZON = 5``.
    """

    _M3_HORIZON = 5

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_declared_future_steps_is_five(self, variant: type) -> None:
        model = variant()
        assert _declared_horizon(model) == self._M3_HORIZON

        # Both future-known forcing variables carry the SAME lowered horizon.
        _step, _rep, spec = _dynamic_spec(model)
        future_vars = [
            var
            for variables in spec.future_known.values()
            for var in variables.values()
        ]
        assert future_vars  # sanity: precip + temp declared
        for var in future_vars:
            assert var.future_steps == self._M3_HORIZON
            assert var.max_nan == 0  # unchanged by the horizon lowering

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_adapter_projects_forecast_horizon_steps_five(self, variant: type) -> None:
        req = _adapter(variant()).data_requirements
        assert req.forecast_horizon_steps == self._M3_HORIZON


class TestArtifactRoundTrip:
    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_serialize_roundtrip_preserves_coefficients_and_predictions(
        self, variant: type
    ) -> None:
        model = variant()
        artifact = _fit(model)

        raw = model.serialize_artifact(artifact)
        # np.savez_compressed, no pickle.
        npz = np.load(io.BytesIO(raw), allow_pickle=False)
        assert "coefficients" in npz.files

        reloaded = model.deserialize_artifact(raw)
        assert np.array_equal(
            np.asarray(reloaded.coefficients),  # type: ignore[attr-defined]
            np.asarray(artifact.coefficients),  # type: ignore[attr-defined]
        )

        horizon = _declared_horizon(model)
        lags = [10.0] * _LOOKBACK if variant is NwpRegression else None
        inputs = _fi_predict_inputs(
            model,
            issue=_ISSUE,
            horizon=horizon,
            precip=[7.0 + k for k in range(horizon)],
            temp=[10.0] * horizon,
            lag_discharge=lags,
        )
        original = _fi_det_values(
            model.predict(
                artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
            )
        )
        restored = _fi_det_values(
            model.predict(
                reloaded, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
            )
        )
        assert restored == pytest.approx(original)


# --------------------------------------------------------------------------- #
# 3. Predict shape — deterministic discharge over the declared horizon
# --------------------------------------------------------------------------- #


class TestPredictShape:
    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_predict_returns_deterministic_discharge_over_horizon(
        self, variant: type
    ) -> None:
        model = variant()
        artifact = _fit(model)
        horizon = _declared_horizon(model)
        lags = [10.0] * _LOOKBACK if variant is NwpRegression else None
        inputs = _fi_predict_inputs(
            model,
            issue=_ISSUE,
            horizon=horizon,
            precip=[7.0 + k for k in range(horizon)],
            temp=[10.0] * horizon,
            lag_discharge=lags,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, fi_boundary.ModelSuccess)
        assert len(result.output.variables) == 1
        station_vars = next(iter(result.output.variables.values()))
        assert set(station_vars) == {"discharge"}
        var = station_vars["discharge"]
        assert var.status is fi_boundary.VariableStatus.SUCCESS
        assert var.deterministic is not None
        assert var.quantiles is None
        assert var.trajectories is None

        frame = var.deterministic.data
        assert frame.height == horizon
        assert set(frame.columns) == {"issue_datetime", "datetime", "value"}
        assert frame["datetime"].n_unique() == horizon
        assert var.metadata.unit is fi_boundary.Unit.M3_PER_S
        assert var.metadata.timedelta == _STEP
        assert var.metadata.forecast_horizon == horizon


# --------------------------------------------------------------------------- #
# 4. Signed-coefficient / monotonicity — LOCKED physics (both variants)
# --------------------------------------------------------------------------- #


class TestPrecipitationMonotonicity:
    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_increasing_future_precip_raises_discharge(self, variant: type) -> None:
        model = variant()
        adapter = _adapter(model)
        artifact = _fit(model)
        horizon = _declared_horizon(model)

        temp = [12.0] * horizon
        lags = [10.0] * _LOOKBACK

        base = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=[6.0] * horizon,
                temp=temp,
                lag_discharge=lags,
            ),
        )
        raised = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=[56.0] * horizon,  # +50 mm, temp + lags held fixed
                temp=temp,
                lag_discharge=lags,
            ),
        )

        assert len(base) == len(raised) == horizon
        for lo, hi in zip(base, raised, strict=True):
            assert hi >= lo + 1.0


# --------------------------------------------------------------------------- #
# 5. Member-mapping (the M3 contract M2 must satisfy)
# --------------------------------------------------------------------------- #


class TestMemberMapping:
    def test_distinct_trajectories_map_to_distinct_members(self) -> None:
        model = NwpRainfallRunoff()
        adapter = _adapter(model)
        artifact = _fit(model)
        horizon = _declared_horizon(model)
        n_members = 4

        member_frames: list[pl.DataFrame] = []
        per_member_values: list[tuple[float, ...]] = []
        for m in range(n_members):
            inputs = _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=[3.0 * m + k for k in range(horizon)],  # distinct per member
                temp=[10.0 + 0.5 * k for k in range(horizon)],
                lag_discharge=[10.0] * _LOOKBACK,
            )
            ensembles, state = adapter.predict(artifact, inputs, random.Random(99))
            assert state is None
            ensemble = ensembles["discharge"]
            # A single call is a single deterministic trajectory (no internal fan-out).
            assert ensemble.member_count == 1
            frame = ensemble.values.sort("valid_time")
            per_member_values.append(tuple(frame["value"].to_list()))
            member_frames.append(
                frame.with_columns(
                    pl.lit(m + 1).cast(pl.Int32).alias("member_id")
                ).select("valid_time", "member_id", "value")
            )

        assembled = ForecastEnsemble.from_members(
            station_id=_SID,
            issued_at=_ISSUE,
            parameter="discharge",
            units="m³/s",
            time_step=_STEP,
            values=pl.concat(member_frames),
        )
        assert assembled.member_count == n_members
        # No collapse/averaging inside the model: each trajectory stays distinct.
        assert len(set(per_member_values)) == n_members

    def test_prediction_is_pure_function_of_input(self) -> None:
        model = NwpRainfallRunoff()
        adapter = _adapter(model)
        artifact = _fit(model)
        horizon = _declared_horizon(model)

        inputs = _station_inputs(
            issue=_ISSUE,
            horizon=horizon,
            precip=[9.0 + k for k in range(horizon)],
            temp=[11.0] * horizon,
            lag_discharge=[10.0] * _LOOKBACK,
        )
        # Deterministic: identical input yields identical output under different seeds.
        first = _adapter_predict_values(adapter, artifact, inputs, seed=1)
        second = _adapter_predict_values(adapter, artifact, inputs, seed=2)
        assert first == second

        different = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=[40.0 + k for k in range(horizon)],
                temp=[11.0] * horizon,
                lag_discharge=[10.0] * _LOOKBACK,
            ),
        )
        assert different != first


# --------------------------------------------------------------------------- #
# 6. Missing-forcing error (adapter max_nan gate → ModelOutputError)
# --------------------------------------------------------------------------- #


class TestMissingForcing:
    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_absent_precip_over_max_nan_raises_model_output_error(
        self, variant: type
    ) -> None:
        model = variant()
        adapter = _adapter(model)
        artifact = _fit(model)
        horizon = _declared_horizon(model)

        inputs = _station_inputs(
            issue=_ISSUE,
            horizon=horizon,
            precip=[None] * horizon,  # required forcing missing beyond max_nan
            temp=[10.0] * horizon,
            lag_discharge=[10.0] * _LOOKBACK,
        )

        with pytest.raises(ModelOutputError, match="max_nan"):
            adapter.predict(artifact, inputs, random.Random(0))


# --------------------------------------------------------------------------- #
# 7. Weather dependence contrast (weather-only vs with-lags)
# --------------------------------------------------------------------------- #


class TestWeatherDependence:
    def test_weather_only_ignores_past_discharge(self) -> None:
        model = NwpRainfallRunoff()
        adapter = _adapter(model)
        artifact = _fit(model)
        horizon = _declared_horizon(model)

        precip = [8.0 + k for k in range(horizon)]
        temp = [12.0] * horizon

        low = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=precip,
                temp=temp,
                lag_discharge=[5.0] * _LOOKBACK,
            ),
        )
        high = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=precip,
                temp=temp,
                lag_discharge=[500.0] * _LOOKBACK,
            ),
        )
        # No discharge feature → forecast invariant to past discharge.
        assert low == high

    def test_with_lags_responds_to_past_discharge(self) -> None:
        model = NwpRegression()
        adapter = _adapter(model)
        artifact = _fit(model)
        horizon = _declared_horizon(model)

        precip = [8.0 + k for k in range(horizon)]
        temp = [12.0] * horizon

        low = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=precip,
                temp=temp,
                lag_discharge=[5.0] * _LOOKBACK,
            ),
        )
        high = _adapter_predict_values(
            adapter,
            artifact,
            _station_inputs(
                issue=_ISSUE,
                horizon=horizon,
                precip=precip,
                temp=temp,
                lag_discharge=[500.0] * _LOOKBACK,
            ),
        )
        # Positive learned lag term → higher past discharge lifts the forecast.
        assert low != high
        assert sum(high) > sum(low)


# --------------------------------------------------------------------------- #
# 8. Adapter training path — the end-to-end pipeline boundary the P1s broke
# --------------------------------------------------------------------------- #


def _fi_station_training_data() -> fi_boundary.StationTrainingData:
    from sapphire_flow.types.model import StationTrainingData

    ts, discharge, precip, temp = _train_series()
    # future_dynamic is timestamp-aligned to past_targets (as the real
    # assemble_station_training_data delivers it); discharge lives in past_targets.
    return StationTrainingData(
        past_targets=_sap_frame(ts, discharge=discharge),
        past_dynamic=_sap_frame(ts),
        future_dynamic=_sap_frame(ts, precipitation=precip, temperature=temp),
        static=None,
        time_step=_STEP,
        val_start=None,
    )


class TestAdapterTrainingPath:
    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_adapter_trains_and_serializes_from_station_training_data(
        self, variant: type
    ) -> None:
        # Under the pre-fix code this KeyErrors for NwpRainfallRunoff (never
        # delivered its obs/discharge fit target) and mis-projects discharge into
        # the forcing channel for NwpRegression.
        model = variant()
        adapter = _adapter(model)
        training = _fi_station_training_data()

        artifact = adapter.train(training, {}, random.Random(0))
        assert isinstance(artifact, NwpRegressionArtifact)

        expected_features = 2 + (_LOOKBACK if variant is NwpRegression else 0)
        assert artifact.coefficients.shape[0] == expected_features

        raw = adapter.serialize_artifact(artifact)
        assert raw
        reloaded = adapter.deserialize_artifact(raw)
        assert isinstance(reloaded, NwpRegressionArtifact)
        assert np.array_equal(reloaded.coefficients, artifact.coefficients)


# --------------------------------------------------------------------------- #
# 9. Insufficient lag history → ModelFailure (FI "return, don't raise" contract)
# --------------------------------------------------------------------------- #


class TestInsufficientLagsReturnsModelFailure:
    def test_predict_returns_model_failure_when_lags_shorter_than_artifact(
        self,
    ) -> None:
        # Artifact trained for 7 lags, but only 3 past-discharge rows delivered:
        # ``_initial_lags`` silently truncates to 3, so the feature vector is one
        # dimension short of the trained coefficients. Per the FI contract this
        # anticipated INPUT_DATA condition must be RETURNED as ModelFailure, not
        # raised as a raw numpy matmul ValueError.
        model = NwpRegression()
        n_lags = 7
        artifact = NwpRegressionArtifact(
            coefficients=np.zeros(2 + n_lags, dtype=np.float64),
            intercept=np.asarray([0.0], dtype=np.float64),
            n_lags=n_lags,
        )
        horizon = _declared_horizon(model)
        inputs = _fi_predict_inputs(
            model,
            issue=_ISSUE,
            horizon=horizon,
            precip=[7.0 + k for k in range(horizon)],
            temp=[10.0] * horizon,
            lag_discharge=[10.0, 11.0, 12.0],  # only 3 rows, need 7
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert "got 3" in result.message
        assert "need 7" in result.message
        assert result.model_name == "nwp_regression"
        assert result.issue_datetime == _ISSUE


# --------------------------------------------------------------------------- #
# 10. Plan 130 Part B — a missing future forcing value must never crash a run
# --------------------------------------------------------------------------- #


class TestMissingFutureValueDoesNotCrashTraining:
    """The reanalysis temperature/precip tail gap (Plan 130) delivers a future
    forcing frame with a NULL value for the newest sample(s) instead of a
    KeyError-style absence. ``_aligned_future``/``train`` must drop the
    affected row(s), not ``float(None)``-crash the whole training call.

    Soundness: fails RED against the pre-fix ``_aligned_future`` (a plain
    ``dict(zip(...))`` lookup fed straight into ``float(...)``) with a real
    ``TypeError: float() argument must be a string or a real number, not
    'NoneType'`` — not a collection/import error.
    """

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_train_drops_row_with_missing_future_temperature_instead_of_raising(
        self, variant: type
    ) -> None:
        model = variant()
        ts, discharge, precip, temp = _train_series()
        temp_with_gap = list(temp)
        temp_with_gap[-1] = None  # the tail-gap sample: no reanalysis temp yet
        inputs = _fi_train_inputs(model, ts, discharge, precip, temp_with_gap)

        artifact = model.train(inputs, config={}, rng=random.Random(0))

        assert isinstance(artifact, NwpRegressionArtifact)

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_train_drops_row_with_future_time_absent_from_frame(
        self, variant: type
    ) -> None:
        # The target time is entirely ABSENT from the future-known frame (not
        # merely null) — the earlier tail-gap edge, where the reanalysis
        # simply has no row yet for that day. The temperature series is one
        # sample short of `ts`/`discharge`, so the LAST target time has no
        # aligned row at all (a dict-lookup miss, not a null value).
        model = variant()
        ts, discharge, precip, temp = _train_series()
        short_ts = ts[:-1]
        short_temp = temp[:-1]
        short_precip = precip[:-1]

        step, rep, spec = _dynamic_spec(model)
        future_known = _future_known_from_spec(spec, short_ts, short_precip, short_temp)
        past_known = {
            "obs": {
                "discharge": _series(
                    ts, "discharge", discharge, fi_boundary.Unit.M3_PER_S
                )
            }
        }
        inputs = _model_inputs(
            model, step=step, rep=rep, past_known=past_known, future_known=future_known
        )

        artifact = model.train(inputs, config={}, rng=random.Random(0))

        assert isinstance(artifact, NwpRegressionArtifact)

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_train_drops_row_with_nan_future_temperature_instead_of_raising(
        self, variant: type
    ) -> None:
        # A PRESENT IEEE NaN (not a null/None) must be treated identically to
        # a missing value: the FI ``max_nan`` gate treats both as missing,
        # and ``Ridge.fit`` raises ``ValueError`` if a literal NaN reaches
        # the design matrix. The None-tail-gap test above does not cover
        # this — a null and a NaN take different code paths in polars.
        #
        # Soundness: fails RED against the pre-fix ``_aligned_future``
        # (``raw is None`` is False for a NaN float, so it is marked
        # ``valid=True`` and reaches ``Ridge.fit``, which raises
        # ``ValueError: Input contains NaN``) instead of returning an
        # artifact.
        model = variant()
        ts, discharge, precip, temp = _train_series()
        temp_with_nan = list(temp)
        temp_with_nan[-1] = float("nan")
        inputs = _fi_train_inputs(model, ts, discharge, precip, temp_with_nan)

        artifact = model.train(inputs, config={}, rng=random.Random(0))

        assert isinstance(artifact, NwpRegressionArtifact)


class TestMissingFutureValueReturnsModelFailureOnPredict:
    """A genuinely missing required future input at predict time is an
    ANTICIPATED condition (FI "return, don't raise" contract) — the model
    must return ``ModelFailure``, never silently emit a NaN-poisoned
    ``ModelSuccess``.

    Soundness: fails RED against the pre-fix ``predict`` (no NaN guard before
    the matmul) — the pre-fix code returns ``ModelSuccess`` with NaN values
    instead of ``ModelFailure``, so ``isinstance(result, ModelFailure)`` fails.
    """

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_predict_returns_model_failure_when_future_precip_missing(
        self, variant: type
    ) -> None:
        model = variant()
        artifact = _fit(model)
        horizon = _declared_horizon(model)
        lags = [10.0] * _LOOKBACK if variant is NwpRegression else None
        precip: list[float | None] = [7.0 + k for k in range(horizon)]
        precip[-1] = None

        inputs = _fi_predict_inputs(
            model,
            issue=_ISSUE,
            horizon=horizon,
            precip=precip,  # type: ignore[arg-type]
            temp=[10.0] * horizon,
            lag_discharge=lags,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert result.issue_datetime == _ISSUE

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_predict_returns_model_failure_when_future_temp_row_absent(
        self, variant: type
    ) -> None:
        # The reanalysis tail gap can ALSO mean the future frame is simply
        # SHORTER than the declared horizon (a row entirely absent, not
        # merely null) — the earlier tail-gap edge. Indexing `temp[step]`
        # for the last horizon step must not IndexError; the missing grid
        # slot must be caught by timestamp-grid alignment and returned as
        # ModelFailure.
        #
        # Soundness: fails RED against the pre-fix `predict` (`horizon =
        # len(future_times)` taken from precip, then plain `temp[step]`
        # indexing) with a real `IndexError: index N is out of bounds`, not
        # a collection/import error.
        model = variant()
        artifact = _fit(model)
        horizon = _declared_horizon(model)
        lags = [10.0] * _LOOKBACK if variant is NwpRegression else None

        step, rep, spec = _dynamic_spec(model)
        future_ts_full = [_ISSUE + (k + 1) * step for k in range(horizon)]
        future_ts_short = future_ts_full[:-1]  # temp's last future row is absent
        precip_vals = [7.0 + k for k in range(horizon)]
        temp_vals_short = [10.0] * (horizon - 1)

        future_known: dict[str, dict[str, fi_boundary.InputSeries]] = {}
        for product, variables in spec.future_known.items():
            inner: dict[str, fi_boundary.InputSeries] = {}
            for name, var in variables.items():
                if name == "precipitation":
                    inner[name] = _series(future_ts_full, name, precip_vals, var.unit)
                else:
                    inner[name] = _series(
                        future_ts_short, name, temp_vals_short, var.unit
                    )
            future_known[product] = inner

        past_known: dict[str, dict[str, fi_boundary.InputSeries]] = {}
        if lags is not None:
            lb = len(lags)
            past_ts = [_ISSUE - (lb - 1 - i) * step for i in range(lb)]
            past_known = {
                "obs": {
                    "discharge": _series(
                        past_ts, "discharge", lags, fi_boundary.Unit.M3_PER_S
                    )
                }
            }
        inputs = _model_inputs(
            model, step=step, rep=rep, past_known=past_known, future_known=future_known
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert result.issue_datetime == _ISSUE

    @pytest.mark.parametrize("variant", _VARIANTS)
    def test_predict_returns_model_failure_when_both_variables_missing_same_tail_row(
        self, variant: type
    ) -> None:
        # Both precip AND temp are missing the SAME tail row (both delivered
        # frames are one row shorter than the declared horizon). The pre-fix
        # code computed `horizon = len(future_times)` from whichever series
        # was inspected, found ZERO NaNs (both arrays are internally
        # consistent-length), and silently returned a ModelSuccess ONE STEP
        # SHORT of the declared horizon instead of failing loudly.
        #
        # Soundness: fails RED against the pre-fix `predict` — it returns
        # `ModelSuccess` with `frame.height == horizon - 1` instead of
        # `ModelFailure`, so `isinstance(result, ModelFailure)` is False.
        model = variant()
        artifact = _fit(model)
        horizon = _declared_horizon(model)
        lags = [10.0] * _LOOKBACK if variant is NwpRegression else None

        step, rep, spec = _dynamic_spec(model)
        future_ts_short = [_ISSUE + (k + 1) * step for k in range(horizon - 1)]
        precip_vals = [7.0 + k for k in range(horizon - 1)]
        temp_vals = [10.0] * (horizon - 1)
        future_known = _future_known_from_spec(
            spec, future_ts_short, precip_vals, temp_vals
        )

        past_known: dict[str, dict[str, fi_boundary.InputSeries]] = {}
        if lags is not None:
            lb = len(lags)
            past_ts = [_ISSUE - (lb - 1 - i) * step for i in range(lb)]
            past_known = {
                "obs": {
                    "discharge": _series(
                        past_ts, "discharge", lags, fi_boundary.Unit.M3_PER_S
                    )
                }
            }
        inputs = _model_inputs(
            model, step=step, rep=rep, past_known=past_known, future_known=future_known
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert result.issue_datetime == _ISSUE
