from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WarmUpSource,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import (
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

_STEP = timedelta(hours=24)
_ISSUE = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
_CYCLE = ensure_utc(datetime(2026, 1, 9, 18, tzinfo=UTC))  # 6h before issue
_NOW = ensure_utc(datetime(2026, 1, 10, 1, tzinfo=UTC))  # 1h after issue
_NWP_SOURCE = "icon_ch2_eps"
_MODEL_ID = ModelId("fake_station_model")
_LOOKBACK = 5  # days worth for test (model has 720 steps default but we patch)


def _utc(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _clock() -> UtcDatetime:
    return _NOW


def _make_nwp_records(
    station_id: StationId,
    cycle_time: UtcDatetime,
    start: UtcDatetime,
    n_steps: int,
    parameters: list[str] | None = None,
    n_members: int = 3,
) -> list[WeatherForecastRecord]:
    params = parameters or ["precipitation", "temperature"]
    records = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(start.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for param in params:
            for m in range(n_members):
                records.append(
                    WeatherForecastRecord(
                        id=uuid4(),
                        station_id=station_id,
                        nwp_source=_NWP_SOURCE,
                        cycle_time=cycle_time,
                        valid_time=vt,
                        parameter=param,
                        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                        band_id=None,
                        member_id=m,
                        value=float(step + m),
                        created_at=_NOW,
                    )
                )
    return records


def _seed_forcing(
    source: FakeWeatherReanalysisSource,
    station_id: StationId,
    start: UtcDatetime,
    n_days: int,
    parameters: list[str] | None = None,
) -> None:
    params = parameters or ["precipitation", "temperature"]
    records = []
    for i in range(n_days * 24):
        ts = ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 3600, tz=UTC))
        for param in params:
            records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter=param,
                    valid_time=ts,
                    value=float(i % 10),
                )
            )
    source._records = records


class _SmallModelRequirements:
    """Minimal wrapper to override lookback_steps for faster tests."""

    from sapphire_flow.types.enums import ArtifactScope
    from sapphire_flow.types.model import ModelDataRequirements

    artifact_scope = ArtifactScope.STATION

    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=10,
        spatial_input_type=SpatialRepresentation.POINT,
    )

    def train(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return b""

    def predict(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return ({}, None)

    def serialize_artifact(self, artifact):  # type: ignore[no-untyped-def]
        return b""

    def deserialize_artifact(self, raw):  # type: ignore[no-untyped-def]
        return raw


def _make_model() -> _SmallModelRequirements:
    return _SmallModelRequirements()


def _make_stores_and_sources(
    station_id: StationId,
    with_nwp: bool = True,
    with_obs: bool = True,
    with_state: bool = True,
    state_age_hours: float = 1.0,
    n_obs: int = 20,
    n_nwp_steps: int = 120,
) -> tuple:
    station_store = FakeStationStore()
    basin_store = FakeBasinStore()
    obs_store = FakeObservationStore()
    nwp_store = FakeWeatherForecastStore()
    state_store = FakeModelStateStore()
    reanalysis = FakeWeatherReanalysisSource()

    station_cfg = make_station_config(station_id=station_id)
    station_store._stations[station_id] = station_cfg
    from sapphire_flow.types.station import StationWeatherSource

    station_store._weather_sources.append(
        StationWeatherSource(
            station_id=station_id,
            nwp_source=_NWP_SOURCE,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
        )
    )

    if with_obs:
        obs_start = _utc(2026, 1, 9, 2)

        obs = make_observations(
            n=n_obs,
            station_id=station_id,
            parameter="discharge",
            start=obs_start,
            interval=timedelta(hours=1),
        )
        obs_store.store_observations(obs)
        # Seed reanalysis for past_dynamic
        _seed_forcing(reanalysis, station_id, obs_start, n_days=2)

    if with_nwp:
        nwp_records = _make_nwp_records(
            station_id=station_id,
            cycle_time=_CYCLE,
            start=_ISSUE,
            n_steps=n_nwp_steps,
        )
        nwp_store.store_weather_forecasts(nwp_records)

    if with_state:
        state_time = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() - state_age_hours * 3600, tz=UTC)
        )
        state_store.store_state(station_id, _MODEL_ID, state_time, b"state_bytes")

    return station_store, basin_store, obs_store, nwp_store, state_store, reanalysis


class TestAssembleStationOperationalInputs:
    def test_happy_path_returns_inputs_and_fresh_metadata(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, state_age_hours=1.0)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        inputs, metadata = result
        assert inputs.station_id == sid
        assert inputs.issue_time == _ISSUE
        assert inputs.forecast_horizon_steps == 120
        assert not inputs.data.past_targets.is_empty()
        assert not inputs.data.future_dynamic.is_empty()
        assert metadata.warm_up_source == WarmUpSource.FRESH
        assert metadata.warm_up_state_age_hours is not None
        assert metadata.warm_up_state_age_hours < 24.0
        assert metadata.prior_state == b"state_bytes"
        assert metadata.nwp_age_hours > 0

    def test_missing_nwp_returns_none(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, with_nwp=False)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is None

    def test_missing_observations_returns_inputs_with_none_staleness(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, with_obs=False)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        _, metadata = result
        assert metadata.observation_staleness_hours is None

    def test_stale_warm_up_state_returns_snapshot(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, state_age_hours=30.0)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        _, metadata = result
        assert metadata.warm_up_source == WarmUpSource.SNAPSHOT
        assert metadata.warm_up_state_age_hours is not None
        assert metadata.warm_up_state_age_hours >= 24.0

    def test_no_warm_up_state_returns_cold_start(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, with_state=False)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        _, metadata = result
        assert metadata.warm_up_source == WarmUpSource.COLD_START
        assert metadata.warm_up_state_age_hours is None
        assert metadata.prior_state is None

    def test_empty_past_dynamic_features_skips_reanalysis(self) -> None:
        from sapphire_flow.types.enums import ArtifactScope
        from sapphire_flow.types.model import ModelDataRequirements

        class _NoPastDynamicModel:
            artifact_scope = ArtifactScope.STATION
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset({"precipitation"}),
                static_features=frozenset(),
                supported_time_steps=frozenset({timedelta(hours=1)}),
                lookback_steps=10,
                spatial_input_type=SpatialRepresentation.POINT,
            )

            def train(self, *a, **kw):  # type: ignore[no-untyped-def]
                return b""

            def predict(self, *a, **kw):  # type: ignore[no-untyped-def]
                return ({}, None)

            def serialize_artifact(self, a):  # type: ignore[no-untyped-def]
                return b""

            def deserialize_artifact(self, r):  # type: ignore[no-untyped-def]
                return r

        sid = StationId(uuid4())
        station_store, basin_store, obs_store, nwp_store, state_store, _ = (
            _make_stores_and_sources(sid)
        )
        # Use a reanalysis that would fail if called — pass empty one
        empty_reanalysis = FakeWeatherReanalysisSource(records=[])

        # Seed NWP with only "precipitation"
        nwp_store2 = FakeWeatherForecastStore()
        nwp_records = _make_nwp_records(
            station_id=sid,
            cycle_time=_CYCLE,
            start=_ISSUE,
            n_steps=10,
            parameters=["precipitation"],
            n_members=1,
        )
        nwp_store2.store_weather_forecasts(nwp_records)

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=_NoPastDynamicModel(),  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=empty_reanalysis,
            weather_forecast_store=nwp_store2,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=10,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        inputs, _ = result
        assert inputs.data.past_dynamic.is_empty()
