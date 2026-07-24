from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from sapphire_flow.types.enums import ModelArtifactStatus

# Convention: all range queries use half-open intervals [start, end).
# SQL: WHERE timestamp >= start AND timestamp < end
# Fakes must match: start <= x < end (not start <= x <= end).

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from pathlib import Path
    from uuid import UUID

    import polars as pl

    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.auth import AccessToken, AuditEntry
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.calculated_station import ComponentWeight
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
        AlertStatus,
        FlowRegime,
        ForcingType,
        ForecastStatus,
        ObservationSource,
        PipelineCheckType,
        QcStatus,
        SkillSource,
        SpatialRepresentation,
        StationKind,
        StationOwnership,
        StationStatus,
    )
    from sapphire_flow.types.forecast import (
        ForecastAdjustment,
        ForeignForecast,
        HindcastForecast,
        OperationalForecast,
    )
    from sapphire_flow.types.forecast_summary import ForecastSummaryRow
    from sapphire_flow.types.historical_forcing import (
        HistoricalForcingRecord,
        RawHistoricalForcing,
    )
    from sapphire_flow.types.ids import (
        AccessTokenId,
        AlertId,
        ArtifactId,
        BasinId,
        ForecastAdjustmentId,
        ForecastId,
        ForeignForecastId,
        HindcastForecastId,
        ModelId,
        ObservationId,
        PackageId,
        RatingCurveId,
        StationGroupId,
        StationId,
        TenantId,
    )
    from sapphire_flow.types.model import ModelArtifactRecord, ModelRecord
    from sapphire_flow.types.observation import (
        ArchivedObservationValue,
        Observation,
        RawObservation,
    )
    from sapphire_flow.types.pipeline import PipelineHealthRecord
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.skill import FlowRegimeConfig, SkillDiagram, SkillScore
    from sapphire_flow.types.station import (
        GatewayPolygonBindingRow,
        GroupModelAssignment,
        ModelAssignment,
        StationConfig,
        StationGroup,
        StationWeatherSource,
    )
    from sapphire_flow.types.tenant import Tenant
    from sapphire_flow.types.weather import GriddedForecast, WeatherForecastRecord


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
        parameter: str | None = None,
    ) -> OperationalForecast | None:
        raise NotImplementedError

    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,
        parameter: str | None = None,
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
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        raise NotImplementedError

    def fetch_forecast_summaries(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        model_id: ModelId | None = None,
        parameter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ForecastSummaryRow], int]:
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
        parameter: str | None = None,
    ) -> list[HindcastForecast]:
        raise NotImplementedError

    def fetch_hindcasts_by_station(
        self,
        station_id: StationId,
        parameter: str,
        period_start: UtcDatetime,
        period_end: UtcDatetime,
    ) -> dict[ModelId, list[HindcastForecast]]:
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

    def fetch_alert(self, alert_id: AlertId) -> Alert | None:
        raise NotImplementedError

    def fetch_active_alerts(
        self,
        station_id: StationId | None = None,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        raise NotImplementedError

    def fetch_alerts(
        self,
        *,
        station_id: StationId | None = None,
        source: AlertSource | None = None,
        status: AlertStatus | None = None,
        level: str | None = None,
        scope_station_ids: frozenset[StationId] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Alert], int]:
        """`scope_station_ids=None` means unscoped (admin — no filter).
        Otherwise ONLY alerts whose `station_id` is a member of
        `scope_station_ids` match — applied BEFORE `limit`/`offset`/the
        `total` count (Plan 147 Slice C fixer round: consumer-scope
        filtering must happen in the query, not after pagination). An empty
        `scope_station_ids` matches nothing (fail-closed, R2) and a
        stationless (`station_id IS NULL`) alert never matches a non-None
        scope (F7)."""
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
        parameter: str | None = None,
    ) -> list[SkillScore]:
        raise NotImplementedError

    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
        parameter: str | None = None,
    ) -> list[SkillDiagram]:
        raise NotImplementedError

    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
        parameter: str | None = None,
    ) -> list[SkillScore]:
        raise NotImplementedError

    def fetch_skill_scores(
        self,
        model_id: ModelId,
        model_artifact_id: ArtifactId,
        parameter: str | None = None,
    ) -> tuple[SkillScore, ...]:
        raise NotImplementedError

    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        parameter: str | None = None,
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
        status: ModelArtifactStatus = ModelArtifactStatus.TRAINING,
    ) -> tuple[ArtifactId, str]:
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
class TenantStore(Protocol):
    """Plan 147 Slice A: the tenant-model foundation root."""

    def fetch_tenant(self, tenant_id: TenantId) -> Tenant | None:
        raise NotImplementedError

    def fetch_tenant_by_code(self, code: str) -> Tenant | None:
        raise NotImplementedError

    def fetch_all_tenants(self) -> list[Tenant]:
        raise NotImplementedError

    def store_tenant(self, tenant: Tenant) -> TenantId:
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

    def update_station(self, station: StationConfig) -> None:
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

    def fetch_forecast_binding(self, station_id: StationId) -> StationWeatherSource:
        raise NotImplementedError

    def fetch_reanalysis_bindings(
        self, station_id: StationId
    ) -> list[StationWeatherSource]:
        raise NotImplementedError

    def update_station_status(
        self, station_id: StationId, new_status: StationStatus
    ) -> None: ...

    def assign_basin(self, station_id: StationId, basin_id: BasinId) -> None:
        raise NotImplementedError


@runtime_checkable
class StationGroupStore(Protocol):
    def store_group(self, group: StationGroup) -> None:
        raise NotImplementedError

    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None:
        raise NotImplementedError

    def fetch_group_by_name(
        self, tenant_id: TenantId, name: str
    ) -> StationGroup | None:
        # Plan 147 Slice A: name is unique PER TENANT, not globally — a
        # lookup by name alone is no longer a valid key.
        raise NotImplementedError

    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]:
        raise NotImplementedError

    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]:
        # All groups with an active group-model assignment for this model.
        # Used by training scope and operational group forecast discovery.
        raise NotImplementedError

    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        raise NotImplementedError

    def remove_station_from_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        raise NotImplementedError

    def store_group_model_assignment(
        self,
        assignment: GroupModelAssignment,
    ) -> None:
        raise NotImplementedError

    def fetch_group_model_assignments(
        self,
        group_id: StationGroupId,
    ) -> tuple[GroupModelAssignment, ...]:
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
class AuditLogStore(Protocol):
    """Plan 147 Slice B: the append-only audit substrate. ONLY an insert —
    no update/delete method exists on this Protocol (append-only is a type
    contract here, and a DB-level guard, migration 0046, backstops it)."""

    def append_entry(self, entry: AuditEntry) -> None:
        raise NotImplementedError


@runtime_checkable
class AccessTokenStore(Protocol):
    """Plan 147 Slice C: `access_tokens` + `access_token_stations` scope.

    `create_token` validates every scoped station belongs to the token's own
    `tenant_id` (R2's scope-membership rule) and raises `ValueError` on a
    cross-tenant station id — never silently drops it."""

    def create_token(
        self, token: AccessToken, *, station_ids: frozenset[StationId]
    ) -> None:
        raise NotImplementedError

    def fetch_by_key_prefix(self, key_prefix: str) -> AccessToken | None:
        raise NotImplementedError

    def fetch_token(self, token_id: AccessTokenId) -> AccessToken | None:
        raise NotImplementedError

    def fetch_all_tokens(self) -> list[AccessToken]:
        raise NotImplementedError

    def revoke_token(self, token_id: AccessTokenId, *, revoked_at: UtcDatetime) -> None:
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

    def fetch_curves_in_range(
        self, station_id: StationId, start: UtcDatetime, end: UtcDatetime
    ) -> list[RatingCurve]:
        raise NotImplementedError

    def fetch_active_curves_batch(
        self, station_ids: list[StationId]
    ) -> dict[StationId, RatingCurve]:
        raise NotImplementedError

    def fetch_active_curves_batch_at(
        self, station_ids: list[StationId], at: UtcDatetime
    ) -> dict[StationId, RatingCurve]:
        """Curve active for each station at ``at`` (valid_from <= at < valid_to,
        valid_to NULL = unbounded). Stations without a matching curve are absent."""
        raise NotImplementedError


@runtime_checkable
class ObservationVersionStore(Protocol):
    def archive_observation_values(
        self,
        observations: Sequence[Observation],
        superseded_by_curve_id: RatingCurveId,
    ) -> int:
        """Archive rating-curve-derived observations' current values before Flow 12
        Branch A reprocessing. Idempotent per (observation, producing curve).
        Returns the number of rows actually inserted."""
        raise NotImplementedError

    def fetch_archived_values(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        rating_curve_id: RatingCurveId | None = None,
    ) -> Sequence[ArchivedObservationValue]:
        """Archived values in [start, end), optionally filtered by the producing
        curve. Ordered by timestamp."""
        raise NotImplementedError


@runtime_checkable
class FormulaStore(Protocol):
    """Calculated-station weighted-sum formulas (Plan 015).

    Parameter-scoped: a formula is the set of ``ComponentWeight`` rows for one
    ``(calculated_station_id, parameter)`` and validity window.
    """

    def store_formula(self, rows: Sequence[ComponentWeight]) -> None:
        """Insert the component-weight rows of one formula version. All rows
        share the same calculated_station_id + parameter + effective_from."""
        raise NotImplementedError

    def close_formula(
        self,
        calculated_station_id: StationId,
        parameter: str,
        effective_to: UtcDatetime,
    ) -> int:
        """Close the current (effective_to IS NULL) formula rows for a
        station+parameter by setting effective_to. Returns rows closed."""
        raise NotImplementedError

    def fetch_current_formula(
        self, calculated_station_id: StationId, parameter: str
    ) -> Sequence[ComponentWeight]:
        """The current (effective_to IS NULL) rows for a station+parameter."""
        raise NotImplementedError

    def fetch_formula_at(
        self, calculated_station_id: StationId, parameter: str, at: UtcDatetime
    ) -> Sequence[ComponentWeight]:
        """The formula valid at ``at``: per component, the row with the greatest
        effective_from <= at whose validity covers ``at`` (latest-wins)."""
        raise NotImplementedError

    def fetch_formulas_for_stations(
        self, station_ids: list[StationId]
    ) -> dict[tuple[StationId, str], list[ComponentWeight]]:
        """Current formulas for the given calculated stations, grouped by
        (station_id, parameter). One query for the Flow 2 step-2.5 pre-fetch."""
        raise NotImplementedError


@runtime_checkable
class FlowRegimeConfigStore(Protocol):
    def store_config(self, config: FlowRegimeConfig) -> None:
        raise NotImplementedError

    def fetch_latest(
        self, station_id: StationId, parameter: str
    ) -> FlowRegimeConfig | None:
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

    def store_basin(
        self,
        basin: Basin,
        *,
        package_id: PackageId | None = None,
        gateway_mapping: list[dict[str, Any]] | None = None,
    ) -> BasinId:
        """Atomically writes the ``basins`` projection row and its paired
        ``version=1`` ``basin_versions`` row (Plan 120 Task 0A). Called by
        both station onboarding (``package_id=None``) and the basin/static
        package importer (``package_id`` set)."""
        raise NotImplementedError


@runtime_checkable
class GatewayPolygonBindingStore(Protocol):
    """§5a mapping-table persistence (Plan 082 Task 2D). Schema + reader owned
    by 082; rows populated by Plan 120's basin/static package importer."""

    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]:
        raise NotImplementedError

    def store_binding(self, binding: GatewayPolygonBindingRow) -> None:
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

    def fetch_covered_days(
        self,
        station_ids: list[StationId],
        source: str,
        parameter: str,
        spatial_type: SpatialRepresentation,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> dict[StationId, set[date]]:
        # Gap-detection presence check (Plan 115b2 §3C): for each station in
        # ``station_ids``, the set of calendar days (UTC, from ``valid_time``)
        # already stored for (source, parameter, spatial_type) within the
        # half-open [start, end) window — keyed on the LOGICAL key, i.e.
        # regardless of ``version``. Every station in ``station_ids`` is
        # present in the result (with an empty set if it has no rows).
        raise NotImplementedError

    def fetch_latest_valid_time(
        self,
        station_ids: list[StationId],
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> UtcDatetime | None:
        # Health-by-effect (Plan 115b4 §6B): the single latest ``valid_time``
        # stored for ``source`` across ALL of ``station_ids`` within the
        # half-open [start, end) window — an O(1) aggregate query, NOT an
        # O(stations) loop over ``fetch_forcing``. ``None`` when nothing is
        # stored for this source/window. Comparing this before vs after an
        # ingest run detects a run with zero EFFECT even when the run
        # "successfully" re-persisted already-covered rows (``rows_stored``
        # would look healthy; this would not).
        raise NotImplementedError


@runtime_checkable
class ClimBaselineStore(Protocol):
    def store_baselines(self, baselines: list[ClimBaseline]) -> None:
        # Upsert keyed on (station_id, parameter, day_of_year)
        raise NotImplementedError

    def delete_baselines(self, station_id: StationId, parameter: str) -> None:
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
        skipped_rule_ids: frozenset[str] = frozenset(),
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
        skipped_rule_ids: frozenset[str] = frozenset(),
    ) -> list[QcFlag]: ...


@runtime_checkable
class NwpGridStore(Protocol):
    def archive(self, forecast: GriddedForecast, base_path: Path) -> Path:
        raise NotImplementedError

    def load(
        self, base_path: Path, nwp_source: str, cycle_time: UtcDatetime
    ) -> GriddedForecast:
        raise NotImplementedError
