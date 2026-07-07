# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import clim_baselines
from sapphire_flow.types.domain import ClimBaseline
from sapphire_flow.types.ids import StationId


class PgClimBaselineStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_baselines(self, baselines: list[ClimBaseline]) -> None:
        if not baselines:
            return
        rows = [
            {
                "station_id": b.station_id,
                "parameter": b.parameter,
                "day_of_year": b.day_of_year,
                "rolling_mean": b.rolling_mean,
                "rolling_std": b.rolling_std,
                "sample_count": b.sample_count,
            }
            for b in baselines
        ]
        stmt = (
            pg_insert(clim_baselines)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["station_id", "parameter", "day_of_year"],
                set_={
                    "rolling_mean": sa.literal_column("excluded.rolling_mean"),
                    "rolling_std": sa.literal_column("excluded.rolling_std"),
                    "sample_count": sa.literal_column("excluded.sample_count"),
                },
            )
        )
        self._conn.execute(stmt)

    def delete_baselines(self, station_id: StationId, parameter: str) -> None:
        self._conn.execute(
            sa.delete(clim_baselines).where(
                sa.and_(
                    clim_baselines.c.station_id == station_id,
                    clim_baselines.c.parameter == parameter,
                )
            )
        )

    def fetch_baselines(
        self, station_id: StationId, parameter: str
    ) -> list[ClimBaseline]:
        q = (
            sa.select(clim_baselines)
            .where(
                sa.and_(
                    clim_baselines.c.station_id == station_id,
                    clim_baselines.c.parameter == parameter,
                )
            )
            .order_by(clim_baselines.c.day_of_year)
        )
        rows = self._conn.execute(q).mappings().all()
        return [_row_to_baseline(row) for row in rows]

    def fetch_baseline(
        self, station_id: StationId, parameter: str, day_of_year: int
    ) -> ClimBaseline | None:
        q = sa.select(clim_baselines).where(
            sa.and_(
                clim_baselines.c.station_id == station_id,
                clim_baselines.c.parameter == parameter,
                clim_baselines.c.day_of_year == day_of_year,
            )
        )
        row = self._conn.execute(q).mappings().one_or_none()
        return _row_to_baseline(row) if row is not None else None


def _row_to_baseline(row: sa.engine.row.RowMapping) -> ClimBaseline:
    return ClimBaseline(
        station_id=StationId(row["station_id"]),
        parameter=row["parameter"],
        day_of_year=row["day_of_year"],
        rolling_mean=row["rolling_mean"],
        rolling_std=row["rolling_std"],
        sample_count=row["sample_count"],
    )
