from __future__ import annotations

from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid4

import polars as pl

from sapphire_flow.exceptions import ConflictError
from sapphire_flow.types.alert import Alert  # noqa: TC001
from sapphire_flow.types.basin import Basin  # noqa: TC001
from sapphire_flow.types.datetime import UtcDatetime  # noqa: TC001
from sapphire_flow.types.domain import (  # noqa: TC001
    ClimBaseline,
    ParameterDefinition,
    QcFlag,
    StationThreshold,
)
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    FlowRegime,
    ForcingType,
    ForecastStatus,
    ModelArtifactStatus,
    ObservationSource,
    PipelineCheckType,
    QcStatus,
    SkillFreshness,
    SkillSource,
    StationKind,
    StationOwnership,
)
from sapphire_flow.types.forecast import (  # noqa: TC001
    ForecastAdjustment,
    ForeignForecast,
    HindcastForecast,
    OperationalForecast,
)
from sapphire_flow.types.historical_forcing import (
    HistoricalForcingRecord,  # noqa: TC001
    RawHistoricalForcing,  # noqa: TC001
)
from sapphire_flow.types.ids import (
    AlertId,
    ArtifactId,
    BasinId,
    ForecastAdjustmentId,
    ForecastId,
    ForeignForecastId,
    HindcastForecastId,
    HistoricalForcingId,
    ModelId,
    ObservationId,
    RatingCurveId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.model import (  # noqa: TC001
    ModelArtifactRecord,
    ModelRecord,
)
from sapphire_flow.types.observation import Observation, RawObservation  # noqa: TC001
from sapphire_flow.types.pipeline import PipelineHealthRecord  # noqa: TC001
from sapphire_flow.types.rating_curve import RatingCurve  # noqa: TC001
from sapphire_flow.types.skill import (  # noqa: TC001
    FlowRegimeConfig,
    SkillDiagram,
    SkillScore,
)
from sapphire_flow.types.station import (  # noqa: TC001
    ModelAssignment,
    StationConfig,
    StationGroup,
    StationWeatherSource,
)
from sapphire_flow.types.weather import WeatherForecastRecord  # noqa: TC001


class FakeObservationStore:
    def __init__(self) -> None:
        self._observations: dict[ObservationId, Observation] = {}

    def store_observations(self, observations: list[Observation]) -> None:
        for obs in observations:
            self._observations[obs.id] = obs

    def store_raw_observations(
        self, observations: list[RawObservation]
    ) -> list[ObservationId]:
        ids = []
        for raw in observations:
            oid = ObservationId(uuid4())
            obs = Observation(
                id=oid,
                station_id=raw.station_id,
                timestamp=raw.timestamp,
                parameter=raw.parameter,
                value=raw.value,
                source=raw.source,
                rating_curve_id=raw.rating_curve_id,
                rating_curve_correction_version=raw.rating_curve_correction_version,
                qc_status=QcStatus.RAW,
                qc_flags=[],
                qc_rule_version=None,
                created_at=raw.timestamp,
            )
            self._observations[oid] = obs
            ids.append(oid)
        return ids

    def update_qc(
        self,
        observation_id: ObservationId,
        qc_status: QcStatus,
        qc_flags: list[QcFlag],
        qc_rule_version: str | None = None,
    ) -> None:
        obs = self._observations[observation_id]
        self._observations[observation_id] = replace(
            obs, qc_status=qc_status, qc_flags=qc_flags, qc_rule_version=qc_rule_version
        )

    def fetch_observations(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> list[Observation]:
        return [
            o
            for o in self._observations.values()
            if o.station_id == station_id
            and o.parameter == parameter
            and start <= o.timestamp < end
            and (qc_status is None or o.qc_status == qc_status)
            and (source is None or o.source == source)
        ]

    def fetch_latest_timestamp(
        self, station_id: StationId, parameter: str
    ) -> UtcDatetime | None:
        timestamps = [
            o.timestamp
            for o in self._observations.values()
            if o.station_id == station_id and o.parameter == parameter
        ]
        return max(timestamps) if timestamps else None

    def fetch_observations_batch(
        self,
        station_ids: list[StationId],
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> dict[StationId, list[Observation]]:
        return {
            sid: self.fetch_observations(sid, parameter, start, end, qc_status, source)
            for sid in station_ids
        }

    def fetch_derived_observations_by_curve(
        self,
        station_id: StationId,
        rating_curve_id: RatingCurveId,
    ) -> list[Observation]:
        return [
            o
            for o in self._observations.values()
            if o.station_id == station_id and o.rating_curve_id == rating_curve_id
        ]


class FakeForecastStore:
    def __init__(self) -> None:
        self._forecasts: dict[ForecastId, OperationalForecast] = {}

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        self._forecasts[forecast.id] = forecast
        return forecast.id

    def fetch_forecast(self, forecast_id: ForecastId) -> OperationalForecast | None:
        return self._forecasts.get(forecast_id)

    def fetch_latest_forecast(
        self,
        station_id: StationId,
        model_id: ModelId | None = None,
        parameter: str | None = None,
    ) -> OperationalForecast | None:
        matches = [
            f
            for f in self._forecasts.values()
            if f.station_id == station_id
            and (model_id is None or f.model_id == model_id)
            and (parameter is None or f.ensemble.parameter == parameter)
        ]
        return max(matches, key=lambda f: f.issued_at) if matches else None

    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        return [
            f
            for f in self._forecasts.values()
            if f.issued_at == issued_at
            and (station_id is None or f.station_id == station_id)
            and (parameter is None or f.ensemble.parameter == parameter)
        ]

    def transition_status(
        self,
        forecast_id: ForecastId,
        expected_version: int,
        new_status: ForecastStatus,
    ) -> int:
        f = self._forecasts.get(forecast_id)
        if f is None:
            raise ConflictError(f"Forecast {forecast_id} not found")
        if f.version != expected_version:
            raise ConflictError(
                f"Version mismatch: expected {expected_version}, got {f.version}"
            )
        new_version = f.version + 1
        self._forecasts[forecast_id] = replace(
            f, status=new_status, version=new_version
        )
        return new_version

    def fetch_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        status: ForecastStatus | None = None,
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        return [
            f
            for f in self._forecasts.values()
            if f.station_id == station_id
            and start <= f.issued_at < end
            and (model_id is None or f.model_id == model_id)
            and (status is None or f.status == status)
            and (parameter is None or f.ensemble.parameter == parameter)
        ]


class FakeHindcastStore:
    def __init__(self) -> None:
        self._hindcasts: dict[HindcastForecastId, HindcastForecast] = {}

    def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
        self._hindcasts[hindcast.id] = hindcast
        return hindcast.id

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
        return [
            h
            for h in self._hindcasts.values()
            if h.station_id == station_id
            and h.model_id == model_id
            and start <= h.hindcast_step < end
            and (forcing_type is None or h.forcing_type == forcing_type)
            and (hindcast_run_id is None or h.hindcast_run_id == hindcast_run_id)
            and (parameter is None or h.ensemble.parameter == parameter)
        ]


class FakeWeatherForecastStore:
    def __init__(self) -> None:
        self._records: list[WeatherForecastRecord] = []

    def store_weather_forecasts(self, records: list[WeatherForecastRecord]) -> None:
        self._records.extend(records)

    def fetch_weather_forecasts(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        parameters: list[str] | None = None,
    ) -> list[WeatherForecastRecord]:
        return [
            r
            for r in self._records
            if r.station_id == station_id
            and r.nwp_source == nwp_source
            and r.cycle_time == cycle_time
            and (parameters is None or r.parameter in parameters)
        ]

    def fetch_lookback(
        self,
        station_id: StationId,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[WeatherForecastRecord]:
        return [
            r
            for r in self._records
            if r.station_id == station_id
            and r.nwp_source == nwp_source
            and start <= r.valid_time < end
        ]

    def fetch_received_cycles(
        self,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[UtcDatetime]:
        cycles = {
            r.cycle_time
            for r in self._records
            if r.nwp_source == nwp_source and start <= r.cycle_time < end
        }
        return sorted(cycles)

    def mark_gap(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        recoverable: bool,
    ) -> None:
        pass  # v0: no gap tracking

    def fetch_latest_cycle_time(self, nwp_source: str) -> UtcDatetime | None:
        cycles = [r.cycle_time for r in self._records if r.nwp_source == nwp_source]
        return max(cycles) if cycles else None


class FakeAlertStore:
    def __init__(self) -> None:
        self._alerts: dict[AlertId, Alert] = {}

    def upsert_alert(self, alert: Alert) -> AlertId:
        if alert.status != AlertStatus.RESOLVED:
            for existing in self._alerts.values():
                if (
                    existing.station_id == alert.station_id
                    and existing.alert_level == alert.alert_level
                    and existing.source == alert.source
                    and existing.status != AlertStatus.RESOLVED
                    and existing.id != alert.id
                ):
                    self._alerts[existing.id] = replace(alert, id=existing.id)
                    return existing.id
        self._alerts[alert.id] = alert
        return alert.id

    def fetch_active_alerts(
        self,
        station_id: StationId | None = None,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        return [
            a
            for a in self._alerts.values()
            if a.status != AlertStatus.RESOLVED
            and (station_id is None or a.station_id == station_id)
            and (source is None or a.source == source)
        ]

    def resolve_alert(self, alert_id: AlertId) -> None:
        a = self._alerts[alert_id]
        self._alerts[alert_id] = replace(a, status=AlertStatus.RESOLVED)

    def acknowledge_alert(self, alert_id: AlertId, acknowledged_by: UUID) -> None:
        a = self._alerts[alert_id]
        self._alerts[alert_id] = replace(
            a, status=AlertStatus.ACKNOWLEDGED, acknowledged_by=acknowledged_by
        )

    def fetch_alert_history(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        return [
            a
            for a in self._alerts.values()
            if a.station_id == station_id
            and start <= a.triggered_at < end
            and (source is None or a.source == source)
        ]


class FakeSkillStore:
    def __init__(self) -> None:
        self._scores: list[SkillScore] = []
        self._diagrams: list[SkillDiagram] = []

    def store_skill_scores(self, scores: list[SkillScore]) -> None:
        self._scores.extend(scores)

    def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> None:
        self._diagrams.extend(diagrams)

    def fetch_latest_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        skill_source: SkillSource | None = None,
        parameter: str | None = None,
    ) -> list[SkillScore]:
        matches = [
            s
            for s in self._scores
            if s.station_id == station_id
            and s.model_id == model_id
            and (skill_source is None or s.skill_source == skill_source)
            and (parameter is None or s.parameter == parameter)
        ]
        if not matches:
            return []
        max_ver = max(s.computation_version for s in matches)
        return [s for s in matches if s.computation_version == max_ver]

    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
        parameter: str | None = None,
    ) -> list[SkillDiagram]:
        matches = [
            d
            for d in self._diagrams
            if d.station_id == station_id
            and d.model_id == model_id
            and (diagram_type is None or d.diagram_type == diagram_type)
            and (parameter is None or d.parameter == parameter)
        ]
        if not matches:
            return []
        max_ver = max(d.computation_version for d in matches)
        return [d for d in matches if d.computation_version == max_ver]

    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
        parameter: str | None = None,
    ) -> list[SkillScore]:
        return [
            s
            for s in self._scores
            if s.station_id == station_id
            and s.model_id == model_id
            and s.flow_regime == flow_regime
            and (parameter is None or s.parameter == parameter)
        ]

    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        parameter: str | None = None,
    ) -> int:
        count = 0
        new_scores = []
        for s in self._scores:
            overlaps = s.eval_period_start < end and s.eval_period_end > start
            is_current = s.freshness == SkillFreshness.CURRENT
            param_match = parameter is None or s.parameter == parameter
            if s.station_id == station_id and is_current and overlaps and param_match:
                new_scores.append(replace(s, freshness=SkillFreshness.STALE))
                count += 1
            else:
                new_scores.append(s)
        self._scores = new_scores
        return count


class FakeModelArtifactStore:
    def __init__(self, group_store: FakeStationGroupStore | None = None) -> None:
        self._records: dict[ArtifactId, ModelArtifactRecord] = {}
        self._bytes: dict[ArtifactId, bytes] = {}
        self._group_store = group_store

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
        aid = ArtifactId(uuid4())
        record = ModelArtifactRecord(
            id=aid,
            model_id=model_id,
            station_id=station_id,
            group_id=group_id,
            status=ModelArtifactStatus.TRAINING,
            artifact_path=f"artifacts/{aid}.bin",
            training_period_start=training_period_start,
            training_period_end=training_period_end,
            trained_at=trained_at,
            promoted_at=None,
            promoted_by=None,
            superseded_at=None,
            created_at=trained_at,
        )
        self._records[aid] = record
        self._bytes[aid] = artifact_bytes
        return aid

    def fetch_artifact(
        self, artifact_id: ArtifactId
    ) -> tuple[ArtifactId, bytes] | None:
        if artifact_id in self._bytes:
            return (artifact_id, self._bytes[artifact_id])
        return None

    def fetch_active_artifact(
        self,
        model_id: ModelId,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> tuple[ArtifactId, bytes] | None:
        for aid, rec in self._records.items():
            if rec.model_id == model_id and rec.status == ModelArtifactStatus.ACTIVE:
                if station_id is not None and rec.station_id == station_id:
                    return (aid, self._bytes[aid])
                if group_id is not None and rec.group_id == group_id:
                    return (aid, self._bytes[aid])
        return None

    def fetch_active_artifact_for_station(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[ArtifactId, bytes] | None:
        result = self.fetch_active_artifact(model_id, station_id=station_id)
        if result is not None:
            return result
        if self._group_store is not None:
            for group in self._group_store.fetch_groups_for_station(station_id):
                result = self.fetch_active_artifact(model_id, group_id=group.id)
                if result is not None:
                    return result
        return None

    def fetch_artifact_record(
        self, artifact_id: ArtifactId
    ) -> ModelArtifactRecord | None:
        return self._records.get(artifact_id)

    def fetch_artifacts_by_status(
        self,
        model_id: ModelId,
        status: ModelArtifactStatus,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> list[ArtifactId]:
        return [
            aid
            for aid, rec in self._records.items()
            if rec.model_id == model_id
            and rec.status == status
            and (station_id is None or rec.station_id == station_id)
            and (group_id is None or rec.group_id == group_id)
        ]

    def transition_artifact_status(
        self,
        artifact_id: ArtifactId,
        new_status: ModelArtifactStatus,
        promoted_by: UUID | None = None,
    ) -> None:
        rec = self._records[artifact_id]
        now = rec.trained_at  # use trained_at as clock proxy in fakes
        if new_status == ModelArtifactStatus.ACTIVE:
            self._records[artifact_id] = replace(
                rec, status=new_status, promoted_at=now, promoted_by=promoted_by
            )
        elif new_status == ModelArtifactStatus.SUPERSEDED:
            self._records[artifact_id] = replace(
                rec, status=new_status, superseded_at=now
            )
        else:
            self._records[artifact_id] = replace(rec, status=new_status)


class FakeModelStore:
    def __init__(self) -> None:
        self._models: dict[ModelId, ModelRecord] = {}

    def register_model(self, record: ModelRecord) -> None:
        self._models[record.id] = record

    def fetch_model(self, model_id: ModelId) -> ModelRecord | None:
        return self._models.get(model_id)

    def fetch_all_models(self) -> list[ModelRecord]:
        return list(self._models.values())


class FakeModelStateStore:
    def __init__(self) -> None:
        self._states: dict[tuple[StationId, ModelId], tuple[UtcDatetime, bytes]] = {}

    def store_state(
        self,
        station_id: StationId,
        model_id: ModelId,
        issue_time: UtcDatetime,
        state_bytes: bytes,
    ) -> None:
        self._states[(station_id, model_id)] = (issue_time, state_bytes)

    def fetch_latest_state(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[UtcDatetime, bytes] | None:
        return self._states.get((station_id, model_id))


class FakeStationStore:
    def __init__(self) -> None:
        self._stations: dict[StationId, StationConfig] = {}
        self._thresholds: list[StationThreshold] = []
        self._assignments: list[ModelAssignment] = []
        self._weather_sources: list[StationWeatherSource] = []

    def fetch_station(self, station_id: StationId) -> StationConfig | None:
        return self._stations.get(station_id)

    def fetch_station_by_code(self, code: str, network: str) -> StationConfig | None:
        return next(
            (
                s
                for s in self._stations.values()
                if s.code == code and s.network == network
            ),
            None,
        )

    def fetch_all_stations(
        self, kind: StationKind | None = None
    ) -> list[StationConfig]:
        return [
            s for s in self._stations.values() if kind is None or s.station_kind == kind
        ]

    def fetch_stations_by_ownership(
        self,
        ownership: StationOwnership,
        kind: StationKind | None = None,
    ) -> list[StationConfig]:
        return [
            s
            for s in self._stations.values()
            if s.ownership == ownership and (kind is None or s.station_kind == kind)
        ]

    def store_station(self, station: StationConfig) -> StationId:
        self._stations[station.id] = station
        return station.id

    def fetch_thresholds(self, station_id: StationId) -> list[StationThreshold]:
        return [t for t in self._thresholds if t.station_id == station_id]

    def store_thresholds(self, thresholds: list[StationThreshold]) -> None:
        for t in thresholds:
            self._thresholds = [
                x
                for x in self._thresholds
                if not (
                    x.station_id == t.station_id
                    and x.danger_level == t.danger_level
                    and x.parameter == t.parameter
                )
            ]
            self._thresholds.append(t)

    def fetch_model_assignments(self, station_id: StationId) -> list[ModelAssignment]:
        return [a for a in self._assignments if a.station_id == station_id]

    def store_model_assignment(self, assignment: ModelAssignment) -> None:
        self._assignments = [
            a
            for a in self._assignments
            if not (
                a.station_id == assignment.station_id
                and a.model_id == assignment.model_id
            )
        ]
        self._assignments.append(assignment)

    def fetch_weather_sources(
        self, station_id: StationId
    ) -> list[StationWeatherSource]:
        return [s for s in self._weather_sources if s.station_id == station_id]

    def store_weather_source(self, source: StationWeatherSource) -> None:
        self._weather_sources = [
            s
            for s in self._weather_sources
            if not (
                s.station_id == source.station_id and s.nwp_source == source.nwp_source
            )
        ]
        self._weather_sources.append(source)


class FakeStationGroupStore:
    def __init__(self) -> None:
        self._groups: dict[StationGroupId, StationGroup] = {}
        self._group_model_assignments: dict[ModelId, set[StationGroupId]] = {}

    def seed_group_model_assignment(
        self, group_id: StationGroupId, model_id: ModelId
    ) -> None:
        self._group_model_assignments.setdefault(model_id, set()).add(group_id)

    def store_group(self, group: StationGroup) -> None:
        self._groups[group.id] = group

    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None:
        return self._groups.get(group_id)

    def fetch_group_by_name(self, name: str) -> StationGroup | None:
        return next((g for g in self._groups.values() if g.name == name), None)

    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]:
        return [g for g in self._groups.values() if station_id in g.station_ids]

    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]:
        assigned_ids = self._group_model_assignments.get(model_id, set())
        return [g for g in self._groups.values() if g.id in assigned_ids]

    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        g = self._groups[group_id]
        self._groups[group_id] = replace(g, station_ids=g.station_ids | {station_id})

    def remove_station_from_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        g = self._groups[group_id]
        self._groups[group_id] = replace(g, station_ids=g.station_ids - {station_id})


class FakePipelineHealthStore:
    def __init__(self) -> None:
        self._records: list[PipelineHealthRecord] = []

    def append_health_record(self, record: PipelineHealthRecord) -> None:
        self._records.append(record)

    def fetch_recent(
        self,
        check_type: PipelineCheckType | None = None,
        limit: int = 100,
    ) -> list[PipelineHealthRecord]:
        matches = [
            r for r in self._records if check_type is None or r.check_type == check_type
        ]
        return matches[-limit:]


class FakeRatingCurveStore:
    def __init__(self) -> None:
        self._curves: dict[RatingCurveId, RatingCurve] = {}

    def store_rating_curve(self, curve: RatingCurve) -> RatingCurveId:
        self._curves[curve.id] = curve
        return curve.id

    def fetch_active_curve(self, station_id: StationId) -> RatingCurve | None:
        return next(
            (
                c
                for c in self._curves.values()
                if c.station_id == station_id and c.valid_to is None
            ),
            None,
        )

    def fetch_curve_at(
        self, station_id: StationId, at: UtcDatetime
    ) -> RatingCurve | None:
        for c in self._curves.values():
            if (
                c.station_id == station_id
                and c.valid_from <= at
                and (c.valid_to is None or at < c.valid_to)
            ):
                return c
        return None

    def supersede_curve(self, curve_id: RatingCurveId, valid_to: UtcDatetime) -> None:
        c = self._curves[curve_id]
        self._curves[curve_id] = replace(c, valid_to=valid_to)


class FakeFlowRegimeConfigStore:
    def __init__(self) -> None:
        self._configs: dict[StationId, list[FlowRegimeConfig]] = {}

    def store_config(self, config: FlowRegimeConfig) -> None:
        self._configs.setdefault(config.station_id, []).append(config)

    def fetch_latest(
        self, station_id: StationId, parameter: str
    ) -> FlowRegimeConfig | None:
        configs = [
            c for c in self._configs.get(station_id, []) if c.parameter == parameter
        ]
        return max(configs, key=lambda c: c.version) if configs else None


class FakeForecastAdjustmentStore:
    def __init__(self) -> None:
        self._adjustments: list[ForecastAdjustment] = []

    def store_adjustment(self, adjustment: ForecastAdjustment) -> ForecastAdjustmentId:
        self._adjustments.append(adjustment)
        return adjustment.id

    def fetch_adjustments(self, forecast_id: ForecastId) -> list[ForecastAdjustment]:
        return sorted(
            [a for a in self._adjustments if a.forecast_id == forecast_id],
            key=lambda a: a.adjusted_at,
        )


class FakeBasinStore:
    def __init__(self) -> None:
        self._basins: dict[BasinId, Basin] = {}

    def fetch_basin(self, basin_id: BasinId) -> Basin | None:
        return self._basins.get(basin_id)

    def fetch_basin_by_code(self, code: str, network: str) -> Basin | None:
        return next(
            (
                b
                for b in self._basins.values()
                if b.code == code and b.network == network
            ),
            None,
        )

    def fetch_all_basins(self) -> list[Basin]:
        return list(self._basins.values())

    def store_basin(self, basin: Basin) -> BasinId:
        self._basins[basin.id] = basin
        return basin.id


class FakeParameterStore:
    def __init__(self) -> None:
        self._params: dict[str, ParameterDefinition] = {}

    def fetch_all(self) -> list[ParameterDefinition]:
        return list(self._params.values())

    def fetch_by_name(self, name: str) -> ParameterDefinition | None:
        return self._params.get(name)

    def seed(self, params: list[ParameterDefinition]) -> None:
        for p in params:
            self._params[p.name] = p


class FakeForeignForecastStore:
    def __init__(self) -> None:
        self._forecasts: dict[ForeignForecastId, ForeignForecast] = {}

    def store_foreign_forecast(self, forecast: ForeignForecast) -> ForeignForecastId:
        self._forecasts[forecast.id] = forecast
        return forecast.id

    def fetch_foreign_forecast(
        self, forecast_id: ForeignForecastId
    ) -> ForeignForecast | None:
        return self._forecasts.get(forecast_id)

    def fetch_latest_foreign_forecast(
        self, station_id: StationId
    ) -> ForeignForecast | None:
        matches = [f for f in self._forecasts.values() if f.station_id == station_id]
        return max(matches, key=lambda f: f.issued_at) if matches else None

    def fetch_foreign_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[ForeignForecast]:
        return [
            f
            for f in self._forecasts.values()
            if f.station_id == station_id and start <= f.issued_at < end
        ]


class FakeHistoricalForcingStore:
    def __init__(self) -> None:
        self._records: list[HistoricalForcingRecord] = []

    def store_forcing(self, records: list[RawHistoricalForcing]) -> None:
        for raw in records:
            fid = HistoricalForcingId(uuid4())
            record = HistoricalForcingRecord(
                id=fid,
                station_id=raw.station_id,
                source=raw.source,
                version=raw.version,
                valid_time=raw.valid_time,
                parameter=raw.parameter,
                spatial_type=raw.spatial_type,
                band_id=raw.band_id,
                member_id=raw.member_id,
                value=raw.value,
                created_at=raw.valid_time,
            )
            self._records.append(record)

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
        return [
            r
            for r in self._records
            if r.station_id == station_id
            and r.source == source
            and start <= r.valid_time < end
            and (parameters is None or r.parameter in parameters)
            and (version is None or r.version == version)
            and (member_id is None or r.member_id == member_id)
        ]

    def fetch_forcing_as_dataframe(
        self,
        station_id: StationId,
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str] | None = None,
        version: str | None = None,
    ) -> pl.DataFrame | None:
        records = self.fetch_forcing(
            station_id, source, start, end, parameters, version
        )
        if not records:
            return None
        rows = [
            {"valid_time": r.valid_time, "parameter": r.parameter, "value": r.value}
            for r in records
        ]
        df = pl.DataFrame(rows)
        return df.pivot(on="parameter", index="valid_time", values="value")

    def fetch_available_sources(self, station_id: StationId) -> list[str]:
        return sorted({r.source for r in self._records if r.station_id == station_id})


class FakeClimBaselineStore:
    def __init__(self) -> None:
        self._baselines: dict[tuple[StationId, str, int], ClimBaseline] = {}

    def store_baselines(self, baselines: list[ClimBaseline]) -> None:
        for b in baselines:
            self._baselines[(b.station_id, b.parameter, b.day_of_year)] = b

    def fetch_baselines(
        self, station_id: StationId, parameter: str
    ) -> list[ClimBaseline]:
        return sorted(
            [
                b
                for (sid, param, _), b in self._baselines.items()
                if sid == station_id and param == parameter
            ],
            key=lambda b: b.day_of_year,
        )

    def fetch_baseline(
        self, station_id: StationId, parameter: str, day_of_year: int
    ) -> ClimBaseline | None:
        return self._baselines.get((station_id, parameter, day_of_year))
