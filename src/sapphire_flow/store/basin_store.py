# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape, to_shape
from sqlalchemy.dialects.postgresql import JSONB

from sapphire_flow.db.metadata import basin_versions, basins
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.basin import Basin, BasinCorrectionResult
from sapphire_flow.types.ids import BasinId, BasinVersionId, PackageId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


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
        # Parse, don't validate: reconcile the kwarg override with the field on
        # the domain object. A caller passing BOTH a `package_id` kwarg AND a
        # `basin.package_id`, with the two disagreeing, is a bug — not a
        # precedence decision to make silently.
        if (
            package_id is not None
            and basin.package_id is not None
            and package_id != basin.package_id
        ):
            raise ValueError(
                "conflicting package_id: kwarg "
                f"{package_id!r} != basin.package_id {basin.package_id!r}"
            )
        effective_package_id = (
            package_id if package_id is not None else basin.package_id
        )
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
                package_id=effective_package_id,
            )
            .returning(basins.c.id)
            .cte("inserted_basin")
        )
        version_select = sa.select(
            sa.literal(uuid.uuid4(), type_=sa.Uuid),
            basins_cte.c.id,
            sa.literal(effective_package_id, type_=sa.Text),
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

    def update_basin_from_package(
        self,
        *,
        basin_id: BasinId,
        package_id: PackageId,
        geometry: Any,
        attributes: dict[str, Any] | None,
        area_km2: float | None,
        regional_basin: str | None,
        band_geometries: list[dict] | None,  # type: ignore[type-arg]
        gateway_mapping: list[dict[str, Any]] | None,
        superseded_at: UtcDatetime,
    ) -> BasinCorrectionResult:
        """Correction branch of the canonical write pipeline (Plan 120 Task
        2C, Decision B): stamp the prior current ``basin_versions`` row's
        ``superseded_at``, append a new ``version+1`` current row, and
        refresh the ``basins`` projection — in THIS exact order (a stamp
        before an append), so the DB never represents two current
        (``superseded_at IS NULL``) rows for one basin (the
        ``uq_basin_versions_one_current_per_basin`` partial unique index).
        This is the SEPARATE upsert path Task 2C adds because
        ``store_basin`` is insert-only (the new-basin creation path).

        **Fixer round (major finding, mirrors Task 0A's ``store_basin``):**
        the stamp/append/refresh triple runs as ONE data-modifying,
        chained-CTE statement — not three separate ``execute()`` calls — so
        it is atomic even on an AUTOCOMMIT connection (the earlier
        three-statement form could leave a basin with ZERO current
        ``basin_versions`` rows if the second statement failed after the
        first had already self-committed). The initial read (fetching the
        current row's id/version) stays a separate, plain ``SELECT`` — reads
        do not threaten atomicity. Each write CTE is wired into the next via
        a genuine data dependency (``select_from``/subquery), not just
        WITH-clause adjacency, so Postgres is guaranteed to execute all
        three rather than skip an unreferenced CTE.
        """
        current = (
            self._conn.execute(
                sa.select(basin_versions.c.id, basin_versions.c.version).where(
                    sa.and_(
                        basin_versions.c.basin_id == basin_id,
                        basin_versions.c.superseded_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise ValueError(
                f"basin {basin_id} has no current basin_versions row — the "
                "Task 0A invariant (exactly one current version per basin) "
                "is violated; cannot apply a correction"
            )
        superseded_id = BasinVersionId(current["id"])
        new_version_id = BasinVersionId(uuid.uuid4())
        wkb_geometry = from_shape(geometry, srid=4326)

        # (a) stamp the prior current row's superseded_at FIRST — must
        # commit-order before (b), or the partial unique index would briefly
        # see two current rows.
        supersede_cte = (
            sa.update(basin_versions)
            .where(basin_versions.c.id == superseded_id)
            .values(superseded_at=superseded_at)
            .returning(basin_versions.c.id)
            .cte("superseded")
        )
        # (b) append the new current row — selects FROM `supersede_cte` (a
        # genuine data dependency, not just WITH-clause adjacency) so
        # Postgres is guaranteed to run (a) as part of this one statement.
        insert_select = sa.select(
            sa.literal(new_version_id, type_=sa.Uuid),
            sa.literal(basin_id, type_=sa.Uuid),
            sa.literal(package_id, type_=sa.Text),
            sa.literal(current["version"] + 1),
            sa.literal(wkb_geometry, type_=Geometry("MULTIPOLYGON", srid=4326)),
            sa.literal(attributes, type_=JSONB),
            sa.literal(area_km2),
            sa.literal(band_geometries, type_=JSONB),
            sa.literal(gateway_mapping, type_=JSONB),
            sa.null(),
        ).select_from(supersede_cte)
        insert_cte = (
            sa.insert(basin_versions)
            .from_select(
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
                insert_select,
            )
            .returning(basin_versions.c.basin_id)
            .cte("inserted_version")
        )
        # (c) refresh the basins projection — targets the row via a
        # subquery on `insert_cte`, so Postgres is guaranteed to run (b)
        # (and therefore (a)) as part of this one statement.
        final_stmt = (
            sa.update(basins)
            .where(basins.c.id == sa.select(insert_cte.c.basin_id).scalar_subquery())
            .values(
                geometry=wkb_geometry,
                attributes=attributes,
                area_km2=area_km2,
                regional_basin=regional_basin,
                band_geometries=band_geometries,
                package_id=package_id,
            )
        )
        # Exactly one execute() call — the whole triple is ONE statement.
        self._conn.execute(final_stmt)

        return BasinCorrectionResult(
            basin_id=basin_id,
            superseded_version_id=superseded_id,
            new_version_id=new_version_id,
        )


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
