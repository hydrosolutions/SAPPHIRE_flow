from __future__ import annotations

from sapphire_flow.protocols.adapters import (
    ForeignForecastSource,
    PipelineStatusSource,
    StationDataSource,
    WeatherForecastSource,
)
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.protocols.stores import (
    AlertStore,
    BasinStore,
    FlowRegimeConfigStore,
    ForecastAdjustmentStore,
    ForecastStore,
    ForeignForecastStore,
    HindcastStore,
    ModelArtifactStore,
    ModelStateStore,
    ModelStore,
    ObservationStore,
    ParameterStore,
    PipelineHealthStore,
    RatingCurveStore,
    SkillStore,
    StationGroupStore,
    StationStore,
    WeatherForecastStore,
)
from tests.fakes.fake_adapters import (
    FakeForeignForecastSource,
    FakePipelineStatusSource,
    FakeStationDataSource,
    FakeWeatherForecastSource,
)
from tests.fakes.fake_clock import FakeClock  # noqa: F401
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeFlowRegimeConfigStore,
    FakeForecastAdjustmentStore,
    FakeForecastStore,
    FakeForeignForecastStore,
    FakeHindcastStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeModelStore,
    FakeObservationStore,
    FakeParameterStore,
    FakePipelineHealthStore,
    FakeRatingCurveStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)


class TestFakeStoreConformance:
    def test_observation_store(self) -> None:
        assert isinstance(FakeObservationStore(), ObservationStore)

    def test_forecast_store(self) -> None:
        assert isinstance(FakeForecastStore(), ForecastStore)

    def test_hindcast_store(self) -> None:
        assert isinstance(FakeHindcastStore(), HindcastStore)

    def test_weather_forecast_store(self) -> None:
        assert isinstance(FakeWeatherForecastStore(), WeatherForecastStore)

    def test_alert_store(self) -> None:
        assert isinstance(FakeAlertStore(), AlertStore)

    def test_skill_store(self) -> None:
        assert isinstance(FakeSkillStore(), SkillStore)

    def test_model_artifact_store(self) -> None:
        assert isinstance(FakeModelArtifactStore(), ModelArtifactStore)

    def test_model_store(self) -> None:
        assert isinstance(FakeModelStore(), ModelStore)

    def test_model_state_store(self) -> None:
        assert isinstance(FakeModelStateStore(), ModelStateStore)

    def test_station_store(self) -> None:
        assert isinstance(FakeStationStore(), StationStore)

    def test_station_group_store(self) -> None:
        assert isinstance(FakeStationGroupStore(), StationGroupStore)

    def test_pipeline_health_store(self) -> None:
        assert isinstance(FakePipelineHealthStore(), PipelineHealthStore)

    def test_rating_curve_store(self) -> None:
        assert isinstance(FakeRatingCurveStore(), RatingCurveStore)

    def test_flow_regime_config_store(self) -> None:
        assert isinstance(FakeFlowRegimeConfigStore(), FlowRegimeConfigStore)

    def test_forecast_adjustment_store(self) -> None:
        assert isinstance(FakeForecastAdjustmentStore(), ForecastAdjustmentStore)

    def test_basin_store(self) -> None:
        assert isinstance(FakeBasinStore(), BasinStore)

    def test_parameter_store(self) -> None:
        assert isinstance(FakeParameterStore(), ParameterStore)

    def test_foreign_forecast_store(self) -> None:
        assert isinstance(FakeForeignForecastStore(), ForeignForecastStore)


class TestFakeAdapterConformance:
    def test_weather_forecast_source(self) -> None:
        assert isinstance(FakeWeatherForecastSource(), WeatherForecastSource)

    def test_station_data_source(self) -> None:
        assert isinstance(FakeStationDataSource(), StationDataSource)

    def test_pipeline_status_source(self) -> None:
        assert isinstance(FakePipelineStatusSource(), PipelineStatusSource)

    def test_foreign_forecast_source(self) -> None:
        assert isinstance(FakeForeignForecastSource(), ForeignForecastSource)


class TestFakeModelConformance:
    def test_station_forecast_model(self) -> None:
        assert isinstance(FakeStationForecastModel(), StationForecastModel)

    def test_group_forecast_model(self) -> None:
        assert isinstance(FakeGroupForecastModel(), GroupForecastModel)
