from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.flows.run_forecast_cycle import (
    ForecastCycleResult,
    run_forecast_cycle_flow,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import (
    ModelArtifactStatus,
    ModelAssignmentStatus,
    SpatialRepresentation,
    StationKind,
    StationStatus,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.station import ModelAssignment, StationWeatherSource
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import make_observations, make_station_config
from tests.fakes.fake_adapters import FakeWeatherForecastSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

_NOW = ensure_utc(datetime(2026, 4, 1, 6, 0, tzinfo=UTC))
_NWP_SOURCE = "icon_ch2_eps"
_MODEL_ID = ModelId("fake_station_model")


def _clock() -> UtcDatetime:
    return _NOW


def _make_config(**overrides: object) -> DeploymentConfig:
    defaults: dict[str, object] = {"max_retention_days": 3650}
    defaults.update(overrides)
    return DeploymentConfig(**defaults)  # type: ignore[arg-type]


def _empty_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0", rules=())


def _make_nwp_records(
    station_id: StationId,
    n_steps: int = 120,
    n_members: int = 3,
) -> list[WeatherForecastRecord]:
    cycle_time = _NOW
    records = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for param in ["precipitation", "temperature"]:
            for m in range(n_members):
                records.append(
                    WeatherForecastRecord(
                        id=uuid4(),
                        station_id=station_id,
                        nwp_source=_NWP_SOURCE,
                        cycle_time=cycle_time,
                        valid_time=vt,
                        parameter=param,
                        spatial_type=SpatialRepresentation.POINT,
                        band_id=None,
                        member_id=m,
                        value=float(step + m),
                        created_at=_NOW,
                    )
                )
    return records


def _build_station_and_stores(
    station_id: StationId,
    model_id: ModelId,
    station_store: FakeStationStore,
    obs_store: FakeObservationStore,
    nwp_store: FakeWeatherForecastStore,
    artifact_store: FakeModelArtifactStore,
    forcing_store: FakeHistoricalForcingStore,
    *,
    n_obs: int = 30,
    seed_nwp: bool = True,
) -> None:
    """Register a station with all required data in the fakes."""
    station = make_station_config(
        station_id=station_id,
        station_kind=StationKind.RIVER,
        station_status=StationStatus.OPERATIONAL,
        measured_parameters=frozenset({"discharge"}),
        forecast_targets=frozenset({"discharge"}),
    )
    station_store.store_station(station)

    assignment = ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=timedelta(hours=1),
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_NOW,
    )
    station_store.store_model_assignment(assignment)

    source = StationWeatherSource(
        station_id=station_id,
        nwp_source=_NWP_SOURCE,
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
    )
    station_store.store_weather_source(source)

    # Observations for staleness check
    obs_start = ensure_utc(
        datetime.fromtimestamp(_NOW.timestamp() - n_obs * 3600, tz=UTC)
    )
    observations = make_observations(
        n=n_obs,
        station_id=station_id,
        parameter="discharge",
        start=obs_start,
        interval=timedelta(hours=1),
    )
    obs_store.store_observations(observations)

    # NWP records in the store (so assemble_station_operational_inputs can fetch them)
    if seed_nwp:
        records = _make_nwp_records(station_id)
        nwp_store.store_weather_forecasts(records)

    # Historical forcing (past_dynamic via StoreBackedReanalysisSource)
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

    forcing_start = ensure_utc(
        datetime.fromtimestamp(_NOW.timestamp() - 30 * 3600, tz=UTC)
    )
    raw_forcing = []
    for i in range(30):
        ts = ensure_utc(
            datetime.fromtimestamp(forcing_start.timestamp() + i * 3600, tz=UTC)
        )
        for param in ["precipitation", "temperature"]:
            raw_forcing.append(
                RawHistoricalForcing(
                    station_id=station_id,
                    source=_NWP_SOURCE,
                    version="1.0",
                    valid_time=ts,
                    parameter=param,
                    spatial_type=SpatialRepresentation.POINT,
                    band_id=None,
                    member_id=None,
                    value=float(i % 10),
                )
            )
    forcing_store.store_forcing(raw_forcing)

    # Active artifact
    artifact_store.store_artifact(
        model_id=model_id,
        artifact_bytes=b"fake_artifact",
        training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
        training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
        trained_at=_NOW,
        station_id=station_id,
        status=ModelArtifactStatus.ACTIVE,
    )


class _SmallFakeModel(FakeStationForecastModel):
    """Fake model with small lookback so tests don't need years of data."""

    from sapphire_flow.types.model import ModelDataRequirements

    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class TestForecastCycle:
    def test_happy_path(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        model = _SmallFakeModel()
        models = {_MODEL_ID: model}

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
            )

        adapter = FakeWeatherForecastSource(result={})  # NWP fetch returns empty dict

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert isinstance(result, ForecastCycleResult)
        assert result.stations_succeeded == 2
        assert result.forecasts_stored == 2
        assert len(forecast_store._forecasts) == 2
        # Warm-up state persisted for both stations
        assert (sid_a, _MODEL_ID) in state_store._states
        assert (sid_b, _MODEL_ID) in state_store._states

    def test_nwp_fetch_failure(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        class _BrokenAdapter:
            def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError("NWP API unavailable")

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_BrokenAdapter(),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.forecasts_stored == 0
        assert "NWP fetch failed" in result.errors
        assert len(forecast_store._forecasts) == 0

    def test_empty_stations(self) -> None:
        # Station store has no stations at all
        result = run_forecast_cycle_flow(
            station_store=FakeStationStore(),
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            adapter=FakeWeatherForecastSource(result={}),
            models={},
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.stations_succeeded == 0
        assert result.forecasts_stored == 0
        assert result.alerts_checked is False

    def test_non_operational_stations_excluded(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        # Station is ONBOARDING, not OPERATIONAL
        station = make_station_config(
            station_id=sid,
            station_kind=StationKind.RIVER,
            station_status=StationStatus.ONBOARDING,
            measured_parameters=frozenset({"discharge"}),
        )
        station_store.store_station(station)

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            adapter=FakeWeatherForecastSource(result={}),
            models={},
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.stations_succeeded == 0

    def test_alerts_checked_when_enabled(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        config = _make_config(enable_forecast_alerts=True)
        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=config,
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1
        assert result.alerts_checked is True
