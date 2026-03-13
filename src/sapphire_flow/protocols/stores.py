from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ParameterDefinition, QcFlag, StationThreshold
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
    )
    from sapphire_flow.types.forecast import (
        ForecastAdjustment,
        HindcastForecast,
        OperationalForecast,
    )
    from sapphire_flow.types.ids import (
        AlertId,
        ArtifactId,
        BasinId,
        ForecastAdjustmentId,
        ForecastId,
        HindcastForecastId,
        ModelId,
        ObservationId,
        RatingCurveId,
        StationGroupId,
        StationId,
    )
    from sapphire_flow.types.model import ModelArtifactRecord, ModelRegistryEntry
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
    def store_observations(self, observations: list[Observation]) -> None: ...
    def store_raw_observations(
        self, observations: list[RawObservation]
    ) -> list[ObservationId]: ...
    def update_qc(
        self,
        observation_id: ObservationId,
        qc_status: QcStatus,
        qc_flags: list[QcFlag],
    ) -> None: ...
    def fetch_observations(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> list[Observation]: ...
    def fetch_latest_timestamp(
        self, station_id: StationId, parameter: str
    ) -> UtcDatetime | None: ...
    def fetch_observations_batch(
        self,
        station_ids: list[StationId],
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> dict[StationId, list[Observation]]: ...
    def fetch_derived_observations_by_curve(
        self,
        station_id: StationId,
        rating_curve_id: RatingCurveId,
    ) -> list[Observation]: ...


@runtime_checkable
class ForecastStore(Protocol):
    def store_forecast(self, forecast: OperationalForecast) -> ForecastId: ...
    def fetch_forecast(self, forecast_id: ForecastId) -> OperationalForecast | None: ...
    def fetch_latest_forecast(
        self,
        station_id: StationId,
        model_id: ModelId | None = None,
    ) -> OperationalForecast | None: ...
    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,
    ) -> list[OperationalForecast]: ...
    def transition_status(
        self,
        forecast_id: ForecastId,
        expected_version: int,
        new_status: ForecastStatus,
    ) -> int: ...
    def fetch_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        status: ForecastStatus | None = None,
    ) -> list[OperationalForecast]: ...


@runtime_checkable
class HindcastStore(Protocol):
    def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId: ...
    def fetch_hindcasts(
        self,
        station_id: StationId,
        model_id: ModelId,
        start: UtcDatetime,
        end: UtcDatetime,
        forcing_type: ForcingType | None = None,
        hindcast_run_id: UUID | None = None,
    ) -> list[HindcastForecast]: ...


@runtime_checkable
class WeatherForecastStore(Protocol):
    def store_weather_forecasts(self, records: list[WeatherForecastRecord]) -> None: ...
    def fetch_weather_forecasts(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        parameters: list[str] | None = None,
    ) -> list[WeatherForecastRecord]: ...
    def fetch_lookback(
        self,
        station_id: StationId,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[WeatherForecastRecord]: ...
    def fetch_received_cycles(
        self,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[UtcDatetime]: ...
    def mark_gap(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        recoverable: bool,
    ) -> None: ...
    def fetch_latest_cycle_time(self, nwp_source: str) -> UtcDatetime | None: ...


@runtime_checkable
class AlertStore(Protocol):
    def upsert_alert(self, alert: Alert) -> AlertId: ...
    def fetch_active_alerts(
        self,
        station_id: StationId | None = None,
        source: AlertSource | None = None,
    ) -> list[Alert]: ...
    def resolve_alert(self, alert_id: AlertId) -> None: ...
    def acknowledge_alert(self, alert_id: AlertId, acknowledged_by: UUID) -> None: ...
    def fetch_alert_history(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        source: AlertSource | None = None,
    ) -> list[Alert]: ...


@runtime_checkable
class SkillStore(Protocol):
    def store_skill_scores(self, scores: list[SkillScore]) -> None: ...
    def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> None: ...
    def fetch_latest_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        skill_source: SkillSource | None = None,
    ) -> list[SkillScore]: ...
    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
    ) -> list[SkillDiagram]: ...
    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
    ) -> list[SkillScore]: ...
    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> int: ...


@runtime_checkable
class ModelArtifactStore(Protocol):
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
    ) -> ArtifactId: ...
    def fetch_artifact(
        self, artifact_id: ArtifactId
    ) -> tuple[ArtifactId, bytes] | None: ...
    def fetch_active_artifact(
        self,
        model_id: ModelId,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> tuple[ArtifactId, bytes] | None: ...
    def fetch_active_artifact_for_station(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[ArtifactId, bytes] | None: ...
    def fetch_artifact_record(
        self, artifact_id: ArtifactId
    ) -> ModelArtifactRecord | None: ...
    def fetch_artifacts_by_status(
        self,
        model_id: ModelId,
        status: ModelArtifactStatus,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> list[ArtifactId]: ...
    def transition_artifact_status(
        self,
        artifact_id: ArtifactId,
        new_status: ModelArtifactStatus,
        promoted_by: UUID | None = None,
    ) -> None: ...


@runtime_checkable
class ModelStore(Protocol):
    def register_model(self, entry: ModelRegistryEntry) -> None: ...
    def fetch_model(self, model_id: ModelId) -> ModelRegistryEntry | None: ...
    def fetch_all_models(self) -> list[ModelRegistryEntry]: ...


@runtime_checkable
class ModelStateStore(Protocol):
    def store_state(
        self,
        station_id: StationId,
        model_id: ModelId,
        issue_time: UtcDatetime,
        state_bytes: bytes,
    ) -> None: ...
    def fetch_latest_state(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[UtcDatetime, bytes] | None: ...


@runtime_checkable
class StationStore(Protocol):
    def fetch_station(self, station_id: StationId) -> StationConfig | None: ...
    def fetch_station_by_code(self, code: str) -> StationConfig | None: ...
    def fetch_all_stations(
        self, kind: StationKind | None = None
    ) -> list[StationConfig]: ...
    def store_station(self, station: StationConfig) -> StationId: ...
    def fetch_thresholds(self, station_id: StationId) -> list[StationThreshold]: ...
    def store_thresholds(self, thresholds: list[StationThreshold]) -> None: ...
    def fetch_model_assignments(
        self, station_id: StationId
    ) -> list[ModelAssignment]: ...
    def store_model_assignment(self, assignment: ModelAssignment) -> None: ...
    def fetch_weather_sources(
        self, station_id: StationId
    ) -> list[StationWeatherSource]: ...
    def store_weather_source(self, source: StationWeatherSource) -> None: ...


@runtime_checkable
class StationGroupStore(Protocol):
    def store_group(
        self, name: str, station_ids: frozenset[StationId]
    ) -> StationGroupId: ...
    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None: ...
    def fetch_group_by_name(self, name: str) -> StationGroup | None: ...
    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]: ...
    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]: ...
    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None: ...
    def remove_station_from_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None: ...


@runtime_checkable
class PipelineHealthStore(Protocol):
    def append_health_record(self, record: PipelineHealthRecord) -> None: ...
    def fetch_recent(
        self,
        check_type: PipelineCheckType | None = None,
        limit: int = 100,
    ) -> list[PipelineHealthRecord]: ...


@runtime_checkable
class RatingCurveStore(Protocol):
    def store_rating_curve(self, curve: RatingCurve) -> RatingCurveId: ...
    def fetch_active_curve(self, station_id: StationId) -> RatingCurve | None: ...
    def fetch_curve_at(
        self, station_id: StationId, at: UtcDatetime
    ) -> RatingCurve | None: ...
    def supersede_curve(
        self, curve_id: RatingCurveId, valid_to: UtcDatetime
    ) -> None: ...


@runtime_checkable
class FlowRegimeConfigStore(Protocol):
    def store_config(self, config: FlowRegimeConfig) -> None: ...
    def fetch_latest(self, station_id: StationId) -> FlowRegimeConfig | None: ...


@runtime_checkable
class ForecastAdjustmentStore(Protocol):
    def store_adjustment(
        self, adjustment: ForecastAdjustment
    ) -> ForecastAdjustmentId: ...
    def fetch_adjustments(
        self, forecast_id: ForecastId
    ) -> list[ForecastAdjustment]: ...


@runtime_checkable
class BasinStore(Protocol):
    def fetch_basin(self, basin_id: BasinId) -> Basin | None: ...
    def fetch_basin_by_code(self, code: str) -> Basin | None: ...
    def fetch_all_basins(self) -> list[Basin]: ...
    def store_basin(self, basin: Basin) -> BasinId: ...


@runtime_checkable
class ParameterStore(Protocol):
    def fetch_all(self) -> list[ParameterDefinition]: ...
    def fetch_by_name(self, name: str) -> ParameterDefinition | None: ...
