from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.services.hindcast import run_group_hindcast, run_station_hindcast
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.station import StationGroup, StationWeatherSource

if TYPE_CHECKING:
    from sapphire_flow.types.model import ModelArtifact, StationModelInputs
from tests.conftest import (
    make_observation,
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHindcastStore,
    FakeObservationStore,
    FakeStationStore,
)

_STEP = timedelta(hours=24)
_PERIOD_START = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))
_PERIOD_END = ensure_utc(datetime(2022, 1, 6, tzinfo=UTC))  # 5 issue times


def _utc(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _fixed_clock() -> UtcDatetime:
    return _utc(2022, 6, 1)


def _make_weather_source(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def _seed_forcing(
    source: FakeWeatherReanalysisSource,
    station_id: StationId,
    start: UtcDatetime,
    n_days: int,
) -> None:
    records = []
    for i in range(n_days * 24):
        ts = ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 3600, tz=UTC))
        for param in ("precipitation", "temperature"):
            records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter=param,
                    valid_time=ts,
                    value=float(i % 20),
                )
            )
    source.set_records(records)


def _seed_observations(
    obs_store: FakeObservationStore,
    station_id: StationId,
    start: UtcDatetime,
    n_days: int,
) -> None:
    obs = make_observations(
        n=n_days * 24,
        station_id=station_id,
        start=start,
        interval=timedelta(hours=1),
    )
    obs_store.store_observations(obs)


class TestHorizonResolution:
    def test_horizon_resolved_from_model_when_omitted(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeStationForecastModel()
        # forecast_horizon_steps omitted — should resolve from model declaration (= 5)

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert all(r.success for r in results)
        for h in hindcast_store._hindcasts.values():
            assert h.ensemble.forecast_horizon_steps == 5

    def test_explicit_horizon_overrides_model_declaration(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeStationForecastModel()
        # Model declares forecast_horizon_steps=5; caller explicitly requests 3.
        explicit_horizon = 3

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=explicit_horizon,
        )

        assert all(r.success for r in results)
        for h in hindcast_store._hindcasts.values():
            assert h.ensemble.forecast_horizon_steps == explicit_horizon


class TestBasicHindcast:
    def test_five_issue_times_all_succeed(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        # seed data well before period start so lookback window is satisfied
        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeStationForecastModel()

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert len(results) == 5
        assert all(r.success for r in results)
        assert all(r.error is None for r in results)
        for h in hindcast_store._hindcasts.values():
            assert isinstance(h.ensemble, ForecastEnsemble)


class TestNoFutureLeakage:
    """Observations must never contain data at or beyond issue_time (target leakage).

    Forcing legitimately extends beyond issue_time — reanalysis serves as teacher
    forcing in hindcast (v0-scope §A13). Forcing must stay within the full window:
    [lookback_start, horizon_end).
    """

    def test_inputs_contain_only_past_data(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        forecast_horizon_steps = 5
        lookback_steps = 720

        class RecordingModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def __init__(self) -> None:
                self.calls: list[tuple[UtcDatetime, StationModelInputs]] = []

            def predict(
                self,
                artifact: ModelArtifact,
                inputs: StationModelInputs,
                rng: random.Random,
                prior_state: bytes | None = None,
            ) -> tuple:
                self.calls.append((inputs.issue_time, inputs))
                return FakeStationForecastModel().predict(
                    artifact, inputs, rng, prior_state
                )

            def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
                return raw

        recording = RecordingModel()

        run_station_hindcast(
            model=recording,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=forecast_horizon_steps,
            lookback_steps=lookback_steps,
        )

        assert len(recording.calls) == 5, "expected predict called once per issue_time"

        for issue_time, inputs in recording.calls:
            lookback_start = ensure_utc(issue_time - lookback_steps * _STEP)
            horizon_end = ensure_utc(issue_time + forecast_horizon_steps * _STEP)

            # Observations: must not contain data at or beyond issue_time
            obs_timestamps = inputs.data.past_targets["timestamp"].to_list()
            for ts in obs_timestamps:
                assert ts < issue_time, (
                    f"observation timestamp {ts} >= issue_time {issue_time}: "
                    "future leakage detected"
                )

            # Forcing: past_dynamic covers [lookback_start, issue_time],
            # future_dynamic covers (issue_time, horizon_end] — teacher forcing
            # (+1 step fetched to ensure enough future rows after split)
            import polars as pl

            forcing_df = pl.concat(
                [inputs.data.past_dynamic, inputs.data.future_dynamic]
            ).sort("timestamp")
            forcing_timestamps = forcing_df["timestamp"].to_list()
            extended_end = ensure_utc(horizon_end + _STEP)
            for ts in forcing_timestamps:
                assert ts >= lookback_start, (
                    f"forcing timestamp {ts} < lookback_start {lookback_start}"
                )
                assert ts < extended_end, (
                    f"forcing timestamp {ts} >= extended_end {extended_end}"
                )


class TestStepFailureContinues:
    def test_model_exception_on_one_step_others_succeed(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        fail_at = _utc(2022, 1, 3)

        class BombModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(
                self,
                artifact: ModelArtifact,
                inputs: StationModelInputs,
                rng: random.Random,
                prior_state: bytes | None = None,
            ) -> tuple:
                if inputs.issue_time == fail_at:
                    raise RuntimeError("simulated model failure")
                return FakeStationForecastModel().predict(
                    artifact, inputs, rng, prior_state
                )

            def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
                return raw

        results = run_station_hindcast(
            model=BombModel(),
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert len(results) == 5
        failed = [r for r in results if not r.success]
        succeeded = [r for r in results if r.success]
        assert len(failed) == 1
        assert len(succeeded) == 4
        assert failed[0].issue_time == fail_at
        assert "simulated model failure" in (failed[0].error or "")


class TestInsufficientDataSkips:
    def test_no_observations_for_window_skips_step(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        # Seed forcing across entire period but NO observations
        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_forcing(forcing_source, sid, data_start, n_days=400)
        # obs_store is intentionally empty

        model = FakeStationForecastModel()

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert len(results) == 5
        assert all(not r.success for r in results)
        assert all("insufficient data" in (r.error or "") for r in results)


class TestHindcastStored:
    def test_hindcast_store_receives_all_successful_hindcasts(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeStationForecastModel()

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert all(r.success for r in results)

        stored = hindcast_store.fetch_hindcasts(
            station_id=sid,
            model_id=model_id,
            start=_PERIOD_START,
            end=ensure_utc(datetime(2022, 1, 10, tzinfo=UTC)),
            hindcast_run_id=run_id,
        )

        assert len(stored) == 5
        for h in stored:
            assert h.station_id == sid
            assert h.model_id == model_id
            assert h.model_artifact_id == artifact_id
            assert h.hindcast_run_id == run_id
            assert h.forcing_type.value == "reanalysis"
            assert isinstance(h.ensemble, ForecastEnsemble)


class TestMultiParameterStation:
    def test_two_parameters_stored_per_step(self) -> None:
        from tests.fakes.fake_models import FakeMultiTargetStationForecastModel

        rng = random.Random(42)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeMultiTargetStationForecastModel(
            parameters=("discharge", "water_level"),
        )

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert all(r.success for r in results)
        n_steps = len(results)
        all_hindcasts = list(hindcast_store._hindcasts.values())
        assert len(all_hindcasts) == 2 * n_steps
        for h in all_hindcasts:
            assert isinstance(h.ensemble, ForecastEnsemble)
        discharge_hindcasts = hindcast_store.fetch_hindcasts(
            station_id=sid,
            model_id=model_id,
            start=_PERIOD_START,
            end=ensure_utc(datetime(2022, 1, 10, tzinfo=UTC)),
            parameter="discharge",
        )
        water_level_hindcasts = hindcast_store.fetch_hindcasts(
            station_id=sid,
            model_id=model_id,
            start=_PERIOD_START,
            end=ensure_utc(datetime(2022, 1, 10, tzinfo=UTC)),
            parameter="water_level",
        )
        assert len(discharge_hindcasts) == n_steps
        assert len(water_level_hindcasts) == n_steps


class TestSingleParameterBackwardCompat:
    def test_single_param_model_stores_one_record_per_step(self) -> None:
        rng = random.Random(42)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeStationForecastModel()

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert all(r.success for r in results)
        n_steps = len(results)
        all_hindcasts = list(hindcast_store._hindcasts.values())
        assert len(all_hindcasts) == n_steps
        for h in all_hindcasts:
            assert isinstance(h.ensemble, ForecastEnsemble)


class TestMultiParameterGroup:
    def test_two_params_stored_per_station_per_step(self) -> None:
        from tests.fakes.fake_models import FakeMultiTargetGroupForecastModel

        rng = random.Random(99)
        station_a = make_station_config(
            station_id=StationId(uuid4()),
            code="A-001",
            name="Station A",
        )
        station_b = make_station_config(
            station_id=StationId(uuid4()),
            code="B-002",
            name="Station B",
        )
        sid_a = station_a.id
        sid_b = station_b.id
        model_id = ModelId("group_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        for st in (station_a, station_b):
            station_store.store_station(st)
            station_store.store_weather_source(_make_weather_source(st.id))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        all_forcing: list = []
        for i, sid in enumerate((sid_a, sid_b)):
            obs = make_observations(
                n=400 * 24,
                station_id=sid,
                start=data_start,
                interval=timedelta(hours=1),
                rng=random.Random(i),  # unique rng avoids ObservationId collisions
            )
            obs_store.store_observations(obs)
            _seed_forcing(forcing_source, sid, data_start, n_days=400)
            all_forcing.extend(forcing_source.records())
        # _seed_forcing overwrites _records each call; restore combined records
        forcing_source.set_records(all_forcing)

        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="test-group",
            station_ids=frozenset({sid_a, sid_b}),
            created_at=_fixed_clock(),
        )

        model = FakeMultiTargetGroupForecastModel(
            parameters=("discharge", "water_level"),
        )

        all_results = run_group_hindcast(
            model=model,
            artifact=b"artifact",
            group=group,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        n_steps = 5  # 5 issue times
        for sid in (sid_a, sid_b):
            assert all(r.success for r in all_results[sid])

        all_hindcasts = list(hindcast_store._hindcasts.values())
        # 2 params * 2 stations * 5 steps = 20
        assert len(all_hindcasts) == 2 * 2 * n_steps
        for h in all_hindcasts:
            assert isinstance(h.ensemble, ForecastEnsemble)


class TestEmptyEnsembleDict:
    def test_empty_ensemble_dict_stores_nothing(self) -> None:
        class EmptyModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(
                self,
                artifact: object,
                inputs: object,
                rng: random.Random,
                prior_state: bytes | None = None,
            ) -> tuple:
                return ({}, b"state")

            def train(self, data: object, params: object, rng: random.Random) -> bytes:
                return b"art"

            def serialize_artifact(self, a: object) -> bytes:
                return a if isinstance(a, bytes) else b""

            def deserialize_artifact(self, b: bytes) -> object:
                return b

        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = EmptyModel()

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert all(r.success for r in results)
        assert len(hindcast_store._hindcasts) == 0


class TestPrefetchCallCount:
    def test_fetch_reanalysis_called_once_per_station(self) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = FakeStationForecastModel()

        run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert forcing_source.fetch_reanalysis_call_count == 1
        assert obs_store.fetch_observations_call_count == 1


class TestConnectionFatalAbort:
    def test_connection_fatal_aborts_station_hindcast(self) -> None:
        from sqlalchemy.exc import OperationalError

        from sapphire_flow.exceptions import StoreError

        class BombHindcastStore:
            def store_hindcast(self, hindcast: object) -> None:
                raise OperationalError(
                    "server closed the connection unexpectedly",
                    params=None,
                    orig=Exception("server closed the connection unexpectedly"),
                )

        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        with pytest.raises(StoreError, match="Connection-fatal"):
            run_station_hindcast(
                model=FakeStationForecastModel(),
                artifact=b"artifact",
                station_id=sid,
                model_id=model_id,
                artifact_id=artifact_id,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=BombHindcastStore(),  # type: ignore[arg-type]
                station_store=station_store,
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=rng,
                hindcast_run_id=run_id,
                forecast_horizon_steps=5,
            )

    def test_transient_error_does_not_abort(self) -> None:
        from sqlalchemy.exc import OperationalError

        class TransientBombStore:
            def __init__(self) -> None:
                self._call_count = 0
                self._stored: list[object] = []

            def store_hindcast(self, hindcast: object) -> None:
                self._call_count += 1
                if self._call_count == 1:
                    raise OperationalError(
                        "deadlock detected",
                        params=None,
                        orig=Exception("deadlock detected"),
                    )
                self._stored.append(hindcast)

        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()
        bomb_store = TransientBombStore()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        # Must NOT raise — transient error is logged and skipped
        results = run_station_hindcast(
            model=FakeStationForecastModel(),
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=bomb_store,  # type: ignore[arg-type]
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert len(results) == 5
        failed = [r for r in results if not r.success]
        succeeded = [r for r in results if r.success]
        assert len(failed) == 1
        assert len(succeeded) == 4

    def test_connection_fatal_aborts_group_hindcast(self) -> None:
        from sqlalchemy.exc import OperationalError

        from sapphire_flow.exceptions import StoreError
        from tests.fakes.fake_models import FakeGroupForecastModel

        class BombHindcastStore:
            def store_hindcast(self, hindcast: object) -> None:
                raise OperationalError(
                    "server closed the connection unexpectedly",
                    params=None,
                    orig=Exception("server closed the connection unexpectedly"),
                )

        rng = random.Random(42)
        station_a = make_station_config(
            station_id=StationId(uuid4()),
            code="A-001",
            name="Station A",
        )
        station_b = make_station_config(
            station_id=StationId(uuid4()),
            code="B-002",
            name="Station B",
        )
        sid_a = station_a.id
        sid_b = station_b.id
        model_id = ModelId("group_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        for st in (station_a, station_b):
            station_store.store_station(st)
            station_store.store_weather_source(_make_weather_source(st.id))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        all_forcing: list = []
        for i, sid in enumerate((sid_a, sid_b)):
            obs = make_observations(
                n=400 * 24,
                station_id=sid,
                start=data_start,
                interval=timedelta(hours=1),
                rng=random.Random(i),
            )
            obs_store.store_observations(obs)
            _seed_forcing(forcing_source, sid, data_start, n_days=400)
            all_forcing.extend(forcing_source.records())
        forcing_source.set_records(all_forcing)

        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="test-group",
            station_ids=frozenset({sid_a, sid_b}),
            created_at=_fixed_clock(),
        )

        with pytest.raises(StoreError, match="Connection-fatal"):
            run_group_hindcast(
                model=FakeGroupForecastModel(),
                artifact=b"artifact",
                group=group,
                model_id=model_id,
                artifact_id=artifact_id,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=BombHindcastStore(),  # type: ignore[arg-type]
                station_store=station_store,
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=rng,
                hindcast_run_id=run_id,
                forecast_horizon_steps=5,
            )


class _RecordingReanalysisSource(FakeWeatherReanalysisSource):
    """Reanalysis source that records the parameters each fetch requests."""

    def __init__(self) -> None:
        super().__init__()
        self.requested_parameters: list[list[str]] = []

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list:
        self.requested_parameters.append(list(parameters))
        return super().fetch_reanalysis(station_configs, start, end, parameters)


class _FutureForcingModel:
    """STATION model whose forcing is future-known only (past_dynamic empty).

    Mirrors the M2 NWP models: precip/temp are future_dynamic_features, so the
    hindcast reanalysis fetch must union past+future features (P1) or
    future_dynamic ends up empty.
    """

    from sapphire_flow.types.enums import ArtifactScope as _ArtifactScope
    from sapphire_flow.types.model import ModelDataRequirements as _Reqs

    artifact_scope = _ArtifactScope.STATION
    data_requirements = _Reqs(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1), timedelta(hours=24)}),
        lookback_steps=720,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )

    def __init__(self) -> None:
        self.calls: list[StationModelInputs] = []

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple:
        self.calls.append(inputs)
        return FakeStationForecastModel().predict(artifact, inputs, rng, prior_state)

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return b""

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


class TestFutureForcingFetched:
    """P1: hindcast must fetch future-known forcing from reanalysis.

    For a model whose precip/temp are ``future_dynamic_features`` (empty
    ``past_dynamic_features``), the reanalysis fetch must request those features
    and the assembled input must carry a NON-EMPTY ``future_dynamic``.
    """

    def test_reanalysis_fetches_future_features_and_populates_future_dynamic(
        self,
    ) -> None:
        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("future_forcing_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = _RecordingReanalysisSource()

        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)
        _seed_forcing(forcing_source, sid, data_start, n_days=400)

        model = _FutureForcingModel()

        results = run_station_hindcast(
            model=model,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert all(r.success for r in results)

        # Reanalysis was asked for the future-known forcing, NOT the target.
        assert forcing_source.requested_parameters == [["precipitation", "temperature"]]
        assert all(
            "discharge" not in params for params in forcing_source.requested_parameters
        )

        # Every assembled input carries a non-empty future_dynamic with precip/temp.
        assert model.calls, "predict was never called"
        for inputs in model.calls:
            future = inputs.data.future_dynamic
            assert not future.is_empty(), "future_dynamic is empty"
            assert {"precipitation", "temperature"} <= set(future.columns)
            assert (future["timestamp"] > inputs.issue_time).all()


class TestGroupHindcastUsesGroupModelInputs:
    def test_predict_batch_receives_group_model_inputs(self) -> None:
        from sapphire_flow.types.model import GroupModelInputs
        from tests.fakes.fake_models import FakeGroupForecastModel

        rng = random.Random(42)
        station_a = make_station_config(
            station_id=StationId(uuid4()),
            code="R-001",
            name="Recording A",
        )
        station_b = make_station_config(
            station_id=StationId(uuid4()),
            code="R-002",
            name="Recording B",
        )
        sid_a = station_a.id
        sid_b = station_b.id
        model_id = ModelId("recording_group_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()
        forcing_source = FakeWeatherReanalysisSource()

        for st in (station_a, station_b):
            station_store.store_station(st)
            station_store.store_weather_source(_make_weather_source(st.id))

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        all_forcing: list = []
        for i, sid in enumerate((sid_a, sid_b)):
            obs = make_observations(
                n=400 * 24,
                station_id=sid,
                start=data_start,
                interval=timedelta(hours=1),
                rng=random.Random(100 + i),
            )
            obs_store.store_observations(obs)
            _seed_forcing(forcing_source, sid, data_start, n_days=400)
            all_forcing.extend(forcing_source.records())
        forcing_source.set_records(all_forcing)

        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="recording-group",
            station_ids=frozenset({sid_a, sid_b}),
            created_at=_fixed_clock(),
        )

        class RecordingGroupModel(FakeGroupForecastModel):
            def __init__(self) -> None:
                super().__init__()
                self.last_inputs: GroupModelInputs | None = None

            def predict_batch(self, artifact, inputs, rng):  # type: ignore[override]
                self.last_inputs = inputs
                return super().predict_batch(artifact, inputs, rng)

        recording = RecordingGroupModel()

        run_group_hindcast(
            model=recording,
            artifact=b"artifact",
            group=group,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
            forecast_horizon_steps=5,
        )

        assert recording.last_inputs is not None
        assert isinstance(recording.last_inputs, GroupModelInputs)


class TestSnowReachesPastDynamicViaHybridSource:
    """Plan 146 D4/3a: the SAME stored ``recap_snow_reanalysis`` series
    reaches ``past_dynamic`` for the hindcast consumer through the real
    ``default_hybrid_forcing_source``."""

    def test_swe_column_present_with_stored_value(self) -> None:
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            default_hybrid_forcing_source,
        )
        from sapphire_flow.types.enums import ArtifactScope
        from sapphire_flow.types.model import ModelDataRequirements
        from tests.fakes.fake_stores import FakeHistoricalForcingStore

        rng = random.Random(0)
        station = make_station_config()
        sid = station.id
        model_id = ModelId("test_model")
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        obs_store = FakeObservationStore()
        hindcast_store = FakeHindcastStore()
        station_store = FakeStationStore()
        basin_store = FakeBasinStore()

        station_store.store_station(station)
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="era5_land",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )

        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        _seed_observations(obs_store, sid, data_start, n_days=400)

        forcing_store = FakeHistoricalForcingStore()
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=sid,
                    source="recap_snow_reanalysis",
                    parameter="swe",
                    valid_time=ensure_utc(
                        datetime.fromtimestamp(
                            data_start.timestamp() + i * 3600, tz=UTC
                        )
                    ),
                    value=float(i % 20),
                )
                for i in range(400 * 24)
            ]
        )

        class _SnowFedModel(FakeStationForecastModel):
            artifact_scope = ArtifactScope.STATION
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset({"swe"}),
                future_dynamic_features=frozenset(),
                static_features=frozenset(),
                supported_time_steps=frozenset({_STEP}),
                lookback_steps=720,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            )

        class RecordingModel:
            artifact_scope = _SnowFedModel.artifact_scope
            data_requirements = _SnowFedModel.data_requirements

            def __init__(self) -> None:
                self.calls: list[StationModelInputs] = []

            def predict(
                self,
                artifact: ModelArtifact,
                inputs: StationModelInputs,
                rng: random.Random,
                prior_state: bytes | None = None,
            ) -> tuple:
                self.calls.append(inputs)
                return FakeStationForecastModel().predict(
                    artifact, inputs, rng, prior_state
                )

            def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
                return raw

        recording = RecordingModel()

        run_station_hindcast(
            model=recording,
            artifact=b"artifact",
            station_id=sid,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=default_hybrid_forcing_source(forcing_store=forcing_store),
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert len(recording.calls) == 5
        for inputs in recording.calls:
            assert "swe" in inputs.data.past_dynamic.columns
            assert not inputs.data.past_dynamic.is_empty()


class TestHindcastResamplesPastTargetsToDeclaredTimeStep:
    """Plan 228 T1/P1: a model's ``lookback_steps`` count of ``past_targets``
    rows must span its declared ``time_step`` window, exactly as the
    operational path already delivers (``operational_inputs.py:551``).
    Before the fix, hindcast handed the model raw ~10-minute-cadence rows
    against a declared ``timedelta(days=1)``: 7 rows spanned ~70 minutes,
    not 7 days — a 144x shortfall, measured live on the mac-mini
    (2026-09-01).
    """

    def test_tail_lookback_spans_the_declared_window_not_raw_cadence(self) -> None:
        from sapphire_flow.services.hindcast import _assemble_hindcast_inputs

        station = make_station_config()
        sid = station.id
        time_step = timedelta(hours=24)
        lookback_steps = 7
        issue_time = ensure_utc(datetime(2022, 1, 15, tzinfo=UTC))

        # 12 days of real mac-mini cadence (~604s / 10min) observations,
        # ending just before issue_time — far more than 7 RAW rows, but
        # exactly enough for 7 daily means.
        data_start = ensure_utc(issue_time - timedelta(days=12))
        observations = make_observations(
            n=12 * 24 * 6,
            station_id=sid,
            parameter="discharge",
            start=data_start,
            interval=timedelta(minutes=10),
        )

        inputs = _assemble_hindcast_inputs(
            station_id=sid,
            issue_time=issue_time,
            lookback_steps=lookback_steps,
            time_step=time_step,
            forecast_horizon_steps=5,
            required_features=[],
            all_forcing=[],
            all_observations=observations,
            weather_sources=[],
            static_attributes=None,
        )

        assert inputs is not None
        past_targets = inputs.data.past_targets

        # This mirrors exactly what a daily model reads
        # (`LinearRegressionDaily._extract_discharge`: `sorted_df.tail(7)`,
        # `NwpRegression._initial_lags`: `values[-self._n_lags:]`).
        tail = past_targets.sort("timestamp").tail(lookback_steps)
        assert tail.height == lookback_steps

        span = tail["timestamp"].max() - tail["timestamp"].min()
        declared_window = lookback_steps * time_step

        # Buggy code reports ~70 minutes here (7 raw 10-minute rows); fixed
        # code must span within one time_step of the full declared window.
        assert span >= declared_window - time_step, (
            f"tail({lookback_steps}) of past_targets spans only {span} "
            f"against a declared {declared_window} lookback window"
        )


class TestHindcastValidatesTheChronologicalTailNotThePhysicalOne:
    """Plan 228 review fixer round (major): ``.tail(declared_lookback_steps)``
    trims whatever rows happen to be LAST in ``obs_df``'s physical order — a
    real risk given the observation store issues no ``ORDER BY`` and (before
    this fixer round) ``resample_to_time_step``'s fast path could return
    rows unsorted. Feeding a scrambled (but otherwise gap-free and complete)
    10-day observation list must not corrupt the CHRONOLOGICAL last 7 days'
    validation."""

    def test_scrambled_input_order_does_not_suppress_a_clean_chronological_tail(
        self,
    ) -> None:
        from sapphire_flow.services.hindcast import _assemble_hindcast_inputs

        station = make_station_config()
        sid = station.id
        time_step = timedelta(hours=24)
        declared_lookback_steps = 7
        fetch_lookback_steps = 10
        issue_time = ensure_utc(datetime(2022, 3, 1, tzinfo=UTC))

        # 10 complete, consecutive calendar days ending the day before
        # issue_time — chronologically gap-free, including its last 7 days.
        data_start = ensure_utc(issue_time - timedelta(days=10))
        by_day = {
            day: make_observation(
                station_id=sid,
                parameter="discharge",
                timestamp=ensure_utc(data_start + timedelta(days=day - 1)),
                value=float(day),
                rng=random.Random(day),
            )
            for day in range(1, 11)
        }
        # Scrambled PHYSICAL order (odds then evens): the LAST 7 elements in
        # this list order are days {7,9,2,4,6,8,10} — a non-contiguous
        # subset with real 2-day gaps between several of them, even though
        # every day 1-10 is genuinely present. A validator that trusts
        # PHYSICAL order (instead of sorting first) sees those manufactured
        # gaps and wrongly raises; the true chronological tail (days 4-10)
        # has none.
        scrambled_order = [1, 3, 5, 7, 9, 2, 4, 6, 8, 10]
        observations = [by_day[day] for day in scrambled_order]

        inputs = _assemble_hindcast_inputs(
            station_id=sid,
            issue_time=issue_time,
            lookback_steps=fetch_lookback_steps,
            time_step=time_step,
            forecast_horizon_steps=5,
            required_features=[],
            all_forcing=[],
            all_observations=observations,
            weather_sources=[],
            static_attributes=None,
            declared_lookback_steps=declared_lookback_steps,
        )

        assert inputs is not None, (
            "a scrambled but chronologically gap-free observation list "
            "wrongly suppressed the hindcast step — validation examined an "
            "arbitrary PHYSICAL tail instead of the true chronological one"
        )
        # The chronological tail must be exactly days 4-10 (values 4.0-10.0),
        # in order — not whatever the scrambled physical order left last.
        tail = inputs.data.past_targets.tail(declared_lookback_steps)
        assert tail["discharge"].to_list() == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


class TestHindcastCadenceMismatchSkipsStepGracefully:
    """Plan 228 review fixer round (major): a real BAFU/SwissMetNet lookback
    window can legitimately contain an isolated missing bucket (a
    sensor/comms gap). ``validate_time_step_cadence`` must still catch it
    (a positional misread is worse than no score), but hindcast must treat
    it exactly like its OTHER insufficient-data conditions
    (``no_observations``, ``no_forcing``) — return ``None`` so the step is
    recorded as "insufficient data", never raise an undifferentiated
    exception out of the assembler."""

    def test_isolated_missing_daily_bucket_returns_none_not_raises(self) -> None:
        from sapphire_flow.services.hindcast import _assemble_hindcast_inputs

        station = make_station_config()
        sid = station.id
        time_step = timedelta(hours=24)
        issue_time = ensure_utc(datetime(2022, 1, 10, tzinfo=UTC))

        # Days 1,2,3,5,6 before issue_time — an isolated missing bucket on
        # day 4 (gaps 1d,1d,2d,1d; median is still 1d).
        data_start = ensure_utc(issue_time - timedelta(days=7))
        days = [1, 2, 3, 5, 6]
        observations = [
            make_observation(
                station_id=sid,
                parameter="discharge",
                timestamp=ensure_utc(data_start + timedelta(days=d - 1)),
                rng=random.Random(d),
            )
            for d in days
        ]

        inputs = _assemble_hindcast_inputs(
            station_id=sid,
            issue_time=issue_time,
            lookback_steps=7,
            time_step=time_step,
            forecast_horizon_steps=5,
            required_features=[],
            all_forcing=[],
            all_observations=observations,
            weather_sources=[],
            static_attributes=None,
        )

        assert inputs is None


class TestHindcastPastTargetsAreCompleteUtcCalendarBuckets:
    """Plan 228 D4: for a non-midnight ``issue_time`` (a 06/12/18Z cycle),
    only COMPLETE UTC-calendar-day buckets may reach the model — never a
    partial trailing day presented as if it were a full one. A previous
    fixer round fixed this by phase-aligning buckets to ``issue_time``
    instead (an ``anchor`` parameter); D4 retracted that (it made hindcast
    consume rolling windows while training consumes UTC calendar days) in
    favour of aligning the FETCH BOUNDS to the calendar grid instead.
    """

    def test_partial_trailing_day_is_excluded_not_averaged_in(self) -> None:
        from sapphire_flow.services.hindcast import _assemble_hindcast_inputs

        station = make_station_config()
        sid = station.id
        time_step = timedelta(hours=24)
        lookback_steps = 7
        # 06:00 UTC — never a midnight boundary. Day D's hours [00:00, 06:00)
        # are BEFORE issue_time (real past data) but only 1/4 of a full day.
        issue_time = ensure_utc(datetime(2022, 1, 15, 6, tzinfo=UTC))
        day_d_midnight = ensure_utc(datetime(2022, 1, 15, tzinfo=UTC))

        # 12 complete prior days at a real 10-minute cadence, all far from
        # the sentinel value used for day D's partial hours below.
        data_start = ensure_utc(issue_time - timedelta(days=12))
        observations = make_observations(
            n=12 * 24 * 6,
            station_id=sid,
            parameter="discharge",
            start=data_start,
            interval=timedelta(minutes=10),
        )
        # Day D's own partial window [00:00, 06:00) — a distinct sentinel
        # value that must NEVER surface in past_targets. Buggy (pre-D4) code
        # fetched observations up to `issue_time` unaligned, forming a
        # spurious "day D" bucket from only these 6 hours.
        partial_day_observations = [
            make_observation(
                station_id=sid,
                parameter="discharge",
                timestamp=ensure_utc(day_d_midnight + timedelta(hours=h)),
                value=999.0,
                rng=random.Random(1000 + h),
            )
            for h in range(6)
        ]

        inputs = _assemble_hindcast_inputs(
            station_id=sid,
            issue_time=issue_time,
            lookback_steps=lookback_steps,
            time_step=time_step,
            forecast_horizon_steps=5,
            required_features=[],
            all_forcing=[],
            all_observations=observations + partial_day_observations,
            weather_sources=[],
            static_attributes=None,
        )

        assert inputs is not None
        past_targets = inputs.data.past_targets.sort("timestamp")

        # No bucket may be built from the partial day-D window at all.
        assert 999.0 not in past_targets["discharge"].to_list(), (
            "past_targets contains a value built from the partial "
            "[00:00, 06:00) day-D window — a partial bucket leaked through "
            "as if it were a complete day"
        )
        # Every bucket is UTC-midnight-aligned and strictly before issue_time.
        for ts in past_targets["timestamp"]:
            assert ts.hour == 0 and ts.minute == 0 and ts.second == 0
            assert ts < issue_time
        # The most recent bucket is the day BEFORE day D — a full complete
        # day, not day D's partial one.
        assert past_targets["timestamp"].max() == ensure_utc(
            day_d_midnight - timedelta(days=1)
        )


class TestHindcastValidatesOnlyTheDeclaredLookbackWindow:
    """Plan 228 ALSO FIX #1: hindcast runners default to a much larger fetch
    ``lookback_steps`` (720) than any model actually consumes via its own
    ``.tail(n)``. Validating cadence over the WHOLE fetched frame let a
    single stale bucket anywhere in that huge window suppress every
    hindcast step — even though the model never reads that far back.
    Validation must be scoped to ``declared_lookback_steps`` (the model's
    own ``data_requirements.lookback_steps``), trimmed from the tail."""

    def test_a_stale_bucket_outside_the_declared_lookback_does_not_block(
        self,
    ) -> None:
        from sapphire_flow.services.hindcast import _assemble_hindcast_inputs

        station = make_station_config()
        sid = station.id
        time_step = timedelta(hours=24)
        declared_lookback_steps = 7  # what the model actually reads
        fetch_lookback_steps = 30  # the runner's larger fetch window
        issue_time = ensure_utc(datetime(2022, 2, 1, tzinfo=UTC))

        # 30 complete daily buckets, EXCEPT day -20 is missing entirely — a
        # gap far outside the model's own 7-day tail, inside the runner's
        # 30-day fetch window.
        data_start = ensure_utc(issue_time - timedelta(days=30))
        skip_day = 10  # the 10th day from data_start — well before the tail
        observations = [
            make_observation(
                station_id=sid,
                parameter="discharge",
                timestamp=ensure_utc(data_start + timedelta(days=d)),
                rng=random.Random(d),
            )
            for d in range(30)
            if d != skip_day
        ]

        inputs = _assemble_hindcast_inputs(
            station_id=sid,
            issue_time=issue_time,
            lookback_steps=fetch_lookback_steps,
            time_step=time_step,
            forecast_horizon_steps=5,
            required_features=[],
            all_forcing=[],
            all_observations=observations,
            weather_sources=[],
            static_attributes=None,
            declared_lookback_steps=declared_lookback_steps,
        )

        assert inputs is not None, (
            "a gap outside the model's own declared lookback window "
            "wrongly suppressed the hindcast step"
        )
        tail = inputs.data.past_targets.sort("timestamp").tail(declared_lookback_steps)
        assert tail.height == declared_lookback_steps
