# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

import sqlalchemy as sa
from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape, to_shape
from sqlalchemy.dialects.postgresql import JSONB

from sapphire_flow.db.metadata import basin_versions, basins
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store._helpers import require_real_transaction, utc_from_row
from sapphire_flow.types.basin import Basin, BasinCorrectionResult
from sapphire_flow.types.ids import BasinId, BasinVersionId, PackageId

_CARAVAN_PREFIX: Final[str] = "caravan:"

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgBasinStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    @property
    def connection(self) -> sa.Connection:
        """Read-only access to the underlying connection -- needed by
        multi-statement callers that must verify they are running inside a
        real transaction before writing (Plan 155:
        `store/caravan_import.py::import_caravan_attributes`,
        `store/_helpers.py::require_real_transaction`)."""
        return self._conn

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

    def merge_namespaced_attributes(
        self,
        basin_id: BasinId,
        *,
        attributes: dict[str, Any],
    ) -> None:
        """Plan 155 T1b's dedicated ADDITIVE operation: union ``attributes``
        into ``basins.attributes`` via a JSONB merge (``||``) -- no new
        ``basin_versions`` row, no ``material_change`` flag, no
        affected-artifact set. This is deliberately NOT
        ``update_basin_from_package`` (the correction branch), which
        replaces attributes/geometry/area wholesale and always flags
        incumbent artifacts -- exactly what an additive attribute merge
        must not do.

        Structurally guarded: every key in ``attributes`` must carry the
        ``"caravan:"`` prefix (D15), hardcoded -- NOT a caller-supplied
        parameter (Plan 155 fixer round minor finding: an exposed
        ``prefix`` argument let a caller pass ``prefix=""`` and defeat the
        guard for every key, including a bare ``"area"``). This makes the
        operation structurally incapable of modifying an existing
        (non-namespaced) attribute even by mistake, which is what makes
        skipping the supersede/flag machinery sound in the first place.

        Plan 155 fixer round (major finding): a changed re-import must not
        silently overwrite an existing namespaced value via the JSONB
        ``||`` merge with no trace. Fetches the basin's CURRENT attributes
        first; a key already present with a DIFFERING value raises
        ``ConfigurationError`` naming every conflicting key (existing vs.
        incoming) rather than merging over it -- an identical replay (same
        value) is still a no-op success. This is deliberately NOT a full
        immutable-provenance/lineage system (a durable, persisted
        source-version record and artifact invalidation on a genuine
        source revision remain out of scope for this additive path -- see
        the plan's T1 deviation note); it closes the SILENT half of the
        finding: any content change is now a loud failure, never an
        untraced overwrite.

        Plan 155 fixer round (major finding, check/update race): the
        read-then-compare-then-write shape above is a classic TOCTOU --
        without a row lock, two concurrent imports can both read the SAME
        "no existing key" snapshot before either has written, so both
        conclude there is no conflict and the second's JSONB ``||`` write
        silently wins over the first's, even though the two committed
        DIFFERENT values for the same key. ``SELECT ... FOR UPDATE`` locks
        this basin's row for the remainder of the caller's transaction, so
        a second concurrent caller's own ``SELECT ... FOR UPDATE`` blocks
        until the first commits (or rolls back) -- it then re-reads the
        NOW-current attributes (reflecting the first caller's write, if
        any) before comparing, so the conflict this function promises to
        catch is actually observed rather than raced past. This relies on
        the caller running inside a real, already-open transaction (see
        `store/_helpers.py::require_real_transaction`, which every
        production caller of this method is gated behind via
        `store/caravan_import.py::import_caravan_attributes`) -- on an
        AUTOCOMMIT connection the lock is released the instant the SELECT
        statement completes, before the UPDATE, and offers no protection.
        """
        bad_keys = sorted(k for k in attributes if not k.startswith(_CARAVAN_PREFIX))
        if bad_keys:
            raise ValueError(
                f"merge_namespaced_attributes refuses key(s) without the "
                f"{_CARAVAN_PREFIX!r} prefix: {bad_keys} -- this additive path is "
                "guarded to be structurally incapable of touching an "
                "existing (non-namespaced) attribute"
            )
        current = (
            self._conn.execute(
                sa.select(basins.c.attributes)
                .where(basins.c.id == basin_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise ValueError(f"merge_namespaced_attributes: basin {basin_id} not found")
        current_attrs: dict[str, Any] = current["attributes"] or {}
        conflicts = {
            key: (current_attrs[key], value)
            for key, value in attributes.items()
            if key in current_attrs and current_attrs[key] != value
        }
        if conflicts:
            raise ConfigurationError(
                f"merge_namespaced_attributes: basin {basin_id} already carries "
                f"differing value(s) for {sorted(conflicts)} -- refusing a "
                "silent overwrite on a changed re-import; existing -> incoming: "
                f"{conflicts}"
            )
        result = self._conn.execute(
            sa.update(basins)
            .where(basins.c.id == basin_id)
            .values(
                attributes=sa.func.coalesce(
                    basins.c.attributes, sa.cast(sa.literal("{}"), JSONB)
                ).op("||")(sa.literal(attributes, type_=JSONB))
            )
            .returning(basins.c.id)
        )
        if result.first() is None:
            raise ValueError(f"merge_namespaced_attributes: basin {basin_id} not found")

    def replace_namespaced_attributes(
        self,
        basin_id: BasinId,
        *,
        attributes: dict[str, Any],
    ) -> None:
        """Plan 188 T3 (D4) -- the ONE missing recovery primitive:
        ``merge_namespaced_attributes`` refuses a changed value under an
        already-merged ``caravan:``-namespaced key by design; this is its
        sibling for the rare case a delivered value genuinely needs
        correcting (a re-delivered, corrected parquet). It is deliberately
        NOT ``update_basin_from_package`` (the correction branch) -- that
        replaces `attributes`/geometry/area wholesale and always flags
        incumbent artifacts as `material_change`, a sledgehammer for one
        changed static.

        Same structural guard as ``merge_namespaced_attributes``: every key
        in ``attributes`` must carry the hardcoded ``"caravan:"`` prefix
        (D15) -- not a caller-suppliable parameter -- so this remains
        structurally incapable of touching a non-namespaced attribute
        (e.g. ``area``) even by mistake. It also refuses any key that is
        NOT already present on the basin: "replace" means correcting an
        already-imported value (T3's stated purpose -- a re-delivered,
        corrected parquet), never inserting a new one -- that is
        ``merge_namespaced_attributes``'s job. Without this check a typo'd
        key (``caravan:areaa``) would commit silently via the JSONB ``||``
        merge instead of raising, because ``||`` adds any prefixed key
        regardless of whether it already existed.

        Unlike ``merge_namespaced_attributes``, this method calls
        ``require_real_transaction`` itself rather than trusting an
        upstream orchestrator to have checked it: there is no
        ``run_operational_...`` wrapper in front of this recovery
        primitive, so the guard has to live here or it would silently not
        exist for a direct caller. ``SELECT ... FOR UPDATE`` locks this
        basin's row for the remainder of the caller's transaction (same
        mechanism as ``merge_namespaced_attributes``'s TOCTOU fix) so a
        second concurrent caller blocks until the first commits; because
        this method does not compare against the current value (it
        REPLACES, it does not refuse), the two callers do not raise on
        each other -- they serialise, and the later committer's value
        wins for any overlapping key. This is NOT "not last-write-wins"
        (that guarantee is unachievable once replacement is permitted at
        all, Plan 188 T3) -- what the lock buys is ORDERING: B's own
        ``UPDATE`` is guaranteed to run against A's already-committed
        row, never an interleaved read-modify-write of a stale snapshot.
        """
        bad_keys = sorted(k for k in attributes if not k.startswith(_CARAVAN_PREFIX))
        if bad_keys:
            raise ValueError(
                f"replace_namespaced_attributes refuses key(s) without the "
                f"{_CARAVAN_PREFIX!r} prefix: {bad_keys} -- this recovery path is "
                "guarded to be structurally incapable of touching an "
                "existing (non-namespaced) attribute"
            )
        require_real_transaction(self._conn, caller="replace_namespaced_attributes")
        locked = (
            self._conn.execute(
                sa.select(basins.c.attributes)
                .where(basins.c.id == basin_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if locked is None:
            raise ValueError(
                f"replace_namespaced_attributes: basin {basin_id} not found"
            )
        current_attrs: dict[str, Any] = locked["attributes"] or {}
        absent_keys = sorted(k for k in attributes if k not in current_attrs)
        if absent_keys:
            raise ConfigurationError(
                f"replace_namespaced_attributes: basin {basin_id} does not "
                f"already carry key(s) {absent_keys} -- this is a "
                "REPLACEMENT primitive for correcting an already-imported "
                "value, not an insertion path; use "
                "merge_namespaced_attributes to add a genuinely new key "
                "(or check for a typo in the requested key)"
            )
        result = self._conn.execute(
            sa.update(basins)
            .where(basins.c.id == basin_id)
            .values(
                attributes=sa.func.coalesce(
                    basins.c.attributes, sa.cast(sa.literal("{}"), JSONB)
                ).op("||")(sa.literal(attributes, type_=JSONB))
            )
            .returning(basins.c.id)
        )
        if result.first() is None:
            raise ValueError(
                f"replace_namespaced_attributes: basin {basin_id} not found"
            )

    def update_basin_from_package(
        self,
        *,
        basin_id: BasinId,
        package_id: PackageId,
        name: str,
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

        **Fixer round (major finding, 2026-07-23):** ``name`` is a REQUIRED
        kwarg, not optional — a corrected package's ``display_name`` must
        refresh ``basins.name`` (the operational projection) exactly like it
        does on the new-basin insert path (``_insert_new_basin`` /
        ``Basin(name=...)``); a correction that touched every other column
        but silently retained the previous display name was the bug this
        closes.

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
                name=name,
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
