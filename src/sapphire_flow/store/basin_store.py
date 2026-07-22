# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape, to_shape
from sqlalchemy.dialects.postgresql import JSONB

from sapphire_flow.db.metadata import basin_versions, basins
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId, PackageId


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

    def store_basin(
        self,
        basin: Basin,
        *,
        package_id: PackageId | None = None,
        gateway_mapping: list[dict[str, Any]] | None = None,
    ) -> BasinId:
        """Atomically write the ``basins`` projection row AND its paired
        ``version=1, superseded_at IS NULL`` ``basin_versions`` row, in ONE
        data-modifying CTE (Plan 120 Task 0A / D-0A).

        This is the SINGLE basin-creation path for both station onboarding
        (``package_id=None`` — the legacy/non-package sentinel) and the
        package importer (``package_id`` set). A single SQL statement is
        atomic under Postgres even on an AUTOCOMMIT connection
        (``flows/_db.py``'s production connection) — two separate INSERT
        statements would each self-commit independently and could leave a
        committed ``basins`` row with no current ``basin_versions`` row if
        the second failed.
        """
        wkb_geometry = from_shape(basin.geometry, srid=4326)
        basins_cte = (
            sa.insert(basins)
            .values(
                id=basin.id,
                code=basin.code,
                name=basin.name,
                geometry=wkb_geometry,
                area_km2=basin.area_km2,
                attributes=basin.attributes,
                regional_basin=basin.regional_basin,
                band_geometries=basin.band_geometries,
                network=basin.network,
                package_id=package_id,
            )
            .returning(basins.c.id)
            .cte("inserted_basin")
        )
        version_select = sa.select(
            sa.literal(uuid.uuid4(), type_=sa.Uuid),
            basins_cte.c.id,
            sa.literal(package_id, type_=sa.Text),
            sa.literal(1),
            sa.literal(wkb_geometry, type_=Geometry("MULTIPOLYGON", srid=4326)),
            sa.literal(basin.attributes, type_=JSONB),
            sa.literal(basin.area_km2),
            sa.literal(basin.band_geometries, type_=JSONB),
            sa.literal(gateway_mapping, type_=JSONB),
            sa.null(),
        )
        stmt = sa.insert(basin_versions).from_select(
            [
                "id",
                "basin_id",
                "package_id",
                "version",
                "geometry",
                "attributes",
                "area_km2",
                "band_geometries",
                "gateway_mapping",
                "superseded_at",
            ],
            version_select,
        )
        # Exactly one execute() call — the whole pair is ONE statement.
        self._conn.execute(stmt)
        return basin.id


def _row_to_domain(row: sa.engine.row.RowMapping) -> Basin:
    return Basin(
        id=BasinId(row["id"]),
        code=row["code"],
        name=row["name"],
        geometry=to_shape(row["geometry"]),
        area_km2=row["area_km2"],
        attributes=row["attributes"],
        regional_basin=row["regional_basin"],
        band_geometries=row["band_geometries"],
        created_at=utc_from_row(row["created_at"]),
        network=row["network"],
        package_id=(
            PackageId(row["package_id"]) if row["package_id"] is not None else None
        ),
    )
