from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

# Convention: all range queries use half-open intervals [start, end).
# SQL: WHERE timestamp >= start AND timestamp < end
# Fakes must match: start <= x < end (not start <= x <= end).

if TYPE_CHECKING:
    from uuid import UUID

    import polars as pl

    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import (
        ClimBaseline,
        ForecastQcRuleSet,
        ParameterDefinition,
        QcFlag,
        QcRuleSet,
        StationForecastQcOverride,
        StationQcOverride,
        StationThreshold,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import (
        AlertSource,
        FlowRegime,
        ForcingType,
        ForecastStatus,
        ModelArtifactStatus,
        ObservationSource,
        PipelineCheckType,
        QcStatus,
        SkillSource,
        StationKind,
        StationOwnership,
    )
    from sapphire_flow.types.forecast import (
        ForecastAdjustment,
        ForeignForecast,
        HindcastForecast,
        OperationalForecast,
    )
    from sapphire_flow.types.historical_forcing import (
        HistoricalForcingRecord,
        RawHistoricalForcing,
    )
    from sapphire_flow.types.ids import (
        AlertId,
        ArtifactId,
        BasinId,
        ForecastAdjustmentId,
        ForecastId,
        ForeignForecastId,
        HindcastForecastId,
        ModelId,
        ObservationId,
        RatingCurveId,
        StationGroupId,
        StationId,
    )
    from sapphire_flow.types.model import ModelArtifactRecord, ModelRecord
    from sapphire_flow.types.observation import Observation, RawObservation
    from sapphire_flow.types.pipeline import PipelineHealthRecord
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.skill import FlowRegimeConfig, SkillDiagram, SkillScore
    from sapphire_flow.types.station import (
        ModelAssignment,
        StationConfig,
        StationGroup,
        StationWeatherSource,
    )
    from sapphire_flow.types.weather import WeatherForecastRecord


@runtime_checkable
class ObservationStore(Protocol):
    def store_observations(self, observations: list[Observation]) -> None:
        raise NotImplementedError

    def store_raw_observations(
        self, observations: list[RawObservation]
    ) -> list[ObservationId]:
        raise NotImplementedError

    def update_qc(
        self,
        observation_id: ObservationId,
        qc_status: QcStatus,
        qc_flags: list[QcFlag],
        qc_rule_version: str | None = None,
    ) -> None:
        raise NotImplementedError

    def fetch_observations(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> list[Observation]:
        raise NotImplementedError

    def fetch_latest_timestamp(
        self, station_id: StationId, parameter: str
    ) -> UtcDatetime | None:
        raise NotImplementedError

    def fetch_observations_batch(
        self,
        station_ids: list[StationId],
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> dict[StationId, list[Observation]]:
        raise NotImplementedError

    def fetch_derived_observations_by_curve(
        self,
        station_id: StationId,
        rating_curve_id: RatingCurveId,
    ) -> list[Observation]:
        raise NotImplementedError


@runtime_checkable
class ForecastStore(Protocol):
    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        raise NotImplementedError

    def fetch_forecast(self, forecast_id: ForecastId) -> OperationalForecast | None:
        raise NotImplementedError

    def fetch_latest_forecast(
        self,
        station_id: StationId,
        model_id: ModelId | None = None,
    ) -> OperationalForecast | None:
        raise NotImplementedError

    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,
    ) -> list[OperationalForecast]:
        raise NotImplementedError

    def transition_status(
        self,
        forecast_id: ForecastId,
        expected_version: int,
        new_status: ForecastStatus,
    ) -> int:
        raise NotImplementedError

    def fetch_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        status: ForecastStatus | None = None,
    ) -> list[OperationalForecast]:
        raise NotImplementedError


@runtime_checkable
class ForeignForecastStore(Protocol):
    def store_foreign_forecast(self, forecast: ForeignForecast) -> ForeignForecastId:
        raise NotImplementedError

    def fetch_foreign_forecast(
        self, forecast_id: ForeignForecastId
    ) -> ForeignForecast | None:
        raise NotImplementedError

    def fetch_latest_foreign_forecast(
        self, station_id: StationId
    ) -> ForeignForecast | None:
        raise NotImplementedError

    def fetch_foreign_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[ForeignForecast]:
        raise NotImplementedError


@runtime_checkable
class HindcastStore(Protocol):
    def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
        raise NotImplementedError

    def fetch_hindcasts(
        self,
        station_id: StationId,
        model_id: ModelId,
        start: UtcDatetime,
        end: UtcDatetime,
        forcing_type: ForcingType | None = None,
        hindcast_run_id: UUID | None = None,
    ) -> list[HindcastForecast]:
        raise NotImplementedError


@runtime_checkable
class WeatherForecastStore(Protocol):
    def store_weather_forecasts(self, records: list[WeatherForecastRecord]) -> None:
        raise NotImplementedError

    def fetch_weather_forecasts(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        parameters: list[str] | None = None,
    ) -> list[WeatherForecastRecord]:
        raise NotImplementedError

    def fetch_lookback(
        self,
        station_id: StationId,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[WeatherForecastRecord]:
        raise NotImplementedError

    def fetch_received_cycles(
        self,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[UtcDatetime]:
        raise NotImplementedError

    def mark_gap(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        recoverable: bool,
    ) -> None:
        raise NotImplementedError

    def fetch_latest_cycle_time(self, nwp_source: str) -> UtcDatetime | None:
        raise NotImplementedError


@runtime_checkable
class AlertStore(Protocol):
    def upsert_alert(self, alert: Alert) -> AlertId:
        raise NotImplementedError

    def fetch_active_alerts(
        self,
        station_id: StationId | None = None,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        raise NotImplementedError

    def resolve_alert(self, alert_id: AlertId) -> None:
        raise NotImplementedError

    def acknowledge_alert(self, alert_id: AlertId, acknowledged_by: UUID) -> None:
        raise NotImplementedError

    def fetch_alert_history(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        raise NotImplementedError


@runtime_checkable
class SkillStore(Protocol):
    def store_skill_scores(self, scores: list[SkillScore]) -> None:
        raise NotImplementedError

    def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> None:
        raise NotImplementedError

    def fetch_latest_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        skill_source: SkillSource | None = None,
    ) -> list[SkillScore]:
        raise NotImplementedError

    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
    ) -> list[SkillDiagram]:
        raise NotImplementedError

    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
    ) -> list[SkillScore]:
        raise NotImplementedError

    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> int:
        raise NotImplementedError


@runtime_checkable
class ModelArtifactStore(Protocol):
    # The implementation is responsible for persisting artifact_bytes to a configured
    # storage backend (filesystem, S3, etc.) and recording the resulting path in
    # artifact_path on the ModelArtifactRecord. Callers pass raw bytes; the store
    # decides where and how to persist them.
    def store_artifact(
        self,
        model_id: ModelId,
        artifact_bytes: bytes,
        training_period_start: UtcDatetime,
        training_period_end: UtcDatetime,
        trained_at: UtcDatetime,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> ArtifactId:
        raise NotImplementedError

    def fetch_artifact(
        self, artifact_id: ArtifactId
    ) -> tuple[ArtifactId, bytes] | None:
        raise NotImplementedError

    def fetch_active_artifact(
        self,
        model_id: ModelId,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> tuple[ArtifactId, bytes] | None:
        raise NotImplementedError

    def fetch_active_artifact_for_station(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[ArtifactId, bytes] | None:
        raise NotImplementedError

    def fetch_artifact_record(
        self, artifact_id: ArtifactId
    ) -> ModelArtifactRecord | None:
        raise NotImplementedError

    def fetch_artifacts_by_status(
        self,
        model_id: ModelId,
        status: ModelArtifactStatus,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> list[ArtifactId]:
        raise NotImplementedError

    def transition_artifact_status(
        self,
        artifact_id: ArtifactId,
        new_status: ModelArtifactStatus,
        promoted_by: UUID | None = None,
    ) -> None:
        raise NotImplementedError


@runtime_checkable
class ModelStore(Protocol):
    def register_model(self, record: ModelRecord) -> None:
        raise NotImplementedError

    def fetch_model(self, model_id: ModelId) -> ModelRecord | None:
        raise NotImplementedError

    def fetch_all_models(self) -> list[ModelRecord]:
        raise NotImplementedError


@runtime_checkable
class ModelStateStore(Protocol):
    def store_state(
        self,
        station_id: StationId,
        model_id: ModelId,
        issue_time: UtcDatetime,
        state_bytes: bytes,
    ) -> None:
        raise NotImplementedError

    def fetch_latest_state(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[UtcDatetime, bytes] | None:
        raise NotImplementedError


@runtime_checkable
class StationStore(Protocol):
    def fetch_station(self, station_id: StationId) -> StationConfig | None:
        raise NotImplementedError

    def fetch_station_by_code(self, code: str, network: str) -> StationConfig | None:
        raise NotImplementedError

    def fetch_all_stations(
        self, kind: StationKind | None = None
    ) -> list[StationConfig]:
        raise NotImplementedError

    def fetch_stations_by_ownership(
        self,
        ownership: StationOwnership,
        kind: StationKind | None = None,
    ) -> list[StationConfig]:
        raise NotImplementedError

    def store_station(self, station: StationConfig) -> StationId:
        raise NotImplementedError

    def fetch_thresholds(self, station_id: StationId) -> list[StationThreshold]:
        raise NotImplementedError

    def store_thresholds(self, thresholds: list[StationThreshold]) -> None:
        raise NotImplementedError

    def fetch_model_assignments(self, station_id: StationId) -> list[ModelAssignment]:
        raise NotImplementedError

    def store_model_assignment(self, assignment: ModelAssignment) -> None:
        raise NotImplementedError

    def fetch_weather_sources(
        self, station_id: StationId
    ) -> list[StationWeatherSource]:
        raise NotImplementedError

    def store_weather_source(self, source: StationWeatherSource) -> None:
        raise NotImplementedError


@runtime_checkable
class StationGroupStore(Protocol):
    def store_group(self, group: StationGroup) -> None:
        raise NotImplementedError

    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None:
        raise NotImplementedError

    def fetch_group_by_name(self, name: str) -> StationGroup | None:
        raise NotImplementedError

    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]:
        raise NotImplementedError

    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]:
        # All groups with at least one station that has an active model assignment
        # for this model. Used by training scope determination.
        raise NotImplementedError

    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        raise NotImplementedError

    def remove_station_from_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        raise NotImplementedError


@runtime_checkable
class PipelineHealthStore(Protocol):
    def append_health_record(self, record: PipelineHealthRecord) -> None:
        raise NotImplementedError

    def fetch_recent(
        self,
        check_type: PipelineCheckType | None = None,
        limit: int = 100,
    ) -> list[PipelineHealthRecord]:
        raise NotImplementedError


@runtime_checkable
class RatingCurveStore(Protocol):
    def store_rating_curve(self, curve: RatingCurve) -> RatingCurveId:
        raise NotImplementedError

    def fetch_active_curve(self, station_id: StationId) -> RatingCurve | None:
        raise NotImplementedError

    def fetch_curve_at(
        self, station_id: StationId, at: UtcDatetime
    ) -> RatingCurve | None:
        raise NotImplementedError

    def supersede_curve(self, curve_id: RatingCurveId, valid_to: UtcDatetime) -> None:
        raise NotImplementedError


@runtime_checkable
class FlowRegimeConfigStore(Protocol):
    def store_config(self, config: FlowRegimeConfig) -> None:
        raise NotImplementedError

    def fetch_latest(self, station_id: StationId) -> FlowRegimeConfig | None:
        raise NotImplementedError


@runtime_checkable
class ForecastAdjustmentStore(Protocol):
    def store_adjustment(self, adjustment: ForecastAdjustment) -> ForecastAdjustmentId:
        raise NotImplementedError

    def fetch_adjustments(self, forecast_id: ForecastId) -> list[ForecastAdjustment]:
        raise NotImplementedError


@runtime_checkable
class BasinStore(Protocol):
    def fetch_basin(self, basin_id: BasinId) -> Basin | None:
        raise NotImplementedError

    def fetch_basin_by_code(self, code: str, network: str) -> Basin | None:
        raise NotImplementedError

    def fetch_all_basins(self) -> list[Basin]:
        raise NotImplementedError

    def store_basin(self, basin: Basin) -> BasinId:
        raise NotImplementedError


@runtime_checkable
class ParameterStore(Protocol):
    def fetch_all(self) -> list[ParameterDefinition]:
        raise NotImplementedError

    def fetch_by_name(self, name: str) -> ParameterDefinition | None:
        raise NotImplementedError


@runtime_checkable
class HistoricalForcingStore(Protocol):
    def store_forcing(self, records: list[RawHistoricalForcing]) -> None:
        # Upsert keyed on natural key (station_id, source, version, valid_time,
        # parameter, spatial_type, band_id, member_id). IDs assigned by store.
        raise NotImplementedError

    def fetch_forcing(
        self,
        station_id: StationId,
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str] | None = None,
        version: str | None = None,
        member_id: int | None = None,
    ) -> list[HistoricalForcingRecord]:
        raise NotImplementedError

    def fetch_forcing_as_dataframe(
        self,
        station_id: StationId,
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str] | None = None,
        version: str | None = None,
    ) -> pl.DataFrame | None:
        raise NotImplementedError

    def fetch_available_sources(self, station_id: StationId) -> list[str]:
        raise NotImplementedError


@runtime_checkable
class ClimBaselineStore(Protocol):
    def store_baselines(self, baselines: list[ClimBaseline]) -> None:
        # Upsert keyed on (station_id, parameter, day_of_year)
        raise NotImplementedError

    def fetch_baselines(
        self, station_id: StationId, parameter: str
    ) -> list[ClimBaseline]:
        raise NotImplementedError

    def fetch_baseline(
        self, station_id: StationId, parameter: str, day_of_year: int
    ) -> ClimBaseline | None:
        raise NotImplementedError


@runtime_checkable
class QualityChecker(Protocol):
    def check(
        self,
        observations: list[Observation],
        rule_set: QcRuleSet,
        overrides: list[StationQcOverride],
        baselines: list[ClimBaseline],
    ) -> dict[ObservationId, list[QcFlag]]:
        raise NotImplementedError


@runtime_checkable
class ForecastQualityChecker(Protocol):
    def check(
        self,
        ensemble: ForecastEnsemble,
        rule_set: ForecastQcRuleSet,
        overrides: list[StationForecastQcOverride],
        baselines: list[ClimBaseline],
    ) -> list[QcFlag]: ...
