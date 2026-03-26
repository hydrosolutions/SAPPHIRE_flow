from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sapphire_flow.flows.train_models import train_models_flow
from sapphire_flow.services.model_registry import register_models
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import (
    ModelId,
    StationId,
)
from sapphire_flow.types.station import ModelAssignment, StationWeatherSource
from tests.conftest import (
    make_deployment_config,
    make_observations,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG = random.Random(42)


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _utc(year: int, month: int = 1, day: int = 1) -> object:
    return ensure_utc(datetime(year, month, day, tzinfo=UTC))


def _make_forcing_records(
    station_id: StationId,
    start: object,
    n_days: int,
) -> list:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

    records = []
    for i in range(n_days):
        ts = ensure_utc(
            datetime.fromtimestamp(
                start.timestamp() + i * 86400,
                tz=UTC,
            )
        )
        for param in ("precipitation", "temperature"):
            records.append(
                RawHistoricalForcing(
                    station_id=station_id,
                    source="smn",
                    version="1.0",
                    valid_time=ts,
                    parameter=param,
                    spatial_type=SpatialRepresentation.POINT,
                    band_id=None,
                    member_id=None,
                    value=5.0 if param == "precipitation" else 10.0,
                )
            )
    return records


def _setup_stores(
    *,
    station_id: StationId,
    model_id: ModelId,
    model: FakeStationForecastModel,
    n_obs_days: int,
    training_start: object,
) -> tuple:
    model_store = FakeModelStore()
    station_store = FakeStationStore()
    group_store = FakeStationGroupStore()
    obs_store = FakeObservationStore()
    basin_store = FakeBasinStore()
    artifact_store = FakeModelArtifactStore()
    hindcast_store = FakeHindcastStore()
    skill_store = FakeSkillStore()
    flow_regime_store = FakeFlowRegimeConfigStore()

    # Register model
    clock = lambda: _EPOCH  # noqa: E731
    register_models({model_id: model}, model_store, clock)

    # Create station with no basin (model has no static_features requirement)
    station = make_station_config(station_id=station_id)
    station_store.store_station(station)

    # Create model assignment
    assignment = ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=timedelta(days=1),
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_EPOCH,
    )
    station_store.store_model_assignment(assignment)

    # Create weather source
    weather_source = StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
    )
    station_store.store_weather_source(weather_source)

    # Store observations
    if n_obs_days > 0:
        obs = make_observations(
            n=n_obs_days,
            station_id=station_id,
            parameter="discharge",
            start=training_start,
            interval=timedelta(days=1),
            rng=random.Random(99),
        )
        obs_store.store_observations(obs)

    # Setup forcing
    forcing_records = _make_forcing_records(station_id, training_start, n_obs_days)
    forcing_source = FakeWeatherReanalysisSource(records=forcing_records)

    return (
        model_store,
        station_store,
        group_store,
        obs_store,
        basin_store,
        artifact_store,
        hindcast_store,
        skill_store,
        flow_regime_store,
        forcing_source,
    )


class TestEndToEndTrainingPipeline:
    def test_end_to_end_training_pipeline(self) -> None:
        station_id = StationId(_uuid())
        model_id = ModelId("fake_station_model")
        model = FakeStationForecastModel()
        training_start = _utc(2022, 1, 1)
        n_obs_days = 365 * 3 + 10  # ~3 years

        (
            model_store,
            station_store,
            group_store,
            obs_store,
            basin_store,
            artifact_store,
            hindcast_store,
            skill_store,
            flow_regime_store,
            forcing_source,
        ) = _setup_stores(
            station_id=station_id,
            model_id=model_id,
            model=model,
            n_obs_days=n_obs_days,
            training_start=training_start,
        )

        deployment_config = make_deployment_config()

        results = train_models_flow(
            period_start="2022-01-01T00:00:00+00:00",
            period_end="2024-12-31T00:00:00+00:00",
            model_store=model_store,
            station_store=station_store,
            group_store=group_store,
            obs_store=obs_store,
            basin_store=basin_store,
            artifact_store=artifact_store,
            hindcast_store=hindcast_store,
            skill_store=skill_store,
            flow_regime_store=flow_regime_store,
            forcing_source=forcing_source,
            models={model_id: model},
            clock=lambda: _EPOCH,
            rng=random.Random(0),
            deployment_config=deployment_config,
        )

        assert len(results) == 1
        result = results[0]
        assert result.error is None
        assert result.artifact_id is not None

        # Artifact should be ACTIVE in store
        active = artifact_store.fetch_active_artifact(model_id, station_id=station_id)
        assert active is not None

        # Hindcast records should exist
        assert len(hindcast_store._hindcasts) > 0

        # Skill scores should exist
        assert len(skill_store._scores) > 0
        assert result.skill_computed is True

    def test_pipeline_insufficient_data(self) -> None:
        station_id = StationId(_uuid())
        model_id = ModelId("fake_station_model")
        model = FakeStationForecastModel()
        training_start = _utc(2022, 1, 1)

        (
            model_store,
            station_store,
            group_store,
            obs_store,
            basin_store,
            artifact_store,
            hindcast_store,
            skill_store,
            flow_regime_store,
            forcing_source,
        ) = _setup_stores(
            station_id=station_id,
            model_id=model_id,
            model=model,
            n_obs_days=0,  # no observations
            training_start=training_start,
        )

        results = train_models_flow(
            period_start="2022-01-01T00:00:00+00:00",
            period_end="2024-12-31T00:00:00+00:00",
            model_store=model_store,
            station_store=station_store,
            group_store=group_store,
            obs_store=obs_store,
            basin_store=basin_store,
            artifact_store=artifact_store,
            hindcast_store=hindcast_store,
            skill_store=skill_store,
            flow_regime_store=flow_regime_store,
            forcing_source=forcing_source,
            models={model_id: model},
            clock=lambda: _EPOCH,
            rng=random.Random(0),
        )

        assert len(results) == 1
        result = results[0]
        assert result.error is not None
        assert "insufficient data" in result.error.lower() or result.error is not None
        assert result.artifact_id is None
        assert result.skill_computed is False
