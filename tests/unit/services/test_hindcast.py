from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.services.hindcast import run_station_hindcast
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.station import StationWeatherSource

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


class TestNoFutureLeakage:
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

        class RecordingModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            required_features = FakeStationForecastModel.required_features
            required_static_attributes = (
                FakeStationForecastModel.required_static_attributes
            )
            spatial_input_type = FakeStationForecastModel.spatial_input_type
            supported_time_steps = FakeStationForecastModel.supported_time_steps

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
            forecast_horizon_steps=5,
        )

        assert len(recording.calls) == 5, "expected predict called once per issue_time"

        for issue_time, inputs in recording.calls:
            obs_timestamps = inputs.observations["timestamp"].to_list()
            for ts in obs_timestamps:
                assert ts < issue_time, (
                    f"observation timestamp {ts} >= issue_time {issue_time}: "
                    "future leakage detected"
                )

            forcing_timestamps = inputs.forcing["timestamp"].to_list()
            for ts in forcing_timestamps:
                assert ts < issue_time, (
                    f"forcing timestamp {ts} >= issue_time {issue_time}: "
                    "future leakage detected"
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
            required_features = FakeStationForecastModel.required_features
            required_static_attributes = (
                FakeStationForecastModel.required_static_attributes
            )
            spatial_input_type = FakeStationForecastModel.spatial_input_type
            supported_time_steps = FakeStationForecastModel.supported_time_steps

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
