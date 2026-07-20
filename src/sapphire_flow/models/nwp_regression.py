"""NWP-forced daily regression models (epic-088 M2, extended by Plan 129).

Three ``forecastinterface`` ``ForecastModel`` implementations sharing a base,
consuming future-known precipitation/temperature forcing over a daily step:

* ``NwpRegression`` — daily discharge on future precip/temp windows PLUS past
  discharge lags (declared as ``past_known`` history and used as features).
* ``NwpRainfallRunoff`` — weather-only: daily discharge on future precip/temp
  windows only. It declares the training TARGET (``obs/discharge``, lookback=1)
  as ``past_known`` so the fit target is delivered at train time, but stays
  weather-only in BEHAVIOR: the regression uses only precip/temp features and
  predict is invariant to past discharge.
* ``SeasonalPrecipRunoffRegression`` (Plan 129) — extends the base with a NEW
  ``past_known reanalysis/precipitation`` channel (antecedent precip, the
  RprelimD-consuming channel that closes the temporal gap up to issue-time —
  see ``docs/architecture-context.md`` "RprelimD fetch mechanics") plus a
  derived day-of-year season feature. Keeps future NWP precip/temp + discharge
  lags from the base, so its precipitation input is one continuous covariate
  spanning past-reanalysis (RhiresD deep / RprelimD recent) through
  future-NWP.

Both/all are ``ArtifactScope.STATION``, deterministic single-trajectory models
— the 21-member ensemble is assembled downstream in M3, not inside the model.
"""

from __future__ import annotations

import io
import math
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
_PRODUCT_REANALYSIS = "reanalysis"
_TARGET = "discharge"
_PRECIPITATION = "precipitation"
_TEMPERATURE = "temperature"
_SPATIAL_REP = SpatialRepresentation.BASIN_AVERAGE
# Plan 129: the antecedent-precip lookback overlaps RprelimD's ~2-month
# retention (its documented live-tail is the recent ~45 days up to issue-time)
# so the past-precip fetch is genuinely RprelimD-served near issue-time.
_PRECIP_LOOKBACK_DAYS = 45
_DAYS_PER_YEAR = 365.25


class _ShortForcingWindowError(ValueError):
    """A model-declared EXTRA past-known window is shorter than required.

    Raised by an ``_extra_predict_features`` override for an ANTICIPATED
    short-window condition (fewer raw rows than the declared lookback, no
    explicit NaN — see ``training_data.py`` ``_raw_forcing_to_dataframe``,
    which pivots by row count and does not pad missing days). ``predict()``
    catches this specific exception and returns ``ModelFailure`` — never lets
    it propagate (CLAUDE.md §ForecastInterface Adherence: "anticipated
    failure must be returned, not raised").
    """


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

    def _extra_past_known(self) -> dict[str, dict[str, PastKnownVariable]]:
        """Additional past_known products/variables beyond the target's own history.

        Default: none. A subclass adding a new past-known forcing channel (e.g.
        the RprelimD-consuming antecedent precip, Plan 129) overrides this.
        """
        return {}

    def _extra_train_features(
        self, dynamic: DynamicInputs, target_times: list[datetime]
    ) -> np.ndarray:
        """Extra ``(n_rows, k)`` feature columns aligned to ``target_times``.

        Default: no extra columns (``k = 0``). A subclass declaring
        ``_extra_past_known`` overrides this to compute the matching features.
        """
        del dynamic
        return np.empty((len(target_times), 0), dtype=np.float64)

    def _extra_predict_features(
        self,
        dynamic: DynamicInputs,
        future_times: list[datetime],
        issue_datetime: datetime,
    ) -> np.ndarray:
        """Extra ``(horizon, k)`` feature columns for ``predict()``.

        Default: no extra columns (``k = 0``). Raise
        ``_ShortForcingWindowError`` for an ANTICIPATED short-window
        condition; ``predict()`` catches it and returns ``ModelFailure``.
        """
        del dynamic, issue_datetime
        return np.empty((len(future_times), 0), dtype=np.float64)

    @property
    def input_requirement(self) -> InputRequirement:
        past_known: dict[str, dict[str, PastKnownVariable]] = {
            _PRODUCT_OBS: {
                _TARGET: PastKnownVariable(
                    lookback=self._declared_lookback,
                    max_nan=0,
                    unit=Unit.M3_PER_S,
                )
            },
            **self._extra_past_known(),
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
        precip = _aligned_future(dynamic, _PRECIPITATION, target_times)
        temp = _aligned_future(dynamic, _TEMPERATURE, target_times)
        extra = self._extra_train_features(dynamic, target_times)

        design_rows: list[np.ndarray] = []
        targets: list[float] = []
        for i in range(self._n_lags, len(discharge)):
            features = [precip[i], temp[i], *extra[i].tolist()]
            if self._n_lags:
                features.extend(discharge[i - self._n_lags : i].tolist())
            design_rows.append(np.asarray(features, dtype=np.float64))
            targets.append(float(discharge[i]))

        if not design_rows:
            raise ValueError(
                f"insufficient training rows for {self._model_name}: "
                f"need > {self._n_lags} aligned samples, got {len(discharge)}"
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

        try:
            extra = self._extra_predict_features(dynamic, future_times, issue_datetime)
        except _ShortForcingWindowError as exc:
            log.warning(
                "nwp_regression.short_forcing_window",
                model=self._model_name,
                error=str(exc),
            )
            return ModelFailure(
                model_name=self._model_name,
                issue_datetime=issue_datetime,
                cause=FailureCause.INPUT_DATA,
                message=str(exc),
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
            features = np.concatenate(
                ([precip[step], temp[step], *extra[step].tolist()], lags)
            )
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
) -> np.ndarray:
    frame = dynamic.future_known[_PRODUCT_NWP][name].data.sort("datetime")
    lookup = dict(zip(frame["datetime"].to_list(), frame[name].to_list(), strict=True))
    return np.asarray([float(lookup[ts]) for ts in target_times], dtype=np.float64)


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


def _season_features(times: list[datetime]) -> np.ndarray:
    """Day-of-year season encoding: ``(sin, cos)`` of the annual angle, shape (n, 2)."""
    day_of_year = np.asarray([t.timetuple().tm_yday for t in times], dtype=np.float64)
    angle = 2.0 * math.pi * day_of_year / _DAYS_PER_YEAR
    return np.stack([np.sin(angle), np.cos(angle)], axis=1)


def _antecedent_precip_sums(
    series_times: list[datetime],
    series_values: np.ndarray,
    anchor_times: list[datetime],
    lookback_days: int,
) -> np.ndarray:
    """Sum of ``series_values`` in ``[anchor - lookback_days, anchor)`` per anchor."""
    lookback = timedelta(days=lookback_days)
    times = np.asarray(series_times)
    values = np.asarray(series_values, dtype=np.float64)
    sums = np.empty(len(anchor_times), dtype=np.float64)
    for i, anchor in enumerate(anchor_times):
        window_start = anchor - lookback
        mask = (times >= window_start) & (times < anchor)
        sums[i] = float(values[mask].sum())
    return sums


class SeasonalPrecipRunoffRegression(_NwpRegressionBase):
    """Daily discharge ~ past discharge lags + season + continuous precip.

    Plan 129's consuming model: extends the base with a NEW ``past_known
    reanalysis/precipitation`` channel (antecedent precip, routed through
    ``ModelDataRequirements.past_dynamic_features`` and so through the hybrid
    RhiresD (definitive) / RprelimD (recent, live-tail) reanalysis chain — the
    RprelimD-consuming channel) plus a derived day-of-year season feature.
    Keeps the base's future NWP precip/temp same-day channel and discharge
    lags (1..7), so precipitation is one continuous covariate spanning
    past-reanalysis through future-NWP.

    The declared lookback (``_PRECIP_LOOKBACK_DAYS``) overlaps RprelimD's live
    tail so the antecedent-precip fetch is genuinely RprelimD-served near
    issue-time.

    NOTE — the past-known variable name ``precipitation`` is intentionally
    shared with the base's future_known ``nwp/precipitation``: they are
    disjoint columns in disjoint frames (``past_dynamic`` vs
    ``future_dynamic``), so model-level routing is correct. The adapter's
    generic ``max_nan`` over-tolerance check (``forecast_interface.py``
    ``_variables_over_nan_tolerance``) resolves a shared variable name to
    whichever frame is checked first (``past_dynamic``), so it does not
    independently gate the future NWP precip's NaN count for this model — a
    pre-existing adapter limitation, not built or asserted here (Plan 129
    scopes only the SHORT-window antecedent-precip path below).
    """

    _n_lags = _LOOKBACK
    _declared_lookback = _LOOKBACK
    _model_name = "seasonal_precip_runoff_regression"
    _precip_lookback_days = _PRECIP_LOOKBACK_DAYS

    def _extra_past_known(self) -> dict[str, dict[str, PastKnownVariable]]:
        return {
            _PRODUCT_REANALYSIS: {
                _PRECIPITATION: PastKnownVariable(
                    lookback=self._precip_lookback_days,
                    max_nan=0,
                    unit=Unit.MM,
                )
            }
        }

    def _extra_train_features(
        self, dynamic: DynamicInputs, target_times: list[datetime]
    ) -> np.ndarray:
        reanalysis_times, reanalysis_precip = _sorted_series(
            dynamic.past_known[_PRODUCT_REANALYSIS][_PRECIPITATION], _PRECIPITATION
        )
        antecedent = _antecedent_precip_sums(
            reanalysis_times,
            reanalysis_precip,
            target_times,
            self._precip_lookback_days,
        )
        season = _season_features(target_times)
        return np.column_stack([antecedent, season])

    def _extra_predict_features(
        self,
        dynamic: DynamicInputs,
        future_times: list[datetime],
        issue_datetime: datetime,
    ) -> np.ndarray:
        reanalysis_times, reanalysis_precip = _sorted_series(
            dynamic.past_known[_PRODUCT_REANALYSIS][_PRECIPITATION], _PRECIPITATION
        )
        if len(reanalysis_times) < self._precip_lookback_days:
            raise _ShortForcingWindowError(
                "insufficient antecedent-precip history: got "
                f"{len(reanalysis_times)}, need {self._precip_lookback_days}"
            )
        antecedent_value = float(
            _antecedent_precip_sums(
                reanalysis_times,
                reanalysis_precip,
                [issue_datetime],
                self._precip_lookback_days,
            )[0]
        )
        season = _season_features(future_times)
        antecedent_col = np.full((len(future_times), 1), antecedent_value)
        return np.hstack([antecedent_col, season])
