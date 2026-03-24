# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import weather_forecasts
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import WeatherForecastRecord

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgWeatherForecastStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_weather_forecasts(self, records: list[WeatherForecastRecord]) -> None:
        if not records:
            return
        rows = [
            {
                "id": r.id,
                "station_id": r.station_id,
                "nwp_source": r.nwp_source,
                "cycle_time": r.cycle_time,
                "valid_time": r.valid_time,
                "parameter": r.parameter,
                "spatial_type": r.spatial_type.value,
                "band_id": r.band_id,
                "member_id": r.member_id,
                "value": r.value,
                "created_at": r.created_at,
            }
            for r in records
        ]
        stmt = pg_insert(weather_forecasts).on_conflict_do_nothing()
        self._conn.execute(stmt, rows)

    def fetch_weather_forecasts(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        parameters: list[str] | None = None,
    ) -> list[WeatherForecastRecord]:
        q = sa.select(weather_forecasts).where(
            sa.and_(
                weather_forecasts.c.station_id == station_id,
                weather_forecasts.c.nwp_source == nwp_source,
                weather_forecasts.c.cycle_time == cycle_time,
            )
        )
        if parameters is not None:
            q = q.where(weather_forecasts.c.parameter.in_(parameters))
        rows = self._conn.execute(q).mappings().all()
        return [_row_to_record(row) for row in rows]

    def fetch_lookback(
        self,
        station_id: StationId,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[WeatherForecastRecord]:
        q = sa.select(weather_forecasts).where(
            sa.and_(
                weather_forecasts.c.station_id == station_id,
                weather_forecasts.c.nwp_source == nwp_source,
                weather_forecasts.c.valid_time >= start,
                weather_forecasts.c.valid_time < end,
            )
        )
        rows = self._conn.execute(q).mappings().all()
        return [_row_to_record(row) for row in rows]

    def fetch_received_cycles(
        self,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[UtcDatetime]:
        q = (
            sa.select(weather_forecasts.c.cycle_time.distinct())
            .where(
                sa.and_(
                    weather_forecasts.c.nwp_source == nwp_source,
                    weather_forecasts.c.cycle_time >= start,
                    weather_forecasts.c.cycle_time < end,
                )
            )
            .order_by(weather_forecasts.c.cycle_time)
        )
        rows = self._conn.execute(q).all()
        return [utc_from_row(row[0]) for row in rows]

    def mark_gap(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        recoverable: bool,
    ) -> None:
        pass

    def fetch_latest_cycle_time(self, nwp_source: str) -> UtcDatetime | None:
        q = sa.select(sa.func.max(weather_forecasts.c.cycle_time)).where(
            weather_forecasts.c.nwp_source == nwp_source
        )
        result = self._conn.execute(q).scalar()
        return utc_from_row(result) if result is not None else None


def _row_to_record(row: sa.engine.row.RowMapping) -> WeatherForecastRecord:
    from uuid import UUID

    return WeatherForecastRecord(
        id=UUID(str(row["id"])),
        station_id=StationId(row["station_id"]),
        nwp_source=row["nwp_source"],
        cycle_time=utc_from_row(row["cycle_time"]),
        valid_time=utc_from_row(row["valid_time"]),
        parameter=row["parameter"],
        spatial_type=SpatialRepresentation(row["spatial_type"]),
        band_id=row["band_id"],
        member_id=row["member_id"],
        value=row["value"],
        created_at=utc_from_row(row["created_at"]),
    )
