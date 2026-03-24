# pyright: reportUnknownMemberType=false
from __future__ import annotations

import json

import sqlalchemy as sa
from geoalchemy2.shape import from_shape, to_shape

from sapphire_flow.db.metadata import basins
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId


class PgBasinStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_basin(self, basin_id: BasinId) -> Basin | None:
        row = (
            self._conn.execute(sa.select(basins).where(basins.c.id == basin_id))
            .mappings()
            .one_or_none()
        )
        return _row_to_domain(row) if row is not None else None

    def fetch_basin_by_code(self, code: str, network: str) -> Basin | None:
        row = (
            self._conn.execute(
                sa.select(basins).where(
                    sa.and_(basins.c.code == code, basins.c.network == network)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _row_to_domain(row) if row is not None else None

    def fetch_all_basins(self) -> list[Basin]:
        rows = self._conn.execute(sa.select(basins)).mappings().all()
        return [_row_to_domain(row) for row in rows]

    def store_basin(self, basin: Basin) -> BasinId:
        self._conn.execute(
            sa.insert(basins).values(
                id=basin.id,
                code=basin.code,
                name=basin.name,
                geometry=from_shape(basin.geometry, srid=4326),
                area_km2=basin.area_km2,
                attributes=basin.attributes,
                band_geometries=json.dumps(basin.band_geometries)
                if basin.band_geometries is not None
                else None,
                network=basin.network,
            )
        )
        return basin.id


def _row_to_domain(row: sa.engine.row.RowMapping) -> Basin:
    return Basin(
        id=BasinId(row["id"]),
        code=row["code"],
        name=row["name"],
        geometry=to_shape(row["geometry"]),
        area_km2=row["area_km2"],
        attributes=row["attributes"],
        band_geometries=row["band_geometries"],
        created_at=utc_from_row(row["created_at"]),
        network=row["network"],
    )
