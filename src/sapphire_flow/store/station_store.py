# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import (
    model_assignments,
    station_thresholds,
    station_weather_sources,
    stations,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.domain import GeoCoord, StationThreshold
from sapphire_flow.types.enums import (
    GaugingStatus,
    ModelAssignmentStatus,
    RegulationType,
    SpatialRepresentation,
    StationKind,
    StationOwnership,
    StationStatus,
    ThresholdSource,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import BasinId, ModelId, StationId
from sapphire_flow.types.station import (
    ModelAssignment,
    StationConfig,
    StationWeatherSource,
)

log = structlog.get_logger(__name__)

# Plan 115a migration 0030's backfill allowlist. Kept in sync manually with
# ``alembic/versions/0030_weather_source_role.py`` — an unknown nwp_source is a
# human decision, never a silent guess.
_KNOWN_FORECAST_SOURCES = frozenset({"icon_ch2_eps"})
_KNOWN_REANALYSIS_SOURCES = frozenset({"camels-ch"})


# Plan 115c: delete at the next free revision at implementation time (once role
# is NOT NULL, this shim and its allowlist are dead code). Do not hardcode a
# number — 115b5 / Release B already claimed revision 0033 for the camels-ch
# retire migration; 115c takes the next free slot after that.
def _legacy_role_for_source(nwp_source: str) -> WeatherSourceRole:
    if nwp_source in _KNOWN_FORECAST_SOURCES:
        return WeatherSourceRole.FORECAST
    if nwp_source in _KNOWN_REANALYSIS_SOURCES:
        return WeatherSourceRole.REANALYSIS
    raise ConfigurationError(
        f"weather source {nwp_source!r} has a NULL role and is not in the "
        "legacy backfill allowlist (icon_ch2_eps, camels-ch); it must be "
        "assigned a role explicitly rather than guessed"
    )


class PgStationStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_station(self, station_id: StationId) -> StationConfig | None:
        row = (
            self._conn.execute(
                sa.select(
                    stations,
                    sa.func.ST_X(stations.c.location).label("lon"),
                    sa.func.ST_Y(stations.c.location).label("lat"),
                ).where(stations.c.id == station_id)
            )
            .mappings()
            .one_or_none()
        )
        return _row_to_station(row) if row is not None else None

    def fetch_station_by_code(self, code: str, network: str) -> StationConfig | None:
        row = (
            self._conn.execute(
                sa.select(
                    stations,
                    sa.func.ST_X(stations.c.location).label("lon"),
                    sa.func.ST_Y(stations.c.location).label("lat"),
                ).where(sa.and_(stations.c.code == code, stations.c.network == network))
            )
            .mappings()
            .one_or_none()
        )
        return _row_to_station(row) if row is not None else None

    def fetch_all_stations(
        self, kind: StationKind | None = None
    ) -> list[StationConfig]:
        q = sa.select(
            stations,
            sa.func.ST_X(stations.c.location).label("lon"),
            sa.func.ST_Y(stations.c.location).label("lat"),
        )
        if kind is not None:
            q = q.where(stations.c.station_kind == kind.value)
        rows = self._conn.execute(q).mappings().all()
        return [_row_to_station(row) for row in rows]

    def fetch_stations_by_ownership(
        self,
        ownership: StationOwnership,
        kind: StationKind | None = None,
    ) -> list[StationConfig]:
        q = sa.select(
            stations,
            sa.func.ST_X(stations.c.location).label("lon"),
            sa.func.ST_Y(stations.c.location).label("lat"),
        ).where(stations.c.ownership == ownership.value)
        if kind is not None:
            q = q.where(stations.c.station_kind == kind.value)
        rows = self._conn.execute(q).mappings().all()
        return [_row_to_station(row) for row in rows]

    def store_station(self, station: StationConfig) -> StationId:
        self._conn.execute(
            sa.insert(stations).values(
                id=station.id,
                code=station.code,
                name=station.name,
                location=sa.func.ST_SetSRID(
                    sa.func.ST_MakePoint(station.location.lon, station.location.lat),
                    4326,
                ),
                altitude_masl=station.location.altitude_masl,
                water_level_datum_masl=station.water_level_datum_masl,
                water_level_unit=station.water_level_unit,
                station_kind=station.station_kind.value,
                basin_id=station.basin_id,
                timezone=station.timezone,
                regulation_type=station.regulation_type.value
                if station.regulation_type is not None
                else None,
                forecast_targets=list(station.forecast_targets)
                if station.forecast_targets
                else None,
                measured_parameters=list(station.measured_parameters),
                station_status=station.station_status.value,
                created_at=station.created_at,
                updated_at=station.updated_at,
                network=station.network,
                ownership=station.ownership.value,
                wigos_id=station.wigos_id,
                gauging_status=station.gauging_status.value,
            )
        )
        return station.id

    def update_station(self, station: StationConfig) -> None:
        self._conn.execute(
            sa.update(stations)
            .where(stations.c.id == station.id)
            .values(
                name=station.name,
                location=sa.func.ST_SetSRID(
                    sa.func.ST_MakePoint(station.location.lon, station.location.lat),
                    4326,
                ),
                altitude_masl=station.location.altitude_masl,
                water_level_datum_masl=station.water_level_datum_masl,
                water_level_unit=station.water_level_unit,
                measured_parameters=list(station.measured_parameters),
                forecast_targets=list(station.forecast_targets)
                if station.forecast_targets
                else None,
                updated_at=station.updated_at,
            )
        )

    def fetch_thresholds(self, station_id: StationId) -> list[StationThreshold]:
        rows = (
            self._conn.execute(
                sa.select(station_thresholds).where(
                    station_thresholds.c.station_id == station_id
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_threshold(row) for row in rows]

    def store_thresholds(self, thresholds: list[StationThreshold]) -> None:
        for t in thresholds:
            stmt = (
                pg_insert(station_thresholds)
                .values(
                    station_id=t.station_id,
                    danger_level=t.danger_level,
                    parameter=t.parameter,
                    value=t.value,
                    source=t.source.value,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
                .on_conflict_do_update(
                    index_elements=["station_id", "danger_level", "parameter"],
                    set_={
                        "value": t.value,
                        "source": t.source.value,
                        "updated_at": t.updated_at,
                    },
                )
            )
            self._conn.execute(stmt)

    def fetch_model_assignments(self, station_id: StationId) -> list[ModelAssignment]:
        rows = (
            self._conn.execute(
                sa.select(model_assignments).where(
                    model_assignments.c.station_id == station_id
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_assignment(row) for row in rows]

    def store_model_assignment(self, assignment: ModelAssignment) -> None:
        stmt = (
            pg_insert(model_assignments)
            .values(
                station_id=assignment.station_id,
                model_id=assignment.model_id,
                time_step=assignment.time_step,
                status=assignment.status.value,
                priority=assignment.priority,
                created_at=assignment.created_at,
            )
            .on_conflict_do_update(
                index_elements=["station_id", "model_id"],
                set_={
                    "time_step": assignment.time_step,
                    "status": assignment.status.value,
                    "priority": assignment.priority,
                },
            )
        )
        self._conn.execute(stmt)

    def fetch_weather_sources(
        self, station_id: StationId
    ) -> list[StationWeatherSource]:
        rows = (
            self._conn.execute(
                sa.select(station_weather_sources).where(
                    station_weather_sources.c.station_id == station_id
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_weather_source(row) for row in rows]

    def store_weather_source(self, source: StationWeatherSource) -> None:
        existing_role = self._conn.execute(
            sa.select(station_weather_sources.c.role).where(
                sa.and_(
                    station_weather_sources.c.station_id == source.station_id,
                    station_weather_sources.c.nwp_source == source.nwp_source,
                )
            )
        ).scalar_one_or_none()
        if existing_role is not None and existing_role != source.role.value:
            raise ConfigurationError(
                f"weather source {source.nwp_source!r} for station "
                f"{source.station_id} already has role {existing_role!r}; "
                f"refusing to silently overwrite it with {source.role.value!r} "
                "(one nwp_source serves exactly one role per station)"
            )
        stmt = (
            pg_insert(station_weather_sources)
            .values(
                station_id=source.station_id,
                nwp_source=source.nwp_source,
                extraction_type=source.extraction_type.value,
                status=source.status.value,
                role=source.role.value,
            )
            .on_conflict_do_update(
                index_elements=["station_id", "nwp_source"],
                set_={
                    "extraction_type": source.extraction_type.value,
                    "status": source.status.value,
                    "role": source.role.value,
                },
            )
        )
        self._conn.execute(stmt)

    def fetch_forecast_binding(self, station_id: StationId) -> StationWeatherSource:
        matches = [
            s
            for s in self.fetch_weather_sources(station_id)
            if s.role == WeatherSourceRole.FORECAST
        ]
        if len(matches) != 1:
            found = [m.nwp_source for m in matches]
            raise ConfigurationError(
                f"station {station_id} has {len(matches)} FORECAST weather-source "
                f"binding(s), expected exactly 1: {found!r}"
            )
        return matches[0]

    def fetch_reanalysis_bindings(
        self, station_id: StationId
    ) -> list[StationWeatherSource]:
        return [
            s
            for s in self.fetch_weather_sources(station_id)
            if s.role == WeatherSourceRole.REANALYSIS
        ]

    def update_station_status(
        self, station_id: StationId, new_status: StationStatus
    ) -> None:
        self._conn.execute(
            sa.update(stations)
            .where(stations.c.id == station_id)
            .values(station_status=new_status.value)
        )


def _row_to_station(row: sa.engine.row.RowMapping) -> StationConfig:
    basin_raw = row["basin_id"]
    regulation_raw = row["regulation_type"]
    return StationConfig(
        id=StationId(row["id"]),
        code=row["code"],
        name=row["name"],
        location=GeoCoord(
            lon=row["lon"],
            lat=row["lat"],
            altitude_masl=row["altitude_masl"],
        ),
        station_kind=StationKind(row["station_kind"]),
        basin_id=BasinId(basin_raw) if basin_raw is not None else None,
        timezone=row["timezone"],
        regulation_type=RegulationType(regulation_raw)
        if regulation_raw is not None
        else None,
        forecast_targets=frozenset(row["forecast_targets"])
        if row["forecast_targets"]
        else None,
        measured_parameters=frozenset(row["measured_parameters"]),
        station_status=StationStatus(row["station_status"]),
        created_at=utc_from_row(row["created_at"]),
        updated_at=utc_from_row(row["updated_at"]),
        network=row["network"],
        ownership=StationOwnership(row["ownership"]),
        wigos_id=row["wigos_id"],
        gauging_status=GaugingStatus(row["gauging_status"]),
        water_level_datum_masl=row["water_level_datum_masl"],
        water_level_unit=row["water_level_unit"],
    )


def _row_to_threshold(row: sa.engine.row.RowMapping) -> StationThreshold:
    return StationThreshold(
        station_id=StationId(row["station_id"]),
        danger_level=row["danger_level"],
        parameter=row["parameter"],
        value=row["value"],
        source=ThresholdSource(row["source"]),
        created_at=utc_from_row(row["created_at"]),
        updated_at=utc_from_row(row["updated_at"]),
    )


def _row_to_assignment(row: sa.engine.row.RowMapping) -> ModelAssignment:
    return ModelAssignment(
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        time_step=row["time_step"],
        status=ModelAssignmentStatus(row["status"]),
        priority=row["priority"],
        created_at=utc_from_row(row["created_at"]),
    )


def _row_to_weather_source(row: sa.engine.row.RowMapping) -> StationWeatherSource:
    role_raw = row["role"]
    if role_raw is None:
        # Plan 115c: delete with the NOT-NULL cleanup migration (next free
        # revision after 0033; TBD) — only reachable if a pre-115a image
        # wrote this row during the rollback window.
        nwp_source = row["nwp_source"]
        role = _legacy_role_for_source(nwp_source)
        log.warning(
            "weather_source.legacy_null_role",
            station_id=str(row["station_id"]),
            nwp_source=nwp_source,
            role=role.value,
        )
    else:
        role = WeatherSourceRole(role_raw)
    return StationWeatherSource(
        station_id=StationId(row["station_id"]),
        nwp_source=row["nwp_source"],
        extraction_type=SpatialRepresentation(row["extraction_type"]),
        status=WeatherSourceStatus(row["status"]),
        role=role,
    )
