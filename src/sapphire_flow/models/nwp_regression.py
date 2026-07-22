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
* ``SeasonalPrecipRunoffRegression`` (Plan 129, extended by Plan 138) —
  extends the base with a NEW ``past_known reanalysis/precipitation`` channel
  (antecedent precip, the RprelimD-consuming channel that closes the temporal
  gap up to issue-time — see ``docs/architecture-context.md`` "RprelimD fetch
  mechanics"), a NEW ``past_known reanalysis/temperature`` channel (antecedent
  temperature, Plan 138), plus a derived day-of-year season feature. Keeps
  future NWP precip/temp + discharge lags from the base, so its precipitation
  input is one continuous covariate spanning past-reanalysis (RhiresD deep /
  RprelimD recent) through future-NWP. The antecedent-precip window is a SUM
  (a flux); the antecedent-temperature window is a MEAN (a state, Plan 138
  DC-1).

Both/all are ``ArtifactScope.STATION``, deterministic single-trajectory models
— the 21-member ensemble is assembled downstream in M3, not inside the model.

**Missing future forcing (Plan 130 Part B)**: a missing future precip/temp
value — absent from the frame, a null, or (in ``train``) a non-finite
(NaN/inf) reading — is an ANTICIPATED condition per the FI contract, not a
crash. ``_aligned_future`` (used by ``train``) returns a validity mask
alongside the aligned values — ``train`` drops rows with a missing value
rather than ``float(None)``-crashing or handing ``Ridge`` a NaN. ``predict``
validates the delivered future forcing against the model's declared
``_HORIZON`` FIRST: precip/temp of unequal length or non-identical
timestamps, or fewer than ``_HORIZON`` steps delivered, returns
``ModelFailure`` (cause ``INPUT_DATA``) instead of an ``IndexError`` on
``temp[step]`` or a silently short ``ModelSuccess``. Over-delivery (more
aligned steps than ``_HORIZON``) is forecast in full, preserving the
delivered-timestamp anchoring. It then applies a missing-value guard — a NaN
in the precipitation or temperature array (e.g. a null future value)
likewise returns ``ModelFailure`` rather than emitting a NaN-poisoned
``ModelSuccess``. Only aligned windows of at least ``_HORIZON`` steps reach
the prediction loop.
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
# Plan 138: antecedent temperature is a melt/accumulation-relevant STATE
# (mean, not sum) over a shorter horizon than the precip window.
_TEMP_LOOKBACK_DAYS = 14
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
    # [precip, temp, *subclass extra, (lag_oldest .. lag_newest)]
    coefficients: np.ndarray
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

    def _train_warmup_steps(self) -> int:
        """Leading rows of ``target_times`` to SKIP before fitting.

        Default: ``_n_lags`` (the discharge-lag window). A subclass whose extra
        past-known feature needs a LONGER history than the lag window (e.g. the
        45-day antecedent-precip window, Plan 129) overrides this so early rows
        with a partial extra-feature window never enter the design matrix.
        """
        return self._n_lags

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
        precip, precip_valid = _aligned_future(dynamic, _PRECIPITATION, target_times)
        temp, temp_valid = _aligned_future(dynamic, _TEMPERATURE, target_times)
        valid = precip_valid & temp_valid
        extra = self._extra_train_features(dynamic, target_times)

        # A missing future forcing value (the reanalysis tail gap, Plan 130) is
        # an ANTICIPATED condition per the FI contract, not a crash: drop the
        # affected training sample rather than raising on float(None).
        warmup = self._train_warmup_steps()
        design_rows: list[np.ndarray] = []
        targets: list[float] = []
        dropped = 0
        for i in range(warmup, len(discharge)):
            if not valid[i]:
                dropped += 1
                continue
            features = [precip[i], temp[i], *extra[i].tolist()]
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
                f"need > {warmup} aligned samples, got {len(design_rows)} "
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
            warmup=warmup,
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
        temp_times, temp = _sorted_series(
            dynamic.future_known[_PRODUCT_NWP][_TEMPERATURE], _TEMPERATURE
        )

        # Shape / horizon guard (Plan 130 Part B follow-up) — runs BEFORE the
        # NaN guard and the prediction loop. The reference is the model's own
        # declared ``_HORIZON``, independent of any one delivered frame. Two
        # ANTICIPATED missing-input conditions must return ModelFailure
        # (INPUT_DATA), never crash or silently short-forecast:
        #   * precip/temp of unequal length or non-identical timestamps —
        #     otherwise the loop IndexErrors on ``temp[step]`` when temp is the
        #     shorter series;
        #   * fewer than the declared ``_HORIZON`` steps delivered — otherwise
        #     the model emits a truncated ModelSuccess shorter than its own
        #     contracted horizon.
        # Over-delivery (more than ``_HORIZON`` aligned steps) is tolerated and
        # forecast in full, preserving the pre-existing behaviour where the
        # future window is anchored on the delivered timestamps.
        aligned = future_times == temp_times
        if not aligned or len(future_times) < _HORIZON or len(temp_times) < _HORIZON:
            log.warning(
                "nwp_regression.future_shape_mismatch",
                model=self._model_name,
                horizon=_HORIZON,
                n_precipitation=len(future_times),
                n_temperature=len(temp_times),
                aligned=aligned,
            )
            return ModelFailure(
                model_name=self._model_name,
                issue_datetime=issue_datetime,
                cause=FailureCause.INPUT_DATA,
                message=(
                    "future forcing shape mismatch: expected at least "
                    f"{_HORIZON} aligned precipitation/temperature steps, got "
                    f"precipitation={len(future_times)}, "
                    f"temperature={len(temp_times)}, aligned={aligned}"
                ),
            )
        horizon = len(future_times)

        # A genuinely missing required future input is an ANTICIPATED
        # condition per the FI contract — return ModelFailure, never let NaN
        # silently flow into a "successful" forecast (Plan 130 Part B). This
        # is a value-presence guard only: cross-variable timestamp alignment
        # is out of scope for Plan 130 (see module docstring).
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

        # Plan 129: subclass-declared extra predict features (e.g. the
        # antecedent-precip continuous window). An ANTICIPATED short/stale
        # window raises _ShortForcingWindowError — return ModelFailure, never
        # let it propagate (CLAUDE.md §ForecastInterface Adherence).
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

        # Plan 138 DC-3: an artifact trained against an OLDER feature schema
        # (e.g. before an extra past-known channel was added) has a shorter
        # coefficient vector than the code now produces per step
        # (``[precip, temp, *extra, *lags]``). Guard the shape BEFORE the
        # matmul so a stale artifact returns a clean ModelFailure instead of a
        # raw NumPy shape error or a silent mis-weighted prediction.
        expected_feature_count = 2 + extra.shape[1] + self._n_lags
        if coefficients.shape[0] != expected_feature_count:
            log.warning(
                "nwp_regression.artifact_feature_count_mismatch",
                model=self._model_name,
                got=int(coefficients.shape[0]),
                expected=expected_feature_count,
            )
            return ModelFailure(
                model_name=self._model_name,
                issue_datetime=issue_datetime,
                cause=FailureCause.INPUT_DATA,
                message=(
                    "artifact feature-count mismatch: got "
                    f"{coefficients.shape[0]} expected {expected_feature_count}"
                ),
            )

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
) -> tuple[np.ndarray, np.ndarray]:
    """Align a future-known variable's series to ``target_times``.

    A target time absent from the frame entirely, present with a null value
    (the reanalysis tail-gap case, Plan 130), or present with a non-finite
    value (IEEE NaN/inf — the FI ``max_nan`` gate treats these the same as a
    null), is an ANTICIPATED condition — this returns NaN for that slot and
    ``False`` in the paired boolean mask, instead of crashing on
    ``float(None)`` or letting a NaN reach ``Ridge.fit``. Callers drop
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
            value = float(raw)
            if np.isfinite(value):
                values[i] = value
                valid[i] = True
            else:
                values[i] = np.nan
                valid[i] = False
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


def _season_features(times: list[datetime]) -> np.ndarray:
    """Day-of-year season encoding: ``(sin, cos)`` of the annual angle, shape (n, 2)."""
    day_of_year = np.asarray([t.timetuple().tm_yday for t in times], dtype=np.float64)
    angle = 2.0 * math.pi * day_of_year / _DAYS_PER_YEAR
    return np.stack([np.sin(angle), np.cos(angle)], axis=1)


def _validate_continuous_window(
    times: list[datetime],
    *,
    issue_datetime: datetime,
    lookback_days: int,
    step: timedelta,
    feature_label: str,
) -> None:
    """Raise ``_ShortForcingWindowError`` unless the ``feature_label`` series
    covers a continuous run of ``lookback_days`` distinct CALENDAR DAYS ending
    at the last COMPLETE reanalysis day at-or-before ``issue_datetime``.

    ``feature_label`` (Plan 138) identifies which antecedent window is being
    validated (e.g. ``"antecedent-precip"``, ``"antecedent-temp"``) so the
    ``_ShortForcingWindowError`` message names the actual offending channel
    instead of always saying "antecedent-precip".

    **Daily-cycle anchoring (Plan 129 BUG 1 fix).** Daily reanalysis rows are
    midnight-bucketed calendar days, but the deployment cron issues every 6h
    (``0 */6 * * *`` -> 00/06/12/18Z). Anchoring the window on the exact
    wall-clock issue instant made a non-midnight cycle demand the latest row
    within one daily ``step`` of e.g. 06:00 — an impossible 30/36/42h gap
    against a previous-midnight bucket — so 3 of every 4 normal cycles wrongly
    returned ``ModelFailure``. Anchoring instead on the last complete
    reanalysis DAY (``(issue - step).date()``, always the previous calendar
    day for any cycle on a given day) tolerates that natural daily staleness:
    every cycle on a calendar day requires the same ``lookback_days``
    antecedent days. For a midnight issue this expected day-set is identical to
    the old ``[issue - lookback_days, issue)`` window, so the check is
    unchanged there.

    Bucketing by calendar day (not exact timestamp) is what makes a bare
    row-count check insufficient: 45 rows crammed into a handful of distinct
    days, or 45 rows entirely outside the window, would otherwise pass while
    ``_antecedent_precip_sums`` silently returns a zero/partial feature. A
    stale feed (latest available day older than the anchor) is caught by the
    same check — the recent expected days are simply absent.
    """
    anchor_day = (issue_datetime - step).date()
    expected_days = {anchor_day - timedelta(days=k) for k in range(lookback_days)}
    covered = expected_days & {t.date() for t in times}
    if len(covered) < lookback_days:
        raise _ShortForcingWindowError(
            f"insufficient {feature_label} history: got "
            f"{len(covered)}, need {lookback_days}"
        )


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


def _antecedent_temp_means(
    series_times: list[datetime],
    series_values: np.ndarray,
    anchor_times: list[datetime],
    lookback_days: int,
) -> np.ndarray:
    """Mean of ``series_values`` in ``[anchor - lookback_days, anchor)`` per anchor.

    Plan 138 DC-1: temperature is a STATE, not a flux, so the antecedent
    aggregation is a MEAN (mirroring ``_antecedent_precip_sums``' SUM for the
    flux case). An empty window (should not occur once
    ``_validate_continuous_window`` has passed) yields ``0.0`` rather than a
    NaN-producing empty-slice mean.
    """
    lookback = timedelta(days=lookback_days)
    times = np.asarray(series_times)
    values = np.asarray(series_values, dtype=np.float64)
    means = np.empty(len(anchor_times), dtype=np.float64)
    for i, anchor in enumerate(anchor_times):
        window_start = anchor - lookback
        mask = (times >= window_start) & (times < anchor)
        means[i] = float(values[mask].mean()) if mask.any() else 0.0
    return means


class SeasonalPrecipRunoffRegression(_NwpRegressionBase):
    """Daily discharge ~ past discharge lags + season + continuous precip+temp.

    Plan 129's consuming model, extended by Plan 138: extends the base with a
    NEW ``past_known reanalysis/precipitation`` channel (antecedent precip,
    routed through ``ModelDataRequirements.past_dynamic_features`` and so
    through the hybrid RhiresD (definitive) / RprelimD (recent, live-tail)
    reanalysis chain — the RprelimD-consuming channel) and a NEW ``past_known
    reanalysis/temperature`` channel (antecedent temperature, Plan 138), plus
    a derived day-of-year season feature. Keeps the base's future NWP
    precip/temp same-day channel and discharge lags (1..7), so precipitation
    is one continuous covariate spanning past-reanalysis through future-NWP.

    The feature vector (identical order in ``train``/``predict``, Plan 138
    DC-1) is::

        [precip, temp, antecedent_precip, antecedent_temp,
         season_sin, season_cos, *discharge_lags]

    ``antecedent_precip`` is a SUM (a flux) over ``_precip_lookback_days``;
    ``antecedent_temp`` is a MEAN (a state) over ``_temp_lookback_days`` — a
    shorter, melt/accumulation-relevant horizon.

    The declared precip lookback (``_PRECIP_LOOKBACK_DAYS``) overlaps
    RprelimD's live tail so the antecedent-precip fetch is genuinely
    RprelimD-served near issue-time.

    NOTE — the past-known variable names ``precipitation``/``temperature`` are
    intentionally shared with the base's future_known ``nwp/precipitation``
    and ``nwp/temperature``: they are disjoint columns in disjoint frames
    (``past_dynamic`` vs ``future_dynamic``), so model-level routing is
    correct. The adapter's ``max_nan`` over-tolerance check
    (``forecast_interface.py`` ``_variables_over_nan_tolerance``) gates
    ``past_known`` and ``future_known`` variables independently against their
    own frame, so a shared bare name across the two temporalities does not
    suppress either gate — both this model's past antecedent
    precip/temperature AND the base's future NWP precip/temperature are
    independently NaN-gated before ``predict()`` runs.
    """

    _n_lags = _LOOKBACK
    _declared_lookback = _LOOKBACK
    _model_name = "seasonal_precip_runoff_regression"
    _precip_lookback_days = _PRECIP_LOOKBACK_DAYS
    _temp_lookback_days = _TEMP_LOOKBACK_DAYS

    def _train_warmup_steps(self) -> int:
        # The 45-day antecedent-precip window needs more leading history than
        # the 7-step discharge-lag window (or the 14-day antecedent-temp
        # window); skip rows whose antecedent window would otherwise be
        # partial (Plan 129 post-implementation review; extended Plan 138).
        return max(self._n_lags, self._precip_lookback_days, self._temp_lookback_days)

    def _extra_past_known(self) -> dict[str, dict[str, PastKnownVariable]]:
        return {
            _PRODUCT_REANALYSIS: {
                _PRECIPITATION: PastKnownVariable(
                    lookback=self._precip_lookback_days,
                    max_nan=0,
                    unit=Unit.MM,
                ),
                _TEMPERATURE: PastKnownVariable(
                    lookback=self._temp_lookback_days,
                    max_nan=0,
                    unit=Unit.DEG_C,
                ),
            }
        }

    def _extra_train_features(
        self, dynamic: DynamicInputs, target_times: list[datetime]
    ) -> np.ndarray:
        reanalysis_times, reanalysis_precip = _sorted_series(
            dynamic.past_known[_PRODUCT_REANALYSIS][_PRECIPITATION], _PRECIPITATION
        )
        antecedent_precip = _antecedent_precip_sums(
            reanalysis_times,
            reanalysis_precip,
            target_times,
            self._precip_lookback_days,
        )
        temp_times, reanalysis_temp = _sorted_series(
            dynamic.past_known[_PRODUCT_REANALYSIS][_TEMPERATURE], _TEMPERATURE
        )
        antecedent_temp = _antecedent_temp_means(
            temp_times,
            reanalysis_temp,
            target_times,
            self._temp_lookback_days,
        )
        season = _season_features(target_times)
        return np.column_stack([antecedent_precip, antecedent_temp, season])

    def _extra_predict_features(
        self,
        dynamic: DynamicInputs,
        future_times: list[datetime],
        issue_datetime: datetime,
    ) -> np.ndarray:
        reanalysis_times, reanalysis_precip = _sorted_series(
            dynamic.past_known[_PRODUCT_REANALYSIS][_PRECIPITATION], _PRECIPITATION
        )
        _validate_continuous_window(
            reanalysis_times,
            issue_datetime=issue_datetime,
            lookback_days=self._precip_lookback_days,
            step=_STEP,
            feature_label="antecedent-precip",
        )
        antecedent_precip_value = float(
            _antecedent_precip_sums(
                reanalysis_times,
                reanalysis_precip,
                [issue_datetime],
                self._precip_lookback_days,
            )[0]
        )

        temp_times, reanalysis_temp = _sorted_series(
            dynamic.past_known[_PRODUCT_REANALYSIS][_TEMPERATURE], _TEMPERATURE
        )
        _validate_continuous_window(
            temp_times,
            issue_datetime=issue_datetime,
            lookback_days=self._temp_lookback_days,
            step=_STEP,
            feature_label="antecedent-temp",
        )
        antecedent_temp_value = float(
            _antecedent_temp_means(
                temp_times,
                reanalysis_temp,
                [issue_datetime],
                self._temp_lookback_days,
            )[0]
        )

        season = _season_features(future_times)
        antecedent_precip_col = np.full((len(future_times), 1), antecedent_precip_value)
        antecedent_temp_col = np.full((len(future_times), 1), antecedent_temp_value)
        return np.hstack([antecedent_precip_col, antecedent_temp_col, season])
