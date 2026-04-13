from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl

from sapphire_flow.protocols.adapters import (
    ForeignForecastSource,
    PipelineStatusSource,
    StationDataSource,
    WeatherForecastSource,
    WeatherReanalysisSource,
)
from sapphire_flow.protocols.alert_strategy import ModelAlertStrategy
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.protocols.notification import NotificationAdapter
from sapphire_flow.protocols.stores import (
    AlertStore,
    BasinStore,
    ClimBaselineStore,
    FlowRegimeConfigStore,
    ForecastAdjustmentStore,
    ForecastQualityChecker,
    ForecastStore,
    ForeignForecastStore,
    HindcastStore,
    HistoricalForcingStore,
    ModelArtifactStore,
    ModelStateStore,
    ModelStore,
    ObservationStore,
    ParameterStore,
    PipelineHealthStore,
    QualityChecker,
    RatingCurveStore,
    SkillStore,
    StationGroupStore,
    StationStore,
    WeatherForecastStore,
)
from tests.fakes.fake_adapters import (
    FakeForeignForecastSource,
    FakeNotificationAdapter,
    FakePipelineStatusSource,
    FakeStationDataSource,
    FakeWeatherForecastSource,
    FakeWeatherReanalysisSource,
)
from tests.fakes.fake_clock import FakeClock  # noqa: F401
from tests.fakes.fake_models import (
    FakeGroupForecastModel,
    FakeMultiTargetGroupForecastModel,
    FakeMultiTargetStationForecastModel,
    FakeStationForecastModel,
)
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeFlowRegimeConfigStore,
    FakeForecastAdjustmentStore,
    FakeForecastStore,
    FakeForeignForecastStore,
    FakeHindcastStore,
    FakeHistoricalForcingStore,
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

    def test_historical_forcing_store(self) -> None:
        assert isinstance(FakeHistoricalForcingStore(), HistoricalForcingStore)

    def test_clim_baseline_store(self) -> None:
        assert isinstance(FakeClimBaselineStore(), ClimBaselineStore)


class TestFakeAdapterConformance:
    def test_weather_forecast_source(self) -> None:
        assert isinstance(FakeWeatherForecastSource(), WeatherForecastSource)

    def test_station_data_source(self) -> None:
        assert isinstance(FakeStationDataSource(), StationDataSource)

    def test_pipeline_status_source(self) -> None:
        assert isinstance(FakePipelineStatusSource(), PipelineStatusSource)

    def test_foreign_forecast_source(self) -> None:
        assert isinstance(FakeForeignForecastSource(), ForeignForecastSource)

    def test_weather_reanalysis_source(self) -> None:
        assert isinstance(FakeWeatherReanalysisSource(), WeatherReanalysisSource)


class TestFakeModelConformance:
    def test_station_forecast_model(self) -> None:
        assert isinstance(FakeStationForecastModel(), StationForecastModel)

    def test_group_forecast_model(self) -> None:
        assert isinstance(FakeGroupForecastModel(), GroupForecastModel)

    def test_multi_target_station_forecast_model(self) -> None:
        assert isinstance(FakeMultiTargetStationForecastModel(), StationForecastModel)

    def test_multi_target_group_forecast_model(self) -> None:
        assert isinstance(FakeMultiTargetGroupForecastModel(), GroupForecastModel)


class TestFakeNotificationAdapterConformance:
    def test_notification_adapter(self) -> None:
        assert isinstance(FakeNotificationAdapter(), NotificationAdapter)


class TestAlertStrategyConformance:
    def test_primary_model_strategy(self) -> None:
        from sapphire_flow.services.alert_strategy import PrimaryModelStrategy

        assert isinstance(PrimaryModelStrategy(), ModelAlertStrategy)

    def test_pooled_ensemble_strategy(self) -> None:
        from sapphire_flow.services.alert_strategy import PooledEnsembleStrategy

        assert isinstance(PooledEnsembleStrategy(), ModelAlertStrategy)


class TestQualityCheckerConformance:
    def test_stage1_quality_checker(self) -> None:
        from sapphire_flow.services.qc import Stage1QualityChecker

        assert isinstance(Stage1QualityChecker(), QualityChecker)

    def test_forecast_output_quality_checker(self) -> None:
        from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker

        assert isinstance(ForecastOutputQualityChecker(), ForecastQualityChecker)


_RNG = random.Random(99)
_EPOCH = datetime(2025, 1, 15, 0, 0, tzinfo=UTC)


def _fake_uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _build_hindcast(*, parameter: str = "discharge") -> HindcastForecast:  # noqa: F821
    from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import (
        ArtifactId,
        HindcastForecastId,
        ModelId,
        StationId,
    )

    station_id = StationId(_fake_uuid())
    model_id = ModelId("test_model")
    issued_at: UtcDatetime = ensure_utc(_EPOCH)
    time_step = timedelta(hours=1)
    vt = ensure_utc(datetime(2025, 1, 15, 1, 0, tzinfo=UTC))

    df = pl.DataFrame(
        [{"valid_time": vt, "member_id": m, "value": 10.0 + m} for m in range(3)]
    ).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )

    units = "m3/s" if parameter == "discharge" else "m"
    ensemble = ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=issued_at,
        parameter=parameter,
        units=units,
        time_step=time_step,
        values=df,
    )
    return HindcastForecast(
        id=HindcastForecastId(_fake_uuid()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=ArtifactId(_fake_uuid()),
        hindcast_step=issued_at,
        forcing_type=ForcingType.REANALYSIS,
        representation=EnsembleRepresentation.MEMBERS,
        hindcast_run_id=_fake_uuid(),
        ensemble=ensemble,
        created_at=issued_at,
    )


class TestFakeHindcastStoreParameterFilter:
    def test_parameter_none_returns_all(self) -> None:
        from sapphire_flow.types.datetime import ensure_utc

        store = FakeHindcastStore()
        h1 = _build_hindcast(parameter="discharge")
        h2 = _build_hindcast(parameter="water_level")

        # Both must share station_id/model_id for the filter to matter
        # Rebuild h2 to share station_id and model_id with h1
        from dataclasses import replace as dc_replace

        h2 = dc_replace(h2, station_id=h1.station_id, model_id=h1.model_id)

        store.store_hindcast(h1)
        store.store_hindcast(h2)

        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))

        result = store.fetch_hindcasts(
            station_id=h1.station_id,
            model_id=h1.model_id,
            start=start,
            end=end,
            parameter=None,
        )
        assert len(result) == 2

    def test_parameter_filters_exact_match(self) -> None:
        from sapphire_flow.types.datetime import ensure_utc

        store = FakeHindcastStore()
        h1 = _build_hindcast(parameter="discharge")
        h2 = _build_hindcast(parameter="water_level")

        from dataclasses import replace as dc_replace

        h2 = dc_replace(h2, station_id=h1.station_id, model_id=h1.model_id)

        store.store_hindcast(h1)
        store.store_hindcast(h2)

        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))

        result = store.fetch_hindcasts(
            station_id=h1.station_id,
            model_id=h1.model_id,
            start=start,
            end=end,
            parameter="discharge",
        )
        assert len(result) == 1
        assert result[0].ensemble.parameter == "discharge"


class TestFakeSkillStoreParameterFilter:
    def test_fetch_scores_filters_by_parameter(self) -> None:
        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import SkillFreshness, SkillSource
        from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
        from sapphire_flow.types.skill import SkillScore

        store = FakeSkillStore()
        sid = StationId(_fake_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_fake_uuid())
        now = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))

        def _score(parameter: str) -> SkillScore:
            return SkillScore(
                id=_fake_uuid(),
                station_id=sid,
                model_id=mid,
                parameter=parameter,
                model_artifact_id=aid,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=None,
                computation_version=1,
                computed_at=now,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                metric="crps",
                score=0.5,
                sample_size=100,
                freshness=SkillFreshness.CURRENT,
                eval_period_start=now,
                eval_period_end=now,
                created_at=now,
            )

        store.store_skill_scores([_score("discharge"), _score("water_level")])

        discharge_only = store.fetch_latest_scores(sid, mid, parameter="discharge")
        assert len(discharge_only) == 1
        assert discharge_only[0].parameter == "discharge"

        all_params = store.fetch_latest_scores(sid, mid, parameter=None)
        assert len(all_params) == 2

    def test_mark_stale_filters_by_parameter(self) -> None:

        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import SkillFreshness, SkillSource
        from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
        from sapphire_flow.types.skill import SkillScore

        store = FakeSkillStore()
        sid = StationId(_fake_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_fake_uuid())
        t0 = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
        t1 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
        t2 = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))

        def _score(parameter: str) -> SkillScore:
            return SkillScore(
                id=_fake_uuid(),
                station_id=sid,
                model_id=mid,
                parameter=parameter,
                model_artifact_id=aid,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=None,
                computation_version=1,
                computed_at=t1,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                metric="crps",
                score=0.5,
                sample_size=100,
                freshness=SkillFreshness.CURRENT,
                eval_period_start=t0,
                eval_period_end=t1,
                created_at=t1,
            )

        store.store_skill_scores([_score("discharge"), _score("water_level")])

        count = store.mark_stale(sid, t0, t2, parameter="discharge")
        assert count == 1

        all_scores = store.fetch_latest_scores(sid, mid)
        discharge = [s for s in all_scores if s.parameter == "discharge"]
        water_level = [s for s in all_scores if s.parameter == "water_level"]
        assert discharge[0].freshness == SkillFreshness.STALE
        assert water_level[0].freshness == SkillFreshness.CURRENT
