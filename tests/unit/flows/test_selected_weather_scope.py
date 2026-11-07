from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import WeatherEvidenceCaptureError
from sapphire_flow.types.enums import (
    AlertEligibility,
    ForecastCycleHealth,
    ForecastDataUse,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.station import GroupModelAssignment, ModelAssignment
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.unit.adapters.test_recap_operational import ISSUE
from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle

if TYPE_CHECKING:
    import pytest

    from sapphire_flow.config.deployment import DeploymentConfig
    from tests.fakes.fake_stores import FakeStationGroupStore


def test_provisional_weather_excludes_ordinary_station_and_group_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    station_mid = ModelId("ordinary-hourly")
    group_mid = ModelId("ordinary-snow")
    ordinary_station = FakeStationForecastModel()
    ordinary_group = FakeGroupForecastModel()
    ordinary_group.data_requirements = replace(
        ordinary_group.data_requirements,
        past_dynamic_features=frozenset({"snow_water_equivalent"}),
    )

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        assert groups is not None
        group = groups.fetch_groups_for_model(mid)[0]
        sid = next(iter(group.station_ids))
        stores["station_store"].store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=station_mid,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group.id,
                model_id=group_mid,
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        return {}

    forecasts = _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        data_use=ForecastDataUse.PROVISIONAL_RATING,
        configure_cycle=configure,
        additional_models={station_mid: ordinary_station, group_mid: ordinary_group},
    )
    assert forecasts is not None and len(forecasts) == 1


def test_selected_mixed_group_capture_failure_preserves_healthy_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    runner = importlib.import_module("sapphire_flow.services.run_group_forecast")
    capture = runner.capture_group_evidence
    failed_mid = ModelId("selected-failed-sibling")
    failing = FakeGroupForecastModel()
    failing.alert_eligibility = AlertEligibility.SKILL_FORECAST
    failing.data_requirements = replace(
        failing.data_requirements,
        supported_time_steps=frozenset({timedelta(days=1)}),
        lookback_steps=2,
        forecast_horizon_steps=10,
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
    )

    def fail_capture(**kwargs: object) -> object:
        if kwargs["model_id"] == failed_mid:
            raise WeatherEvidenceCaptureError("fixture selected capture failure")
        return capture(**kwargs)

    monkeypatch.setattr(runner, "capture_group_evidence", fail_capture)

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        assert groups is not None
        group = groups.fetch_groups_for_model(mid)[0]
        groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group.id,
                model_id=failed_mid,
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        stores["artifact_store"].store_artifact(
            model_id=failed_mid,
            group_id=group.id,
            artifact_bytes=b"fake_artifact",
            training_period_start=ISSUE - timedelta(days=100),
            training_period_end=ISSUE - timedelta(days=10),
            trained_at=ISSUE,
            status=ModelArtifactStatus.ACTIVE,
        )
        return {}

    forecasts = _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        configure_cycle=configure,
        additional_models={failed_mid: failing},
        expected_health=ForecastCycleHealth.DEGRADED,
    )
    assert forecasts is not None and len(forecasts) == 1
    assert forecasts[0].model_id != failed_mid


def test_selected_standard_incompatible_assignment_refused_before_cache_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pytest

    from sapphire_flow.exceptions import ConfigurationError
    from tests.fakes.operational_weather import FakeOperationalWeatherCache
    from tests.unit.flows.test_selected_weather_cycle import FixtureOperationalApi

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "incompatible roster must fail before cache/provider access"
        )

    monkeypatch.setattr(FakeOperationalWeatherCache, "begin", forbidden)
    monkeypatch.setattr(FakeOperationalWeatherCache, "fetch", forbidden)
    monkeypatch.setattr(FixtureOperationalApi, "operational", forbidden)
    ordinary_mid = ModelId("incompatible-hourly-station")

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        assert groups is not None
        sid = next(iter(groups.fetch_groups_for_model(mid)[0].station_ids))
        stores["station_store"].store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=ordinary_mid,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        return {}

    with pytest.raises(ConfigurationError, match="daily|time step"):
        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            configure_cycle=configure,
            additional_models={ordinary_mid: FakeStationForecastModel()},
        )
