"""NWP-forced daily regression models (epic-088 M2).

Two ``forecastinterface`` ``ForecastModel`` implementations that consume
future-known precipitation/temperature forcing over a daily step:

* ``NwpRegression`` — daily discharge on future precip/temp windows PLUS past
  discharge lags (declared as ``past_known`` history and used as features).
* ``NwpRainfallRunoff`` — weather-only: daily discharge on future precip/temp
  windows only. It declares the training TARGET (``obs/discharge``, lookback=1)
  as ``past_known`` so the fit target is delivered at train time, but stays
  weather-only in BEHAVIOR: the regression uses only precip/temp features and
  predict is invariant to past discharge.

Both are ``ArtifactScope.STATION``, deterministic single-trajectory models — the
21-member ensemble is assembled downstream in M3, not inside the model.

**Missing future forcing (Plan 130 Part B)**: a missing future precip/temp
value is an ANTICIPATED condition per the FI contract, not a crash.
``_aligned_future`` (used by ``train``) returns a validity mask alongside the
aligned values — ``train`` drops rows with a missing value rather than
``float(None)``-crashing; ``predict`` returns ``ModelFailure`` (cause
``INPUT_DATA``) rather than emitting a NaN-poisoned ``ModelSuccess``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import numpy as np
import polars as pl
import structlog
from forecast_interface import (
    AggregationMethod,
    ArtifactScope,
    DeterministicData,
    DynamicInputSpec,
    EnsembleMode,
    FailureCause,
    FutureKnownVariable,
    InputRequirement,
    ModelFailure,
    ModelOutput,
    ModelResult,
    ModelSuccess,
    OutputRepresentation,
    PastKnownVariable,
    SpatialInputSpec,
    SpatialRepresentation,
    TargetSpec,
    Unit,
    VariableMetadata,
    VariableOutput,
    VariableStatus,
)
from sklearn.linear_model import Ridge

if TYPE_CHECKING:
    import random
    from datetime import datetime

    from forecast_interface import DynamicInputs, InputSeries, ModelInputs

log = structlog.get_logger(__name__)

_STEP = timedelta(days=1)
_HORIZON = 5  # M3: match ICON-CH2-EPS 5-day / 120h coverage (was 7)
_LOOKBACK = 7
_PRODUCT_NWP = "nwp"
_PRODUCT_OBS = "obs"
_TARGET = "discharge"
_PRECIPITATION = "precipitation"
_TEMPERATURE = "temperature"
_SPATIAL_REP = SpatialRepresentation.BASIN_AVERAGE


@dataclass(frozen=True, kw_only=True, slots=True)
class NwpRegressionArtifact:
    coefficients: np.ndarray  # [precip, temp, (lag_oldest .. lag_newest)]
    intercept: np.ndarray  # (1,)
    n_lags: int


def _dynamic_inputs(inputs: ModelInputs) -> tuple[str, DynamicInputs]:
    station_key, station = next(iter(inputs.stations.items()))
    spatial = next(iter(station.dynamic.values()))
    dynamic = next(iter(spatial.data.values()))
    return station_key, dynamic


def _sorted_series(series: InputSeries, name: str) -> tuple[list[datetime], np.ndarray]:
    frame = series.data.sort("datetime")
    times = frame["datetime"].to_list()
    values = frame[name].to_numpy().astype(np.float64)
    return times, values


class _NwpRegressionBase:
    artifact_scope: ArtifactScope = ArtifactScope.STATION
    _n_lags: int = 0
    # Declared past-known target history. Always >= 1 so the fit target is
    # delivered from the target channel (past_targets) at train time. For the
    # with-lags variant this equals the lag window used as features; for the
    # weather-only variant it is the minimal 1 step (target-only, no feature).
    _declared_lookback: int = 1
    _model_name: str = "nwp-regression-base"

    @property
    def input_requirement(self) -> InputRequirement:
        past_known: dict[str, dict[str, PastKnownVariable]] = {
            _PRODUCT_OBS: {
                _TARGET: PastKnownVariable(
                    lookback=self._declared_lookback,
                    max_nan=0,
                    unit=Unit.M3_PER_S,
                )
            }
        }
        return InputRequirement(
            targets={
                _TARGET: TargetSpec(
                    unit=Unit.M3_PER_S,
                    representations=frozenset({OutputRepresentation.DETERMINISTIC}),
                )
            },
            dynamic={
                _STEP: SpatialInputSpec(
                    data={
                        _SPATIAL_REP: DynamicInputSpec(
                            past_known=past_known,
                            future_known={
                                _PRODUCT_NWP: {
                                    _PRECIPITATION: FutureKnownVariable(
                                        future_steps=_HORIZON,
                                        max_nan=0,
                                        unit=Unit.MM,
                                        aggregation=AggregationMethod.SUM,
                                        ensemble_mode=EnsembleMode.ENSEMBLE,
                                    ),
                                    _TEMPERATURE: FutureKnownVariable(
                                        future_steps=_HORIZON,
                                        max_nan=0,
                                        unit=Unit.DEG_C,
                                        aggregation=AggregationMethod.MEAN,
                                        ensemble_mode=EnsembleMode.ENSEMBLE,
                                    ),
                                }
                            },
                        )
                    }
                )
            },
        )

    def train(
        self,
        inputs: ModelInputs,
        *,
        config: object,
        rng: random.Random,
    ) -> NwpRegressionArtifact:
        del rng  # ridge fit is deterministic; no injected randomness needed
        _station_key, dynamic = _dynamic_inputs(inputs)

        target_times, discharge = _sorted_series(
            dynamic.past_known[_PRODUCT_OBS][_TARGET], _TARGET
        )
        precip, precip_valid = _aligned_future(dynamic, _PRECIPITATION, target_times)
        temp, temp_valid = _aligned_future(dynamic, _TEMPERATURE, target_times)
        valid = precip_valid & temp_valid

        # A missing future forcing value (the reanalysis tail gap, Plan 130) is
        # an ANTICIPATED condition per the FI contract, not a crash: drop the
        # affected training sample rather than raising on float(None).
        design_rows: list[np.ndarray] = []
        targets: list[float] = []
        dropped = 0
        for i in range(self._n_lags, len(discharge)):
            if not valid[i]:
                dropped += 1
                continue
            features = [precip[i], temp[i]]
            if self._n_lags:
                features.extend(discharge[i - self._n_lags : i].tolist())
            design_rows.append(np.asarray(features, dtype=np.float64))
            targets.append(float(discharge[i]))

        if dropped:
            log.warning(
                "model.training_rows_dropped_missing_future",
                model=self._model_name,
                dropped=dropped,
                kept=len(design_rows),
            )

        if not design_rows:
            raise ValueError(
                f"insufficient training rows for {self._model_name}: "
                f"need > {self._n_lags} aligned samples, got {len(design_rows)} "
                f"after dropping {dropped} row(s) with missing future forcing "
                f"(of {len(discharge)} total)"
            )

        alpha = _alpha_from_config(config)
        ridge = Ridge(alpha=alpha)
        ridge.fit(np.stack(design_rows), np.asarray(targets, dtype=np.float64))

        log.debug(
            "model.training_completed",
            model=self._model_name,
            n_samples=len(design_rows),
            n_features=int(np.stack(design_rows).shape[1]),
            n_lags=self._n_lags,
        )

        return NwpRegressionArtifact(
            coefficients=np.asarray(ridge.coef_, dtype=np.float64),
            intercept=np.asarray([ridge.intercept_], dtype=np.float64),
            n_lags=self._n_lags,
        )

    def predict(
        self,
        artifact: NwpRegressionArtifact,
        *,
        inputs: ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> ModelResult:
        del rng  # deterministic single trajectory; output is a pure function of input
        station_key, dynamic = _dynamic_inputs(inputs)

        future_times, precip = _sorted_series(
            dynamic.future_known[_PRODUCT_NWP][_PRECIPITATION], _PRECIPITATION
        )
        _temp_times, temp = _sorted_series(
            dynamic.future_known[_PRODUCT_NWP][_TEMPERATURE], _TEMPERATURE
        )
        horizon = len(future_times)

        # A genuinely missing required future input is an ANTICIPATED
        # condition per the FI contract — return ModelFailure, never let NaN
        # silently flow into a "successful" forecast (Plan 130 Part B).
        missing_precip = int(np.isnan(precip).sum())
        missing_temp = int(np.isnan(temp).sum())
        if missing_precip or missing_temp:
            log.warning(
                "nwp_regression.missing_future_forcing",
                model=self._model_name,
                missing_precipitation=missing_precip,
                missing_temperature=missing_temp,
                horizon=horizon,
            )
            return ModelFailure(
                model_name=self._model_name,
                issue_datetime=issue_datetime,
                cause=FailureCause.INPUT_DATA,
                message=(
                    "missing required future forcing: "
                    f"precipitation={missing_precip}, temperature={missing_temp} "
                    f"missing of {horizon} steps"
                ),
            )

        lags = self._initial_lags(dynamic)
        if len(lags) != artifact.n_lags:
            log.warning(
                "nwp_regression.insufficient_lags",
                got=len(lags),
                need=artifact.n_lags,
            )
            return ModelFailure(
                model_name=self._model_name,
                issue_datetime=issue_datetime,
                cause=FailureCause.INPUT_DATA,
                message=(
                    f"insufficient lag history: got {len(lags)}, need {artifact.n_lags}"
                ),
            )
        coefficients = np.asarray(artifact.coefficients, dtype=np.float64)
        intercept = float(artifact.intercept[0])

        predictions: list[float] = []
        for step in range(horizon):
            features = np.concatenate(([precip[step], temp[step]], lags))
            value = float(features @ coefficients + intercept)
            predictions.append(value)
            if self._n_lags:
                lags = np.concatenate((lags[1:], [value]))

        frame = pl.DataFrame(
            {
                "issue_datetime": [issue_datetime] * horizon,
                "datetime": future_times,
                "value": predictions,
            }
        ).with_columns(
            pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
            pl.col("datetime").cast(pl.Datetime("us", "UTC")),
            pl.col("value").cast(pl.Float64),
        )

        variable = VariableOutput(
            metadata=VariableMetadata(
                unit=Unit.M3_PER_S,
                timedelta=_STEP,
                forecast_horizon=horizon,
                offset=0,
            ),
            deterministic=DeterministicData(data=frame),
            flags=frozenset(),
            status=VariableStatus.SUCCESS,
        )
        return ModelSuccess(
            output=ModelOutput(
                model_name=self._model_name,
                issue_datetime=issue_datetime,
                variables={station_key: {_TARGET: variable}},
            )
        )

    def _initial_lags(self, dynamic: DynamicInputs) -> np.ndarray:
        if not self._n_lags:
            return np.empty(0, dtype=np.float64)
        series = dynamic.past_known[_PRODUCT_OBS][_TARGET]
        _times, values = _sorted_series(series, _TARGET)
        return np.asarray(values[-self._n_lags :], dtype=np.float64)

    def serialize_artifact(self, artifact: NwpRegressionArtifact) -> bytes:
        buffer = io.BytesIO()
        np.savez_compressed(
            buffer,
            coefficients=artifact.coefficients,
            intercept=artifact.intercept,
            n_lags=np.asarray([artifact.n_lags], dtype=np.int64),
        )
        return buffer.getvalue()

    def deserialize_artifact(self, raw: bytes) -> NwpRegressionArtifact:
        data = np.load(io.BytesIO(raw), allow_pickle=False)
        missing = {"coefficients", "intercept", "n_lags"} - set(data.files)
        if missing:
            raise ValueError(f"artifact missing keys: {sorted(missing)}")
        return NwpRegressionArtifact(
            coefficients=np.asarray(data["coefficients"], dtype=np.float64),
            intercept=np.asarray(data["intercept"], dtype=np.float64),
            n_lags=int(data["n_lags"][0]),
        )


def _aligned_future(
    dynamic: DynamicInputs, name: str, target_times: list[datetime]
) -> tuple[np.ndarray, np.ndarray]:
    """Align a future-known variable's series to ``target_times``.

    A target time absent from the frame entirely, or present with a null
    value (the reanalysis tail-gap case, Plan 130), is an ANTICIPATED
    condition — this returns NaN for that slot and ``False`` in the paired
    boolean mask, instead of crashing on ``float(None)``. Callers drop
    invalid rows rather than train/predict on them.
    """
    frame = dynamic.future_known[_PRODUCT_NWP][name].data.sort("datetime")
    lookup = dict(zip(frame["datetime"].to_list(), frame[name].to_list(), strict=True))
    values = np.empty(len(target_times), dtype=np.float64)
    valid = np.empty(len(target_times), dtype=np.bool_)
    for i, ts in enumerate(target_times):
        raw = lookup.get(ts)
        if raw is None:
            values[i] = np.nan
            valid[i] = False
        else:
            values[i] = float(raw)
            valid[i] = True
    return values, valid


def _alpha_from_config(config: object) -> float:
    if isinstance(config, dict):
        raw = cast("dict[str, object]", config).get("alpha", 1.0)
        if isinstance(raw, int | float):
            return float(raw)
    return 1.0


class NwpRegression(_NwpRegressionBase):
    """Daily discharge ~ future precip/temp + past discharge lags (1..7)."""

    _n_lags = _LOOKBACK
    _declared_lookback = _LOOKBACK
    _model_name = "nwp_regression"


class NwpRainfallRunoff(_NwpRegressionBase):
    """Daily discharge ~ future precip/temp only (weather-only rainfall-runoff).

    Declares the training TARGET (obs/discharge, lookback=1) so the fit target is
    delivered at train time; uses no discharge feature and ignores past discharge
    at predict.
    """

    _n_lags = 0
    _declared_lookback = 1
    _model_name = "nwp_rainfall_runoff"
