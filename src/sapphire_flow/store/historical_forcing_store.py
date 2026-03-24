# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import historical_forcing
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.historical_forcing import (
    HistoricalForcingRecord,
    RawHistoricalForcing,
)
from sapphire_flow.types.ids import HistoricalForcingId, StationId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgHistoricalForcingStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    # psycopg has a 65,535 parameter limit per statement. With 10 columns
    # per row, batch at ~5,000 rows to stay well under the limit.
    _BATCH_SIZE = 5000

    def store_forcing(self, records: list[RawHistoricalForcing]) -> None:
        if not records:
            return
        rows = [
            {
                "id": uuid4(),
                "station_id": r.station_id,
                "source": r.source,
                "version": r.version,
                "valid_time": r.valid_time,
                "parameter": r.parameter,
                "spatial_type": r.spatial_type.value,
                "band_id": r.band_id,
                "member_id": r.member_id,
                "value": r.value,
            }
            for r in records
        ]
        for i in range(0, len(rows), self._BATCH_SIZE):
            batch = rows[i : i + self._BATCH_SIZE]
            stmt = pg_insert(historical_forcing).values(batch)
            self._conn.execute(stmt.on_conflict_do_nothing())

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
        q = sa.select(historical_forcing).where(
            sa.and_(
                historical_forcing.c.station_id == station_id,
                historical_forcing.c.source == source,
                historical_forcing.c.valid_time >= start,
                historical_forcing.c.valid_time < end,
            )
        )
        if parameters is not None:
            q = q.where(historical_forcing.c.parameter.in_(parameters))
        if version is not None:
            q = q.where(historical_forcing.c.version == version)
        if member_id is not None:
            q = q.where(historical_forcing.c.member_id == member_id)
        rows = self._conn.execute(q).mappings().all()
        return [_row_to_record(row) for row in rows]

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
        q = (
            sa.select(historical_forcing.c.source)
            .where(historical_forcing.c.station_id == station_id)
            .distinct()
            .order_by(historical_forcing.c.source)
        )
        rows = self._conn.execute(q).all()
        return [row[0] for row in rows]


def _row_to_record(row: sa.engine.row.RowMapping) -> HistoricalForcingRecord:
    return HistoricalForcingRecord(
        id=HistoricalForcingId(row["id"]),
        station_id=StationId(row["station_id"]),
        source=row["source"],
        version=row["version"],
        valid_time=utc_from_row(row["valid_time"]),
        parameter=row["parameter"],
        spatial_type=SpatialRepresentation(row["spatial_type"]),
        band_id=row["band_id"],
        member_id=row["member_id"],
        value=row["value"],
        created_at=utc_from_row(row["created_at"]),
    )
