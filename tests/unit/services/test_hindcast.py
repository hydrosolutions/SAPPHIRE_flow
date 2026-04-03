from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.services.hindcast import run_group_hindcast, run_station_hindcast
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.station import StationGroup, StationWeatherSource

if TYPE_CHECKING:
    from sapphire_flow.types.model import ModelArtifact, ModelInputs
from tests.conftest import (
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
    source._records = records


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
                self.calls: list[tuple[UtcDatetime, ModelInputs]] = []

            def predict(
                self,
                artifact: ModelArtifact,
                inputs: ModelInputs,
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
            obs_timestamps = inputs.observations["timestamp"].to_list()
            for ts in obs_timestamps:
                assert ts < issue_time, (
                    f"observation timestamp {ts} >= issue_time {issue_time}: "
                    "future leakage detected"
                )

            # Forcing: covers [lookback_start, horizon_end) — teacher forcing
            forcing_timestamps = inputs.forcing["timestamp"].to_list()
            for ts in forcing_timestamps:
                assert ts >= lookback_start, (
                    f"forcing timestamp {ts} < lookback_start {lookback_start}"
                )
                assert ts < horizon_end, (
                    f"forcing timestamp {ts} >= horizon_end {horizon_end}"
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
                inputs: ModelInputs,
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
            all_forcing.extend(forcing_source._records)
        # _seed_forcing overwrites _records each call; restore combined records
        forcing_source._records = all_forcing

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
            all_forcing.extend(forcing_source._records)
        forcing_source._records = all_forcing

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
