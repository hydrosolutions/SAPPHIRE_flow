"""LOCKED failing-first tests for the Plan 129 consuming model (M1/M2).

``SeasonalPrecipRunoffRegression`` extends ``_NwpRegressionBase`` with a NEW
past_known ``reanalysis/precipitation`` channel (the RprelimD-consuming
antecedent-precip feature) and a derived day-of-year season feature, keeping
the base's future NWP precip/temp + discharge lags.

These tests prove:

1. The past-precip channel actually ROUTES through
   ``ModelDataRequirements.past_dynamic_features`` and the assembled
   ``past_dynamic`` frame (training AND operational) — not merely that a
   fetch happened (NWP-only models already fetch precip for future
   teacher-forcing).
2. A SHORT antecedent-precip window (fewer raw rows than the declared
   lookback, no explicit NaN) is the model's own anticipated failure:
   ``predict()`` returns ``ModelFailure``, never raises (CLAUDE.md
   §ForecastInterface Adherence).
3. Continuous-series assembly: with reanalysis rows present up to
   issue-time (representing RprelimD's live tail), the operational
   past-precip fetch reaches issue-time — no gap before the NWP future
   precip.
4. Training excludes leading rows with a PARTIAL 45-day antecedent-precip
   window (Plan 129 post-implementation review, warmup fix).
5. predict() rejects a STALE or DUPLICATE-timestamp antecedent-precip window
   that a bare row-count check would let through (Plan 129
   post-implementation review, continuity-validation fix).
6. The FI adapter's max_nan gate independently covers this model's future
   NWP precip channel despite sharing a bare name with the past reanalysis
   channel (Plan 129 post-implementation review, temporality-aware gating).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
from forecast_interface import FailureCause, ModelFailure
from structlog.testing import capture_logs

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.models.nwp_regression import (
    NwpRainfallRunoff,
    NwpRegression,
    SeasonalPrecipRunoffRegression,
    _dynamic_inputs,
)
from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.services.training_data import assemble_station_training_data
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import (
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_STEP = timedelta(days=1)
_SID = StationId(uuid4())
_ISSUE = ensure_utc(datetime(2026, 3, 1, tzinfo=UTC))
_MODEL_ID = ModelId("seasonal_precip_runoff_regression")
_NWP_SOURCE = "icon_ch2_eps"


def _adapter(model: object) -> fi_boundary.ForecastInterfaceAdapter:
    adapted = fi_boundary.adapt_if_fi(model, station_code_resolver=lambda _sid: "gauge")
    assert isinstance(adapted, fi_boundary.ForecastInterfaceAdapter)
    return adapted


# --------------------------------------------------------------------------- #
# 1. Requirements — past-precip channel is declared and routes through
#    ModelDataRequirements.past_dynamic_features
# --------------------------------------------------------------------------- #


class TestPastPrecipRequirementRouting:
    def test_past_dynamic_features_includes_precipitation(self) -> None:
        adapter = _adapter(SeasonalPrecipRunoffRegression())
        assert "precipitation" in adapter.data_requirements.past_dynamic_features

    def test_past_dynamic_features_includes_temperature(self) -> None:
        # Plan 138: the new past-known reanalysis/temperature channel must
        # route through ModelDataRequirements.past_dynamic_features exactly
        # like the existing past-precip channel.
        adapter = _adapter(SeasonalPrecipRunoffRegression())
        assert "temperature" in adapter.data_requirements.past_dynamic_features

    def test_nwp_only_variants_do_not_declare_past_precip(self) -> None:
        # Soundness contrast: today's NWP-only models fetch precip only for
        # FUTURE teacher-forcing — their past_dynamic_features is empty. If this
        # assertion were checking "a fetch happened" rather than routing
        # specifically through past_dynamic_features, these would wrongly pass
        # too.
        assert _adapter(NwpRegression()).data_requirements.past_dynamic_features == (
            frozenset()
        )
        assert (
            _adapter(NwpRainfallRunoff()).data_requirements.past_dynamic_features
            == frozenset()
        )

    def test_lookback_overlaps_rprelimd_live_tail(self) -> None:
        reqs = _adapter(SeasonalPrecipRunoffRegression()).data_requirements
        assert reqs.lookback_steps >= 45


# --------------------------------------------------------------------------- #
# 2. Assembled training data — past_dynamic actually contains precipitation
# --------------------------------------------------------------------------- #


def _make_forcing(station_id: StationId, n: int, start: datetime) -> list:
    records = []
    for i in range(n):
        ts = ensure_utc(start + i * _STEP)
        records.append(
            make_raw_historical_forcing(
                station_id=station_id,
                parameter="precipitation",
                valid_time=ts,
                value=float(i % 5),
            )
        )
        records.append(
            make_raw_historical_forcing(
                station_id=station_id,
                parameter="temperature",
                valid_time=ts,
                value=float(10 + i % 5),
            )
        )
    return records


def _reanalysis_weather_source(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


class TestAssembledTrainingDataRoutesPastPrecip:
    def test_training_past_dynamic_contains_precipitation(self) -> None:
        model = _adapter(SeasonalPrecipRunoffRegression())
        station_id = StationId(uuid4())
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2020, 6, 1, tzinfo=UTC)

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        station_store.store_weather_source(_reanalysis_weather_source(station_id))
        obs_store.store_observations(
            make_observations(
                n=90,
                station_id=station_id,
                start=ensure_utc(start),
                interval=_STEP,
            )
        )
        forcing_records = _make_forcing(station_id, n=150, start=start)

        result = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=ensure_utc(start),
            period_end=ensure_utc(end),
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert "precipitation" in result.past_dynamic.columns
        assert not result.past_dynamic["precipitation"].is_empty()
        # Plan 138: the NEW past_known reanalysis/temperature channel must
        # route through assembly too, not just precipitation — a broken
        # training-assembly path that drops temperature (e.g. an
        # over-narrow column filter) would otherwise pass this test silently.
        assert "temperature" in result.past_dynamic.columns
        assert not result.past_dynamic["temperature"].is_empty()


# --------------------------------------------------------------------------- #
# 3. Continuous-series assembly — operational past-precip fetch reaches
#    issue-time (no gap before NWP future precip)
# --------------------------------------------------------------------------- #


def _nwp_records(
    station_id: StationId, issue: datetime, horizon_days: int, n_members: int = 2
) -> list[WeatherForecastRecord]:
    records = []
    for hour in range(1, horizon_days * 24 + 1):
        vt = ensure_utc(issue + timedelta(hours=hour))
        for param, base in (("precipitation", 1.0), ("temperature", 12.0)):
            for member in range(n_members):
                records.append(
                    WeatherForecastRecord(
                        id=uuid4(),
                        station_id=station_id,
                        nwp_source=_NWP_SOURCE,
                        cycle_time=ensure_utc(issue - timedelta(hours=6)),
                        valid_time=vt,
                        parameter=param,
                        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                        band_id=None,
                        member_id=member,
                        value=base + member,
                        created_at=issue,
                    )
                )
    return records


class TestContinuousSeriesAssembly:
    def test_operational_past_precip_reaches_issue_time_when_recent_rows_present(
        self,
    ) -> None:
        model = _adapter(SeasonalPrecipRunoffRegression())
        reqs = model.data_requirements
        lookback_days = reqs.lookback_steps

        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        state_store = FakeModelStateStore()
        reanalysis = FakeWeatherReanalysisSource()

        station_store.store_station(make_station_config(station_id=_SID))
        station_store.store_weather_source(_reanalysis_weather_source(_SID))

        obs_start = ensure_utc(_ISSUE - lookback_days * _STEP)
        obs_store.store_observations(
            make_observations(
                n=lookback_days,
                station_id=_SID,
                parameter="discharge",
                start=obs_start,
                interval=_STEP,
            )
        )
        # Forcing rows span the FULL lookback window through the day BEFORE
        # issue-time — representing RprelimD's live tail closing the gap.
        forcing_records = _make_forcing(_SID, n=lookback_days, start=obs_start)
        reanalysis.set_records(forcing_records)
        nwp_store.store_weather_forecasts(
            _nwp_records(_SID, _ISSUE, reqs.forecast_horizon_steps)
        )
        state_store.store_state(_SID, _MODEL_ID, ensure_utc(_ISSUE - _STEP), b"state")

        result = assemble_station_operational_inputs(
            station_id=_SID,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=ensure_utc(_ISSUE - timedelta(hours=6)),
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=lambda: _ISSUE,
            forecast_horizon_steps=reqs.forecast_horizon_steps,
            time_step=_STEP,
        )

        assert result is not None
        inputs, _metadata = result
        assert "precipitation" in inputs.data.past_dynamic.columns
        past_precip = inputs.data.past_dynamic.drop_nulls("precipitation")
        assert not past_precip.is_empty()
        latest_past_precip_ts = past_precip["timestamp"].max()
        # Soundness: if RprelimD were NOT consumed, the fetched window would
        # stop at some older definitive-product boundary, well short of
        # issue-time. "Reaches issue-time" == within one step of issue.
        assert isinstance(latest_past_precip_ts, datetime)
        gap = _ISSUE - ensure_utc(latest_past_precip_ts)
        assert gap <= _STEP

        # Plan 138: the NEW past temperature channel must reach the SAME
        # live-tail boundary as past precipitation, not merely be present —
        # a broken operational assembly path that fails to extend the
        # temperature fetch to issue-time would otherwise pass silently.
        assert "temperature" in inputs.data.past_dynamic.columns
        past_temp = inputs.data.past_dynamic.drop_nulls("temperature")
        assert not past_temp.is_empty()
        latest_past_temp_ts = past_temp["timestamp"].max()
        assert isinstance(latest_past_temp_ts, datetime)
        assert ensure_utc(latest_past_temp_ts) == ensure_utc(latest_past_precip_ts)

        # The Plan 129 claim itself is "no gap before the NWP future precip"
        # (past_dynamic reaching issue-time is only a proxy) — assert the
        # actual seam: the first FUTURE precip bucket must pick up exactly
        # one time_step after the last PAST precip bucket, not a calendar
        # day beyond it.
        future_dynamic = inputs.data.future_dynamic
        precip_cols = [
            c
            for c in future_dynamic.columns
            if c == "precipitation" or c.startswith("precipitation_")
        ]
        assert precip_cols
        assert not future_dynamic.is_empty()
        earliest_future_ts = future_dynamic["timestamp"].min()
        assert isinstance(earliest_future_ts, datetime)
        seam_gap = ensure_utc(earliest_future_ts) - ensure_utc(latest_past_precip_ts)
        assert seam_gap == _STEP

    def test_stale_reanalysis_feed_does_not_falsely_reach_issue_time(self) -> None:
        # Contrast case proving the assertion above discriminates: seed
        # forcing rows only up to a stale boundary (simulating RprelimD NOT
        # having been consumed) and confirm the gap is genuinely large.
        model = _adapter(SeasonalPrecipRunoffRegression())
        reqs = model.data_requirements
        lookback_days = reqs.lookback_steps

        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        state_store = FakeModelStateStore()
        reanalysis = FakeWeatherReanalysisSource()

        station_store.store_station(make_station_config(station_id=_SID))
        station_store.store_weather_source(_reanalysis_weather_source(_SID))

        obs_start = ensure_utc(_ISSUE - lookback_days * _STEP)
        obs_store.store_observations(
            make_observations(
                n=lookback_days,
                station_id=_SID,
                parameter="discharge",
                start=obs_start,
                interval=_STEP,
            )
        )
        # Stale reanalysis feed: rows stop 10 days before issue-time (the
        # RprelimD-not-consumed scenario).
        stale_cutoff_days = lookback_days - 10
        forcing_records = _make_forcing(_SID, n=stale_cutoff_days, start=obs_start)
        reanalysis.set_records(forcing_records)
        nwp_store.store_weather_forecasts(
            _nwp_records(_SID, _ISSUE, reqs.forecast_horizon_steps)
        )
        state_store.store_state(_SID, _MODEL_ID, ensure_utc(_ISSUE - _STEP), b"state")

        result = assemble_station_operational_inputs(
            station_id=_SID,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=ensure_utc(_ISSUE - timedelta(hours=6)),
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=lambda: _ISSUE,
            forecast_horizon_steps=reqs.forecast_horizon_steps,
            time_step=_STEP,
        )

        assert result is not None
        inputs, _metadata = result
        assert "precipitation" in inputs.data.past_dynamic.columns
        past_precip = inputs.data.past_dynamic.drop_nulls("precipitation")
        assert not past_precip.is_empty()
        latest_past_precip_ts = past_precip["timestamp"].max()
        assert isinstance(latest_past_precip_ts, datetime)
        gap = _ISSUE - ensure_utc(latest_past_precip_ts)
        assert gap >= timedelta(days=9)


# --------------------------------------------------------------------------- #
# 4. SHORT antecedent-precip window -> ModelFailure (FI "return, don't raise")
# --------------------------------------------------------------------------- #


def _fi_frame(ts: Sequence[datetime], name: str, vals: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"datetime": list(ts), name: vals}).with_columns(
        pl.col("datetime").cast(pl.Datetime("us", "UTC"))
    )


def _series(
    ts: Sequence[datetime], name: str, vals: list[float], unit: fi_boundary.Unit
) -> fi_boundary.InputSeries:
    return fi_boundary.InputSeries(unit=unit, data=_fi_frame(ts, name, vals))


def _train_series(
    n: int, base: datetime
) -> tuple[
    list[datetime], list[float], list[float], list[float], list[float], list[float]
]:
    rng = random.Random(20260301)
    ts = [base + i * _STEP for i in range(n)]
    precip = [round(rng.uniform(0.0, 20.0), 4) for _ in range(n)]
    temp = [round(rng.uniform(-5.0, 25.0), 4) for _ in range(n)]
    reanalysis_precip = [round(rng.uniform(0.0, 15.0), 4) for _ in range(n)]
    reanalysis_temp = [round(rng.uniform(-5.0, 20.0), 4) for _ in range(n)]
    discharge = [10.0] * n
    for i in range(1, n):
        discharge[i] = (
            2.0
            + 0.4 * discharge[i - 1]
            + 5.0 * precip[i]
            + 0.5 * temp[i]
            + 0.1 * reanalysis_precip[i]
            + 0.2 * reanalysis_temp[i]
        )
    return ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp


def _seasonal_dynamic_inputs(
    ts: list[datetime],
    discharge: list[float],
    precip: list[float],
    temp: list[float],
    reanalysis_precip: list[float],
    reanalysis_temp: list[float],
) -> fi_boundary.DynamicInputs:
    return fi_boundary.DynamicInputs(
        past_known={
            "obs": {
                "discharge": _series(
                    ts, "discharge", discharge, fi_boundary.Unit.M3_PER_S
                )
            },
            "reanalysis": {
                "precipitation": _series(
                    ts, "precipitation", reanalysis_precip, fi_boundary.Unit.MM
                ),
                "temperature": _series(
                    ts, "temperature", reanalysis_temp, fi_boundary.Unit.DEG_C
                ),
            },
        },
        future_known={
            "nwp": {
                "precipitation": _series(
                    ts, "precipitation", precip, fi_boundary.Unit.MM
                ),
                "temperature": _series(ts, "temperature", temp, fi_boundary.Unit.DEG_C),
            }
        },
    )


def _fit_seasonal_model() -> tuple[SeasonalPrecipRunoffRegression, object]:
    model = SeasonalPrecipRunoffRegression()
    n = 120  # > _PRECIP_LOOKBACK_DAYS (45) + lookback lags, for real windows
    base = datetime(2024, 1, 1, tzinfo=UTC)
    ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp = _train_series(
        n, base
    )

    dynamic = _seasonal_dynamic_inputs(
        ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp
    )
    station = fi_boundary.StationInputs(
        dynamic={_STEP: fi_boundary.SpatialInputs(data={_spatial_rep(): dynamic})},
        static={},
    )
    inputs = fi_boundary.ModelInputs(stations={"station": station})
    artifact = model.train(inputs, config={}, rng=random.Random(0))
    return model, artifact


def _spatial_rep() -> fi_boundary.FISpatialRepresentation:
    req = SeasonalPrecipRunoffRegression().input_requirement
    _step, spatial = next(iter(req.dynamic.items()))
    rep, _spec = next(iter(spatial.data.items()))
    return rep


_DEFAULT_TEMP_LOOKBACK_DAYS = 14


def _default_temp_window(
    issue: datetime, lookback_days: int = _DEFAULT_TEMP_LOOKBACK_DAYS
) -> tuple[list[datetime], list[float]]:
    """A full, continuous antecedent-temperature window ending the day before
    ``issue`` — the "everything is fine" default so tests that vary the
    PRECIP window in isolation don't also trip the independent temp-window
    guard."""
    ts = [issue - (lookback_days - i) * _STEP for i in range(lookback_days)]
    return ts, [10.0] * lookback_days


def _predict_inputs(
    *,
    issue: datetime,
    horizon: int,
    reanalysis_precip: list[float],
    lag_discharge: list[float],
) -> fi_boundary.ModelInputs:
    rp_n = len(reanalysis_precip)
    rp_ts = [issue - (rp_n - i) * _STEP for i in range(rp_n)]
    return _predict_inputs_custom_reanalysis(
        issue=issue,
        horizon=horizon,
        reanalysis_ts=rp_ts,
        reanalysis_vals=reanalysis_precip,
        lag_discharge=lag_discharge,
    )


def _predict_inputs_custom_reanalysis(
    *,
    issue: datetime,
    horizon: int,
    reanalysis_ts: Sequence[datetime],
    reanalysis_vals: list[float],
    lag_discharge: list[float],
    reanalysis_temp_ts: Sequence[datetime] | None = None,
    reanalysis_temp_vals: list[float] | None = None,
) -> fi_boundary.ModelInputs:
    """Like ``_predict_inputs`` but with EXPLICIT antecedent-precip timestamps
    — used to construct stale/duplicate-timestamp windows that a plain
    ``reanalysis_precip: list[float]`` count cannot express.

    ``reanalysis_temp_ts``/``reanalysis_temp_vals`` (Plan 138) default to a
    full, valid antecedent-temperature window so tests exercising the PRECIP
    window in isolation are unaffected by the independent temp-window guard;
    pass both explicitly to exercise the temp window itself.
    """
    future_ts = [issue + (k + 1) * _STEP for k in range(horizon)]
    lb = len(lag_discharge)
    lag_ts = [issue - (lb - 1 - i) * _STEP for i in range(lb)]
    if reanalysis_temp_ts is None or reanalysis_temp_vals is None:
        reanalysis_temp_ts, reanalysis_temp_vals = _default_temp_window(issue)

    dynamic = fi_boundary.DynamicInputs(
        past_known={
            "obs": {
                "discharge": _series(
                    lag_ts, "discharge", lag_discharge, fi_boundary.Unit.M3_PER_S
                )
            },
            "reanalysis": {
                "precipitation": _series(
                    reanalysis_ts, "precipitation", reanalysis_vals, fi_boundary.Unit.MM
                ),
                "temperature": _series(
                    reanalysis_temp_ts,
                    "temperature",
                    reanalysis_temp_vals,
                    fi_boundary.Unit.DEG_C,
                ),
            },
        },
        future_known={
            "nwp": {
                "precipitation": _series(
                    future_ts,
                    "precipitation",
                    [5.0 + k for k in range(horizon)],
                    fi_boundary.Unit.MM,
                ),
                "temperature": _series(
                    future_ts, "temperature", [10.0] * horizon, fi_boundary.Unit.DEG_C
                ),
            }
        },
    )
    station = fi_boundary.StationInputs(
        dynamic={_STEP: fi_boundary.SpatialInputs(data={_spatial_rep(): dynamic})},
        static={},
    )
    return fi_boundary.ModelInputs(stations={"station": station})


class TestShortAntecedentPrecipWindowReturnsModelFailure:
    def test_predict_returns_model_failure_for_short_reanalysis_window(self) -> None:
        model, artifact = _fit_seasonal_model()
        horizon = 5

        inputs = _predict_inputs(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_precip=[3.0, 4.0, 5.0],  # only 3 rows, need 45
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert "got 3" in result.message
        assert "need 45" in result.message
        assert result.model_name == "seasonal_precip_runoff_regression"

    def test_predict_succeeds_with_full_reanalysis_window(self) -> None:
        model, artifact = _fit_seasonal_model()
        horizon = 5

        inputs = _predict_inputs(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_precip=[2.0] * 45,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, fi_boundary.ModelSuccess)


# --------------------------------------------------------------------------- #
# 4b. Plan 138 — past-TEMPERATURE channel: SHORT/STALE antecedent-temp window
#     -> ModelFailure (mirrors the precip case, DC-1/DC-2).
# --------------------------------------------------------------------------- #


class TestShortAntecedentTempWindowReturnsModelFailure:
    def test_predict_returns_model_failure_for_short_reanalysis_temp_window(
        self,
    ) -> None:
        model, artifact = _fit_seasonal_model()
        horizon = 5

        inputs = _predict_inputs_custom_reanalysis(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_ts=[
                _ISSUE - (45 - i) * _STEP for i in range(45)
            ],  # full, valid precip window
            reanalysis_vals=[2.0] * 45,
            lag_discharge=[10.0] * 7,
            reanalysis_temp_ts=[
                _ISSUE - (3 - i) * _STEP for i in range(3)
            ],  # only 3 rows, need 14
            reanalysis_temp_vals=[3.0, 4.0, 5.0],
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert "antecedent-temp" in result.message
        assert "got 3" in result.message
        assert "need 14" in result.message
        assert result.model_name == "seasonal_precip_runoff_regression"

    def test_predict_returns_model_failure_for_stale_reanalysis_temp_window(
        self,
    ) -> None:
        # 14 DISTINCT rows (would pass a bare row-count check) but entirely
        # outside the actual [issue-14d, issue) window.
        model, artifact = _fit_seasonal_model()
        horizon = 5
        stale_start = _ISSUE - timedelta(days=100)
        stale_temp_ts = [stale_start + i * _STEP for i in range(14)]

        inputs = _predict_inputs_custom_reanalysis(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_ts=[_ISSUE - (45 - i) * _STEP for i in range(45)],
            reanalysis_vals=[2.0] * 45,
            lag_discharge=[10.0] * 7,
            reanalysis_temp_ts=stale_temp_ts,
            reanalysis_temp_vals=[2.0] * 14,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert "antecedent-temp" in result.message
        assert "got 0" in result.message
        assert "need 14" in result.message

    def test_predict_succeeds_with_full_reanalysis_temp_window(self) -> None:
        model, artifact = _fit_seasonal_model()
        horizon = 5

        # Uses the default full temp window baked into
        # ``_predict_inputs`` / ``_predict_inputs_custom_reanalysis``.
        inputs = _predict_inputs(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_precip=[2.0] * 45,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, fi_boundary.ModelSuccess)


# --------------------------------------------------------------------------- #
# 4c. Plan 138 DC-3 — a coefficient/feature-count mismatch (a stale artifact,
#     trained BEFORE the antecedent-temp column existed) returns ModelFailure
#     instead of a raw NumPy shape crash or a silent mis-weighted prediction.
# --------------------------------------------------------------------------- #


class TestArtifactFeatureCountGuard:
    def test_predict_returns_model_failure_for_stale_artifact_missing_temp_column(
        self,
    ) -> None:
        model, artifact = _fit_seasonal_model()
        # Simulate a Plan-129-era artifact: one column short (no
        # antecedent-temp), same n_lags.
        stale_coefficients = artifact.coefficients[:-1]
        stale_artifact = type(artifact)(
            coefficients=stale_coefficients,
            intercept=artifact.intercept,
            n_lags=artifact.n_lags,
        )
        horizon = 5

        inputs = _predict_inputs(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_precip=[2.0] * 45,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            stale_artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert "feature-count mismatch" in result.message
        assert f"got {len(stale_coefficients)}" in result.message
        assert f"expected {len(artifact.coefficients)}" in result.message

    def test_predict_succeeds_with_matching_artifact_feature_count(self) -> None:
        # Soundness contrast: the freshly-trained artifact (matching feature
        # count) must still succeed — proves the guard discriminates on
        # count, not merely rejecting everything.
        model, artifact = _fit_seasonal_model()
        horizon = 5

        inputs = _predict_inputs(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_precip=[2.0] * 45,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=_ISSUE, rng=random.Random(0)
        )

        assert isinstance(result, fi_boundary.ModelSuccess)


# --------------------------------------------------------------------------- #
# 4d. Plan 138 DC-1 — feature-vector order: [precip, temp, antecedent_precip,
#     antecedent_temp, season_sin, season_cos, *lags]. Antecedent precip is a
#     SUM (flux); antecedent temp is a MEAN (state) — verified against an
#     independent oracle, not by re-deriving from the model's own helpers.
# --------------------------------------------------------------------------- #


def _manual_antecedent_sum(
    times: list[datetime], values: list[float], anchor: datetime, lookback_days: int
) -> float:
    window_start = anchor - timedelta(days=lookback_days)
    return sum(
        v for t, v in zip(times, values, strict=True) if window_start <= t < anchor
    )


def _manual_antecedent_mean(
    times: list[datetime], values: list[float], anchor: datetime, lookback_days: int
) -> float:
    window_start = anchor - timedelta(days=lookback_days)
    selected = [
        v for t, v in zip(times, values, strict=True) if window_start <= t < anchor
    ]
    return sum(selected) / len(selected)


class TestFeatureVectorOrderIncludesAntecedentTemp:
    def test_extra_train_features_column_order_and_values(self) -> None:
        model = SeasonalPrecipRunoffRegression()
        n = 120
        base = datetime(2024, 1, 1, tzinfo=UTC)
        ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp = _train_series(
            n, base
        )
        dynamic = _seasonal_dynamic_inputs(
            ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp
        )

        extra = model._extra_train_features(dynamic, ts)

        # 4 extra columns: antecedent_precip, antecedent_temp, season_sin, season_cos.
        assert extra.shape == (n, 4)

        anchor_idx = n - 1
        anchor = ts[anchor_idx]
        expected_precip_sum = _manual_antecedent_sum(
            ts, reanalysis_precip, anchor, model._precip_lookback_days
        )
        expected_temp_mean = _manual_antecedent_mean(
            ts, reanalysis_temp, anchor, model._temp_lookback_days
        )
        day_of_year = anchor.timetuple().tm_yday
        angle = 2.0 * math.pi * day_of_year / 365.25
        expected_season_sin = math.sin(angle)
        expected_season_cos = math.cos(angle)

        assert extra[anchor_idx, 0] == pytest.approx(expected_precip_sum)
        assert extra[anchor_idx, 1] == pytest.approx(expected_temp_mean)
        assert extra[anchor_idx, 2] == pytest.approx(expected_season_sin)
        assert extra[anchor_idx, 3] == pytest.approx(expected_season_cos)

    def test_extra_predict_features_column_order_and_values(self) -> None:
        model = SeasonalPrecipRunoffRegression()
        horizon = 5
        precip_ts = [_ISSUE - (45 - i) * _STEP for i in range(45)]
        precip_vals = [round(2.0 + i * 0.1, 4) for i in range(45)]
        temp_ts, temp_vals = _default_temp_window(_ISSUE)

        inputs = _predict_inputs_custom_reanalysis(
            issue=_ISSUE,
            horizon=horizon,
            reanalysis_ts=precip_ts,
            reanalysis_vals=precip_vals,
            lag_discharge=[10.0] * 7,
            reanalysis_temp_ts=temp_ts,
            reanalysis_temp_vals=temp_vals,
        )
        _station_key, dynamic = _dynamic_inputs(inputs)
        future_times = [_ISSUE + (k + 1) * _STEP for k in range(horizon)]

        extra = model._extra_predict_features(dynamic, future_times, _ISSUE)

        assert extra.shape == (horizon, 4)
        expected_precip_sum = _manual_antecedent_sum(
            precip_ts, precip_vals, _ISSUE, model._precip_lookback_days
        )
        expected_temp_mean = _manual_antecedent_mean(
            temp_ts, temp_vals, _ISSUE, model._temp_lookback_days
        )
        # Constant across the horizon (single antecedent value at issue-time).
        assert extra[:, 0] == pytest.approx([expected_precip_sum] * horizon)
        assert extra[:, 1] == pytest.approx([expected_temp_mean] * horizon)


# --------------------------------------------------------------------------- #
# 5. Training warmup — no sample with a partial 45-day antecedent window
#    enters the design matrix (Plan 129 post-implementation review)
# --------------------------------------------------------------------------- #


class TestTrainingWarmupExcludesPartialAntecedentWindows:
    def test_no_partial_precip_window_sample_enters_design_matrix(self) -> None:
        model = SeasonalPrecipRunoffRegression()
        n = 120
        base = datetime(2024, 1, 1, tzinfo=UTC)
        ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp = _train_series(
            n, base
        )
        dynamic = _seasonal_dynamic_inputs(
            ts, discharge, precip, temp, reanalysis_precip, reanalysis_temp
        )
        station = fi_boundary.StationInputs(
            dynamic={_STEP: fi_boundary.SpatialInputs(data={_spatial_rep(): dynamic})},
            static={},
        )
        inputs = fi_boundary.ModelInputs(stations={"station": station})

        with capture_logs() as logs:
            model.train(inputs, config={}, rng=random.Random(0))

        events = [e for e in logs if e.get("event") == "model.training_completed"]
        assert len(events) == 1
        # Plan 138: warmup is the max across ALL antecedent windows, not just
        # precip — the precip window (45) still dominates the temp window
        # (14) here, but the formula itself must reflect both.
        expected_warmup = max(
            model._n_lags, model._precip_lookback_days, model._temp_lookback_days
        )
        # If a partial-window row (index < 45) had entered the design matrix,
        # n_samples would be n - model._n_lags (113) instead of n - 45 (75).
        assert events[0]["warmup"] == expected_warmup
        assert events[0]["n_samples"] == n - expected_warmup

    def test_train_warmup_steps_reflects_temp_window_when_it_dominates(self) -> None:
        """Unit-level lock on ``_train_warmup_steps`` itself.

        In every scenario the row-count test above exercises,
        ``_temp_lookback_days`` (14) is smaller than ``_precip_lookback_days``
        (45), so ``max(n_lags, precip_window, temp_window)`` and
        ``max(n_lags, precip_window)`` produce the IDENTICAL value — that row
        -count test cannot distinguish the correct warmup formula from a
        regression that silently drops the temp term from the ``max(...)``.
        Override ``_temp_lookback_days`` on the instance to DOMINATE the
        precip window, independent of ``_train_series``'s fixed windows, so
        only a formula that genuinely includes it reports the right value.
        """
        model = SeasonalPrecipRunoffRegression()
        model._temp_lookback_days = model._precip_lookback_days + 100

        warmup = model._train_warmup_steps()

        assert warmup == model._temp_lookback_days
        assert warmup == max(
            model._n_lags, model._precip_lookback_days, model._temp_lookback_days
        )


# --------------------------------------------------------------------------- #
# 6. predict() rejects STALE / DUPLICATE-timestamp antecedent-precip windows
#    that a bare row-count check would let through (Plan 129
#    post-implementation review)
# --------------------------------------------------------------------------- #


class TestPredictRejectsDiscontinuousAntecedentWindow:
    def test_predict_returns_model_failure_for_stale_reanalysis_window(self) -> None:
        # 45 DISTINCT rows (passes the OLD bare `len(rows) >= 45` count check,
        # which never windowed by issue-time at all) but ALL from ~150 days
        # ago — entirely outside the actual [issue-45d, issue) window.
        model, artifact = _fit_seasonal_model()
        horizon = 5
        issue = _ISSUE
        lookback_days = model._precip_lookback_days
        stale_start = issue - timedelta(days=150)
        reanalysis_ts = [
            stale_start + i * timedelta(days=1) for i in range(lookback_days)
        ]
        reanalysis_vals = [2.0] * lookback_days

        inputs = _predict_inputs_custom_reanalysis(
            issue=issue,
            horizon=horizon,
            reanalysis_ts=reanalysis_ts,
            reanalysis_vals=reanalysis_vals,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=issue, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert "got 0" in result.message
        assert "need 45" in result.message
        assert result.model_name == "seasonal_precip_runoff_regression"

    def test_predict_returns_model_failure_for_rows_crammed_into_few_days(
        self,
    ) -> None:
        # 45 DISTINCT timestamps (passes the OLD bare `len(rows) >= 45` count
        # check) but crammed into just 5 distinct CALENDAR DAYS near
        # issue-time (9 sub-daily readings per day) instead of covering all
        # 45 antecedent days — an exact-timestamp dedup would still miss
        # this; bucketing by calendar day is what catches it.
        model, artifact = _fit_seasonal_model()
        horizon = 5
        issue = _ISSUE
        lookback_days = model._precip_lookback_days
        n_days = 5
        readings_per_day = lookback_days // n_days
        reanalysis_ts = sorted(
            issue - timedelta(days=1 + day) + timedelta(hours=hour)
            for day in range(n_days)
            for hour in range(readings_per_day)
        )
        reanalysis_vals = [2.0] * len(reanalysis_ts)

        inputs = _predict_inputs_custom_reanalysis(
            issue=issue,
            horizon=horizon,
            reanalysis_ts=reanalysis_ts,
            reanalysis_vals=reanalysis_vals,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=issue, rng=random.Random(0)
        )

        assert isinstance(result, ModelFailure)
        assert result.cause is FailureCause.INPUT_DATA
        assert f"got {n_days}" in result.message
        assert "need 45" in result.message

    def test_predict_succeeds_with_continuous_window_ending_at_issue(self) -> None:
        # Contrast case: proves the two failure tests above discriminate on
        # continuity, not merely on the total sample count (both use rows
        # >= lookback_days).
        model, artifact = _fit_seasonal_model()
        horizon = 5
        issue = _ISSUE
        lookback_days = model._precip_lookback_days
        window_start = issue - timedelta(days=lookback_days)
        reanalysis_ts = [
            window_start + i * timedelta(days=1) for i in range(lookback_days)
        ]
        reanalysis_vals = [2.0] * lookback_days

        inputs = _predict_inputs_custom_reanalysis(
            issue=issue,
            horizon=horizon,
            reanalysis_ts=reanalysis_ts,
            reanalysis_vals=reanalysis_vals,
            lag_discharge=[10.0] * 7,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=issue, rng=random.Random(0)
        )

        assert isinstance(result, fi_boundary.ModelSuccess)


# --------------------------------------------------------------------------- #
# 6b. BUG 1 — non-midnight forecast cycles. The deployment cron issues every
#     6h (0 */6 * * * -> 00/06/12/18Z), but daily reanalysis rows are
#     previous-midnight buckets. The antecedent-precip window must anchor on
#     the last complete reanalysis DAY, tolerating the 30/36/42h natural
#     staleness, so ALL FOUR cycles forecast rather than returning
#     ModelFailure on 3 of 4.
# --------------------------------------------------------------------------- #


class TestNonMidnightForecastCyclesProduceForecast:
    @pytest.mark.parametrize("issue_hour", [0, 6, 12, 18])
    def test_all_six_hourly_cycles_forecast_with_midnight_reanalysis(
        self, issue_hour: int
    ) -> None:
        # Real deployment shape: daily reanalysis rows are midnight-bucketed
        # (D-1 .. D-45 at 00:00), while the 6-hourly cron issues at
        # 00/06/12/18Z. A non-midnight issue therefore sits 30/36/42h after the
        # latest midnight bucket. Anchoring on the last complete reanalysis DAY
        # must accept every cycle. (Against the pre-fix exact-instant anchoring,
        # 06/12/18Z fail: the window drops a boundary day AND the freshness gap
        # exceeds one daily step.)
        model, artifact = _fit_seasonal_model()
        horizon = 5
        issue = ensure_utc(datetime(2026, 3, 1, issue_hour, tzinfo=UTC))
        precip_lookback_days = model._precip_lookback_days
        temp_lookback_days = model._temp_lookback_days
        issue_midnight = datetime(2026, 3, 1, tzinfo=UTC)
        # Midnight buckets for the antecedent calendar days D-45..D-1 (precip)
        # / D-14..D-1 (temp), ascending, as InputSeries requires. VARIED (not
        # uniform) per-day values: a Plan 138 aggregation-window regression
        # that silently drops the oldest validated day would leave a uniform
        # SUM/MEAN unchanged (dropping one 2.0 out of forty-five 2.0s does not
        # move the sum's shape-matching assertion below), so the fixture must
        # vary day-to-day for the value assertions to actually catch it.
        precip_ts = [
            issue_midnight - timedelta(days=k)
            for k in range(precip_lookback_days, 0, -1)
        ]
        precip_vals = [round(1.0 + 0.3 * i, 4) for i in range(precip_lookback_days)]
        temp_ts = [
            issue_midnight - timedelta(days=k) for k in range(temp_lookback_days, 0, -1)
        ]
        temp_vals = [round(-5.0 + 0.7 * i, 4) for i in range(temp_lookback_days)]

        inputs = _predict_inputs_custom_reanalysis(
            issue=issue,
            horizon=horizon,
            reanalysis_ts=precip_ts,
            reanalysis_vals=precip_vals,
            lag_discharge=[10.0] * 7,
            reanalysis_temp_ts=temp_ts,
            reanalysis_temp_vals=temp_vals,
        )

        result = model.predict(
            artifact, inputs=inputs, issue_datetime=issue, rng=random.Random(0)
        )

        assert isinstance(result, fi_boundary.ModelSuccess), (
            f"issue at {issue_hour:02d}Z returned {type(result).__name__}: "
            f"{getattr(result, 'message', '')}"
        )

        # Plan 138 regression lock: predict() must aggregate the SAME
        # calendar-day window [D-45, D) / [D-14, D) that
        # ``_validate_continuous_window`` just validated, for EVERY 6-hourly
        # issue on the same calendar day D — not a window shifted by the
        # wall-clock issue hour. Before the fix, a non-midnight issue anchored
        # the SUM/MEAN on ``[issue - lookback, issue)`` (including the hour),
        # which drops the oldest validated day (D-45 for precip, D-14 for
        # temp) — biasing both antecedent features on 3 of these 4 cycles.
        _station_key, dynamic = _dynamic_inputs(inputs)
        future_times = [issue + (k + 1) * _STEP for k in range(horizon)]
        extra = model._extra_predict_features(dynamic, future_times, issue)
        expected_precip_sum = _manual_antecedent_sum(
            precip_ts, precip_vals, issue_midnight, precip_lookback_days
        )
        expected_temp_mean = _manual_antecedent_mean(
            temp_ts, temp_vals, issue_midnight, temp_lookback_days
        )
        assert extra[0, 0] == pytest.approx(expected_precip_sum), (
            f"issue at {issue_hour:02d}Z: antecedent-precip sum does not "
            "match the midnight-anchored 45-day window"
        )
        assert extra[0, 1] == pytest.approx(expected_temp_mean), (
            f"issue at {issue_hour:02d}Z: antecedent-temp mean does not "
            "match the midnight-anchored 14-day window"
        )


# --------------------------------------------------------------------------- #
# 7. The FI adapter's max_nan gate independently covers this model's future
#    NWP precip channel despite the colliding bare name with the past
#    reanalysis channel (Plan 129 post-implementation review)
# --------------------------------------------------------------------------- #


def _time_frame(data: Mapping[str, Sequence[object]]) -> pl.DataFrame:
    return pl.DataFrame(dict(data)).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


class TestAdapterNanGateCoversCollidingFutureNwpPrecip:
    def test_adapter_predict_raises_for_nan_in_future_precip_with_clean_past(
        self,
    ) -> None:
        adapter = _adapter(SeasonalPrecipRunoffRegression())
        issue = _ISSUE
        lag_n = 7
        lag_ts = [issue - (lag_n - i) * _STEP for i in range(lag_n)]
        precip_n = 45
        precip_ts = [issue - (precip_n - i) * _STEP for i in range(precip_n)]
        horizon = 5
        future_ts = [issue + (k + 1) * _STEP for k in range(horizon)]

        data = StationInputData(
            past_targets=_time_frame(
                {"timestamp": lag_ts, "discharge": [10.0] * lag_n}
            ),
            past_dynamic=_time_frame(
                # Clean past antecedent precip AND antecedent temperature
                # (Plan 138 — the new past-known reanalysis/temperature
                # channel must also be present, or the adapter's
                # ``_frame_with_column`` lookup itself fails before the NaN
                # gate this test targets ever runs).
                {
                    "timestamp": precip_ts,
                    "precipitation": [2.0] * precip_n,
                    "temperature": [10.0] * precip_n,
                }
            ),
            future_dynamic=_time_frame(
                {
                    "timestamp": future_ts,
                    # Dirty future NWP precip — one NaN.
                    "precipitation": [1.0, float("nan"), 3.0, 4.0, 5.0],
                    "temperature": [10.0] * horizon,
                }
            ),
            static=None,
        )
        inputs = StationModelInputs(
            station_id=StationId(uuid4()),
            data=data,
            issue_time=issue,
            forecast_horizon_steps=horizon,
            time_step=_STEP,
        )

        with pytest.raises(ModelOutputError, match="future_known.precipitation"):
            adapter.predict(object(), inputs, random.Random(0))
