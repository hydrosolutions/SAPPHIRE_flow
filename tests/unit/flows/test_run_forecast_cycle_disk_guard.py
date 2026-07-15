"""LOCKED regression/acceptance tests for Plan 105 — flow-level disk guard.

Covers the _fetch_nwp_task's two new except clauses:
  * DiskSoftLimitError → flow returns HEALTHY or DEGRADED (never FAILED);
    a WARNING DISK_USAGE PipelineHealthRecord is written.
  * DiskHardLimitError → flow returns FAILED (via the existing None-outcome
    abort at run_forecast_cycle.py:~1210); a CRITICAL DISK_USAGE record is
    written.

Soundness rules:
  * Tests drive the PUBLIC `run_forecast_cycle_flow` entry point, never the
    private `_fetch_nwp_task` directly.
  * Fake NWP adapters raise the exact Plan-105 exceptions from their public
    `fetch_forecasts` method.
  * No invented/guessed params — adapters satisfy the WeatherForecastSource
    Protocol (station_configs, cycle_time positional).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import uuid4

from sapphire_flow.exceptions import DiskHardLimitError, DiskSoftLimitError
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    ForecastCycleHealth,
    ModelAssignmentStatus,
    PipelineCheckType,
    PipelineHealthStatus,
    SpatialRepresentation,
    StationKind,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.station import ModelAssignment, StationWeatherSource
from tests.conftest import make_observations, make_station_config
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
    FakePipelineHealthStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

_NOW = ensure_utc(datetime(2026, 4, 1, 6, 0, tzinfo=UTC))
_NWP_SOURCE = "icon_ch2_eps"
_MODEL_ID = ModelId("fake_station_model")


def _clock() -> UtcDatetime:
    return _NOW


def _make_config() -> object:
    from sapphire_flow.config.deployment import DeploymentConfig

    return DeploymentConfig(max_retention_days=3650)


def _empty_qc_rules() -> object:
    from sapphire_flow.types.domain import ForecastQcRuleSet

    return ForecastQcRuleSet(version="1.0", rules=())


def _seed_minimal_station(
    station_id: StationId,
    station_store: FakeStationStore,
    obs_store: FakeObservationStore,
    nwp_store: FakeWeatherForecastStore,
    artifact_store: FakeModelArtifactStore,
    forcing_store: FakeHistoricalForcingStore,
) -> None:
    """Seed the minimum data for a station so the flow reaches Phase B."""
    from datetime import UTC, datetime

    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import ModelArtifactStatus

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
        model_id=_MODEL_ID,
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
        role=WeatherSourceRole.FORECAST,
    )
    station_store.store_weather_source(source)

    obs_start = ensure_utc(datetime.fromtimestamp(_NOW.timestamp() - 30 * 3600, tz=UTC))
    observations = make_observations(
        n=30,
        station_id=station_id,
        parameter="discharge",
        start=obs_start,
        interval=timedelta(hours=1),
        rng=random.Random(str(station_id)),
    )
    obs_store.store_observations(observations)

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

    artifact_store.store_artifact(
        model_id=_MODEL_ID,
        artifact_bytes=b"fake_artifact",
        training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
        training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
        trained_at=_NOW,
        station_id=station_id,
        status=ModelArtifactStatus.ACTIVE,
    )


class _SoftDiskAdapter:
    """Fake NWP adapter whose fetch_forecasts raises DiskSoftLimitError."""

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> NoReturn:
        raise DiskSoftLimitError(
            "Disk scratch soft limit: 1.0 GB free < 1.5 GB threshold",
            path="/tmp/sapphire_nwp",
            free_gb=1.0,
            threshold_gb=1.5,
            subject="scratch",
        )


class _HardDiskAdapter:
    """Fake NWP adapter whose fetch_forecasts raises DiskHardLimitError."""

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> NoReturn:
        raise DiskHardLimitError(
            "Disk scratch hard limit: 0.1 GB free < 0.5 GB threshold",
            path="/tmp/sapphire_nwp",
            free_gb=0.1,
            threshold_gb=0.5,
            subject="scratch",
        )


class _NativeFakeModel(FakeStationForecastModel):
    """Native model with no NWP features so it can forecast in runoff-only mode."""

    from sapphire_flow.types.enums import AlertEligibility

    alert_eligibility = AlertEligibility.SKILL_FORECAST

    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class TestDiskSoftLimitFlowLevel:
    """DiskSoftLimitError from the adapter: flow degrades to runoff-only, NOT FAILED."""

    def test_soft_breach_flow_health_is_not_failed(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _seed_minimal_station(
            sid,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_SoftDiskAdapter(),
            models={_MODEL_ID: _NativeFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        # Soft breach must NOT produce FAILED — either HEALTHY or DEGRADED.
        assert result.health in (
            ForecastCycleHealth.HEALTHY,
            ForecastCycleHealth.DEGRADED,
        ), f"Expected HEALTHY or DEGRADED on soft breach, got {result.health}"
        assert result.health is not ForecastCycleHealth.FAILED

    def test_soft_breach_emits_warning_disk_usage_record(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _seed_minimal_station(
            sid,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_SoftDiskAdapter(),
            models={_MODEL_ID: _NativeFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        records = pipeline_health_store._records
        disk_records = [
            r for r in records if r.check_type == PipelineCheckType.DISK_USAGE
        ]
        assert len(disk_records) == 1
        assert disk_records[0].status == PipelineHealthStatus.WARNING


class TestDiskHardLimitFlowLevel:
    """DiskHardLimitError from the adapter → flow returns FAILED."""

    def test_hard_breach_flow_health_is_failed(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _seed_minimal_station(
            sid,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_HardDiskAdapter(),
            models={_MODEL_ID: _NativeFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.health is ForecastCycleHealth.FAILED

    def test_hard_breach_emits_critical_disk_usage_record(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _seed_minimal_station(
            sid,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_HardDiskAdapter(),
            models={_MODEL_ID: _NativeFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        records = pipeline_health_store._records
        disk_records = [
            r for r in records if r.check_type == PipelineCheckType.DISK_USAGE
        ]
        assert len(disk_records) == 1
        assert disk_records[0].status == PipelineHealthStatus.CRITICAL
