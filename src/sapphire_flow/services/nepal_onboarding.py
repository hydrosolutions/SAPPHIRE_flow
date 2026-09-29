from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.basin_importer import import_loaded_basin_package
from sapphire_flow.services.qc_datum import obs_qc_rule_version
from sapphire_flow.services.training_data import assemble_station_training_data
from sapphire_flow.services.write_principal import (
    enforce_tenant_isolation,
    resolve_run_principal,
)
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import (
    AuditEventType,
    ModelAssignmentStatus,
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.nepal_onboarding import (
    GATEWAY_HISTORY_SOURCE,
    GATEWAY_HRU,
    GAUGE_POLYGONS,
    HISTORY_PARAMETERS,
    ReadinessStatus,
    StationReadiness,
)
from sapphire_flow.types.station import StationWeatherSource

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import timedelta

    import sqlalchemy as sa

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.types.basin_package import (
        BasinPackageImportReport,
        LoadedBasinPackage,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.model import StationTrainingData
    from sapphire_flow.types.nepal_onboarding import TrainingWindow
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.station import StationConfig


def chwrr_stations(
    conn: sa.Connection, identity: DeploymentIdentityConfig, *, now: UtcDatetime
) -> list[StationConfig]:
    if identity.global_admin or identity.writable_tenants != frozenset({"chwrr"}):
        raise ConfigurationError("Nepal onboarding requires CHWRR-only write authority")
    tenants = PgTenantStore(conn)
    principal = resolve_run_principal(tenants, identity, tenant_code="chwrr")
    if principal.tenant_id is None:
        raise ConfigurationError("CHWRR principal must be tenant scoped")
    tenants.lock_tenant(principal.tenant_id)
    store = PgStationStore(conn)
    groups = PgStationGroupStore(conn)
    stations: list[StationConfig] = []
    for code, _ in GAUGE_POLYGONS:
        station = store.fetch_station_by_code(code, "dhm")
        if station is None:
            raise ConfigurationError(
                f"missing DHM station {code}; complete Plan 268 T8"
            )
        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=station.tenant_id,
            audit_log_store=None,
            event_type=AuditEventType.STATION_ONBOARDED,
            target_type="station",
            target_id=str(station.id),
            detail=None,
            now=now,
        )
        if station.station_status is not StationStatus.ONBOARDING:
            raise ConfigurationError(f"station {code} must remain onboarding")
        if station.forecast_targets not in (None, frozenset({"discharge"})):
            raise ConfigurationError(f"unexpected forecast target for {code}")
        assignments = store.fetch_model_assignments(station.id)
        group_assignments = [
            assignment
            for group in groups.fetch_groups_for_station(station.id)
            for assignment in groups.fetch_group_model_assignments(group.id)
        ]
        if any(
            a.status is ModelAssignmentStatus.ACTIVE
            for a in [*assignments, *group_assignments]
        ):
            raise ConfigurationError(
                f"station {code} already has an active model assignment"
            )
        stations.append(station)
    return stations


def history_binding(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="era5_land",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def require_history_bindings(
    conn: sa.Connection, stations: list[StationConfig]
) -> list[StationWeatherSource]:
    for station in stations:
        if reason := history_binding_issue(conn, station):
            raise ConfigurationError(f"{reason}: {station.code}")
    return [history_binding(s.id) for s in stations]


def history_binding_issue(conn: sa.Connection, station: StationConfig) -> str | None:
    store = PgStationStore(conn)
    polygons = RecapGatewayPolygonStore(conn)
    if history_binding(station.id) not in store.fetch_weather_sources(station.id):
        return "missing_active_reanalysis_binding"
    matches = [
        r
        for r in polygons.fetch_bindings_for_station(station.id)
        if r.spatial_type is SpatialRepresentation.BASIN_AVERAGE
    ]
    if len(matches) != 1 or (
        matches[0].basin_id != station.basin_id
        or matches[0].gateway_hru_name != GATEWAY_HRU
        or matches[0].name != dict(GAUGE_POLYGONS)[station.code]
    ):
        return "missing_or_conflicting_gateway_mapping"
    return None


def validate_nepal_package(loaded: LoadedBasinPackage) -> None:
    expected = dict(GAUGE_POLYGONS)
    if (
        loaded.manifest.network != "dhm"
        or loaded.manifest.gateway_hru_names != frozenset({GATEWAY_HRU})
    ):
        raise ConfigurationError(
            "package must name DHM network and registered Nepal HRU"
        )
    if len(loaded.basins) != 6 or {b.station_code for b in loaded.basins} != set(
        expected
    ):
        raise ConfigurationError("package must contain exactly the six DHM gauges")
    if any(
        b.network != "dhm"
        or b.gateway_hru_name != GATEWAY_HRU
        or b.name != expected[b.station_code]
        for b in loaded.basins
    ):
        raise ConfigurationError("package has an unexpected gauge/polygon mapping")


def import_nepal_package(
    conn: sa.Connection,
    loaded: LoadedBasinPackage,
    identity: DeploymentIdentityConfig,
    *,
    clock: Callable[[], UtcDatetime],
) -> BasinPackageImportReport:
    validate_nepal_package(loaded)
    stations = chwrr_stations(conn, identity, now=clock())
    by_code = {s.code: s for s in stations}
    store = PgStationStore(conn)
    polygons = RecapGatewayPolygonStore(conn)
    for station in stations:
        bindings = [
            b
            for b in store.fetch_weather_sources(station.id)
            if b.nwp_source == "era5_land"
        ]
        if bindings and bindings != [history_binding(station.id)]:
            raise ConfigurationError(
                f"conflicting reanalysis binding for {station.code}"
            )
        for row in polygons.fetch_bindings_for_station(station.id):
            if row.spatial_type is SpatialRepresentation.BASIN_AVERAGE and (
                row.gateway_hru_name != GATEWAY_HRU
                or row.name != dict(GAUGE_POLYGONS)[station.code]
            ):
                raise ConfigurationError(
                    f"conflicting polygon binding for {station.code}"
                )

    def resolve(code: str, network: str) -> StationId | None:
        station = by_code.get(code) if network == "dhm" else None
        return station.id if station else None

    with conn.begin_nested():
        report = import_loaded_basin_package(
            conn,
            loaded,
            resolve_station=resolve,
            assigned_model_features=lambda _: frozenset(),
            clock=clock,
        )
        if (
            report.outcome == "rejected"
            or report.onboarding_held
            or len(report.accepted) != 6
        ):
            raise ConfigurationError(
                "Nepal package rejected or held; whole batch rolled back"
            )
        for station in stations:
            store.store_weather_source(history_binding(station.id))
        require_history_bindings(conn, chwrr_stations(conn, identity, now=clock()))
    return report


class DeliveryObservationReader(PgObservationStore):
    def fetch_observations(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | Collection[QcStatus] | None = None,
        source: ObservationSource | None = None,
    ) -> list[Observation]:
        rows = super().fetch_observations(
            station_id, parameter, start, end, qc_status, source
        )
        return [
            r
            for r in rows
            if r.delivery_id == DELIVERY_ID
            and r.source is ObservationSource.MANUAL_IMPORT
            and r.parameter == "discharge"
            and r.qc_rule_version == obs_qc_rule_version("discharge", None)
        ]


class GatewayHistoryReader(StoreBackedReanalysisSource):
    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        bindings = [c for c in station_configs if c == history_binding(c.station_id)]
        return [
            r
            for r in super().fetch_reanalysis(bindings, start, end, parameters)
            if r.spatial_type is SpatialRepresentation.BASIN_AVERAGE
            and r.band_id is None
            and r.member_id is None
        ]


def count_training_samples(
    frame: pl.DataFrame, step: timedelta, lookback: int, horizon: int
) -> int:
    run = 0
    total = 0
    previous = None
    for stamp in frame["timestamp"].sort().to_list():
        run = run + 1 if previous is not None and stamp - previous == step else 1
        total += int(run >= lookback + horizon)
        previous = stamp
    return total


def complete_training_rows(data: StationTrainingData) -> pl.DataFrame:
    result = data.past_targets
    for frame in (data.past_dynamic, data.future_dynamic):
        if len(frame.columns) > 1:
            result = result.join(frame, on="timestamp", how="inner", suffix="_future")
    columns = [c for c in result.columns if c != "timestamp"]
    return result.filter(
        pl.all_horizontal(
            [pl.col(c).is_not_null() & pl.col(c).is_finite() for c in columns]
        )
    ).sort("timestamp")


def qualify_discharge_targets(
    conn: sa.Connection,
    identity: DeploymentIdentityConfig,
    model: ForecastModel,
    window: TrainingWindow,
    *,
    now: UtcDatetime,
) -> tuple[StationReadiness, ...]:
    requirements = model.data_requirements
    if requirements.target_parameters != frozenset({"discharge"}):
        raise ConfigurationError("select a discharge-only model")
    if window.time_step not in requirements.supported_time_steps:
        raise ConfigurationError("selected time step is not supported by the model")
    stations = chwrr_stations(conn, identity, now=now)
    store = PgStationStore(conn)
    obs_store = DeliveryObservationReader(conn)
    forcing = GatewayHistoryReader(
        PgHistoricalForcingStore(conn),
        source_mapping={"era5_land": GATEWAY_HISTORY_SOURCE},
    )
    cohort = obs_store.fetch_delivery_observations(
        DELIVERY_ID, [s.id for s in stations]
    )
    if any(
        r.source is not ObservationSource.MANUAL_IMPORT or r.parameter != "discharge"
        for r in cohort
    ):
        raise ConfigurationError("unexpected delivery cohort")
    reports: list[StationReadiness] = []
    with conn.begin_nested():
        for station in stations:
            rows = [
                r
                for r in cohort
                if r.station_id == station.id
                and window.start <= r.timestamp < window.end
            ]
            usable = [
                r
                for r in rows
                if r.qc_status is QcStatus.QC_PASSED
                and r.qc_rule_version == obs_qc_rule_version("discharge", None)
            ]
            reasons: list[str] = []
            if binding_issue := history_binding_issue(conn, station):
                reasons.append(binding_issue)
            if (
                requirements.spatial_input_type
                is not SpatialRepresentation.BASIN_AVERAGE
            ):
                reasons.append("unsupported_model_spatial_requirements")
            required_features = (
                requirements.past_dynamic_features
                | requirements.future_dynamic_features
            )
            if required_features - HISTORY_PARAMETERS:
                reasons.append("unsupported_model_weather_requirements")
            if not usable:
                reasons.append("no_current_qc_passed_delivery_history")
            data = (
                None
                if reasons
                else assemble_station_training_data(
                    station.id,
                    model,
                    window.start,
                    window.end,
                    window.time_step,
                    forcing,
                    obs_store,
                    PgBasinStore(conn),
                    store,
                )
            )
            complete = complete_training_rows(data) if data is not None else None
            samples = (
                count_training_samples(
                    complete,
                    window.time_step,
                    requirements.lookback_steps,
                    requirements.forecast_horizon_steps,
                )
                if complete is not None
                else 0
            )
            if data is None and not reasons:
                reasons.append("missing_training_inputs_or_statics")
            if samples < window.minimum_samples:
                reasons.append("insufficient_complete_training_samples")
            status = ReadinessStatus.HELD if reasons else ReadinessStatus.READY
            store.update_station(
                replace(
                    station,
                    forecast_targets=frozenset({"discharge"})
                    if status is ReadinessStatus.READY
                    else None,
                    updated_at=now,
                )
            )
            stamps = complete["timestamp"].to_list() if complete is not None else []
            reports.append(
                StationReadiness(
                    station_id=station.id,
                    code=station.code,
                    status=status,
                    reasons=tuple(reasons),
                    qc_counts=tuple(
                        sorted(Counter(r.qc_status.value for r in rows).items())
                    ),
                    qc_versions=tuple(
                        sorted({r.qc_rule_version or "unset" for r in rows})
                    ),
                    usable_observations=len(usable),
                    complete_samples=samples,
                    overlap_start=ensure_utc(stamps[0]) if stamps else None,
                    overlap_end=ensure_utc(stamps[-1]) if stamps else None,
                )
            )
    return tuple(reports)
