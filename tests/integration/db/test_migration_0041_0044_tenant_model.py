"""Plan 147 Slice A — LOCKED acceptance tests for migrations 0041-0044 (the
tenant-model foundation).

Real Alembic upgrades/downgrades against a throwaway PostGIS container
(``migration_engine`` fixture, mirrors
``tests/integration/db/test_migration_0033_camels_retire.py`` /
``test_migration_0039_basin_static_provenance.py``).

RED-before-fix: before these migrations exist, ``stations``/``station_groups``
have no ``tenant_id`` column and ``station_groups.name`` is globally unique —
every test in this file targets behaviour these four migrations introduce.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import (
    station_group_members,
    station_groups,
    stations,
    tenants,
)
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.types.ids import StationGroupId, StationId, TenantId
from sapphire_flow.types.station import StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOCATION = "SRID=4326;POINT(8.5 47.4)"


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade/downgrade can
    mutate the schema without disturbing the shared session-scoped engine."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_147a_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            yield engine, url
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _alembic_cfg(url: str) -> object:
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _insert_pre_tenant_station(
    conn: sa.Connection,
    *,
    code: str,
    station_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Seed a station using columns that exist before migration 0042 (plus an
    optional ``tenant_id`` for callers running at/after 0042 — the column now
    carries NO server default, so it must be named explicitly once it exists).
    SQLAlchemy Core only emits the columns named in ``.values()``, so passing
    no ``tenant_id`` stays safe on the pre-0042 schema."""
    sid = station_id or uuid.uuid4()
    values: dict[str, object] = {
        "id": sid,
        "code": code,
        "name": f"Station {code}",
        "location": _LOCATION,
        "station_kind": "river",
        "network": "bafu",
        "timezone": "Europe/Zurich",
        "measured_parameters": ["discharge"],
        "ownership": "own",
    }
    if tenant_id is not None:
        values["tenant_id"] = tenant_id
    conn.execute(sa.insert(stations).values(**values))
    return sid


def _insert_pre_tenant_group(
    conn: sa.Connection,
    *,
    name: str,
    group_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    gid = group_id or uuid.uuid4()
    values: dict[str, object] = {"id": gid, "name": name, "description": None}
    if tenant_id is not None:
        values["tenant_id"] = tenant_id
    conn.execute(sa.insert(station_groups).values(**values))
    return gid


def _insert_pre_tenant_member(
    conn: sa.Connection,
    *,
    group_id: uuid.UUID,
    station_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
) -> None:
    values: dict[str, object] = {"group_id": group_id, "station_id": station_id}
    if tenant_id is not None:
        values["tenant_id"] = tenant_id
    conn.execute(sa.insert(station_group_members).values(**values))


class TestTenantColumnHasNoServerDefault:
    """MAJOR 1(a): after the one-time backfill, every `tenant_id` column drops
    its server default — a raw INSERT that OMITS tenant_id must FAIL LOUD
    (NotNullViolation), never silently land on the Swiss `sapphire` tenant.

    Soundness: this test FAILS against the pre-fix migrations (which left a
    persistent ``server_default='...0001'`` on each column — the insert would
    silently succeed on the default tenant instead of raising).
    """

    def test_station_insert_omitting_tenant_id_raises_not_null(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.connect() as conn, pytest.raises(IntegrityError) as exc_info:
            # NOTE: deliberately omit tenant_id — no server default exists, so
            # PostgreSQL must reject this with a NOT NULL violation.
            _insert_pre_tenant_station(conn, code="NO-TENANT-001")
            conn.commit()
        assert "tenant_id" in str(exc_info.value)

    def test_group_insert_omitting_tenant_id_raises_not_null(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.connect() as conn, pytest.raises(IntegrityError) as exc_info:
            _insert_pre_tenant_group(conn, name="no-tenant-group")
            conn.commit()
        assert "tenant_id" in str(exc_info.value)

    def test_member_insert_omitting_tenant_id_raises_not_null(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            sid = _insert_pre_tenant_station(
                conn, code="MEMBER-NT-001", tenant_id=DEFAULT_TENANT_ID
            )
            gid = _insert_pre_tenant_group(
                conn, name="member-nt-group", tenant_id=DEFAULT_TENANT_ID
            )

        with engine.connect() as conn, pytest.raises(IntegrityError) as exc_info:
            _insert_pre_tenant_member(conn, group_id=gid, station_id=sid)
            conn.commit()
        assert "tenant_id" in str(exc_info.value)


class TestMigrationUpgradeBackfillsDefaultTenant:
    """F14/verify: `alembic upgrade head` lands `sapphire`-tenant rows on
    every pre-existing station/group/member, with all FKs/uniques in place.
    """

    def test_seeds_default_tenant_and_backfills_populated_data(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0040")

        with engine.begin() as conn:
            sid = _insert_pre_tenant_station(conn, code="TEN-001")
            gid = _insert_pre_tenant_group(conn, name="pre-migration-group")
            _insert_pre_tenant_member(conn, group_id=gid, station_id=sid)

        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            tenant_row = (
                conn.execute(
                    sa.select(tenants).where(tenants.c.code == DEFAULT_TENANT_CODE)
                )
                .mappings()
                .one()
            )
            assert tenant_row["id"] == DEFAULT_TENANT_ID

            station_tenant, group_tenant, member_tenant = conn.execute(
                sa.text(
                    "SELECT s.tenant_id, g.tenant_id, m.tenant_id "
                    "FROM station_group_members m "
                    "JOIN stations s ON s.id = m.station_id "
                    "JOIN station_groups g ON g.id = m.group_id "
                    "WHERE m.group_id = :gid AND m.station_id = :sid"
                ),
                {"gid": gid, "sid": sid},
            ).one()
            assert station_tenant == DEFAULT_TENANT_ID
            assert group_tenant == DEFAULT_TENANT_ID
            assert member_tenant == DEFAULT_TENANT_ID


class TestPerTenantGroupNameUniqueness:
    """Two tenants may hold a group of the same name; one tenant may not."""

    def test_two_tenants_may_share_a_group_name_one_tenant_may_not(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        other_tenant_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(tenants).values(
                    id=other_tenant_id, code="dhm", name="DHM (Nepal)"
                )
            )

        with engine.connect() as conn:
            store = PgStationGroupStore(conn)
            store.store_group(
                StationGroup(
                    id=StationGroupId(uuid.uuid4()),
                    name="regional",
                    station_ids=frozenset(),
                    created_at=_now(),
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
            # Same name, DIFFERENT tenant: must succeed.
            store.store_group(
                StationGroup(
                    id=StationGroupId(uuid.uuid4()),
                    name="regional",
                    station_ids=frozenset(),
                    created_at=_now(),
                    tenant_id=TenantId(other_tenant_id),
                )
            )
            # store_group commits its own (default engine.begin()) txn — no
            # explicit commit needed on this otherwise-unused connection.

        # Same name, SAME tenant: must fail (UNIQUE(tenant_id, name)).
        with engine.connect() as conn, pytest.raises(IntegrityError):
            conn.execute(
                sa.insert(station_groups).values(
                    id=uuid.uuid4(),
                    name="regional",
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
            conn.commit()


class TestCompositeFkRejectsCrossTenantMembership:
    """R4/G3: a station cannot be added to a group of a different tenant —
    the composite FK rejects it structurally, through every writer.

    Soundness: proved by temporarily removing the composite FKs from
    migration 0044 and re-running — see the implementer's soundness note.
    """

    def test_add_station_to_group_raises_on_tenant_mismatch(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        other_tenant_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(tenants).values(
                    id=other_tenant_id, code="dhm", name="DHM (Nepal)"
                )
            )
            station_id = conn.execute(
                sa.insert(stations)
                .values(
                    id=uuid.uuid4(),
                    code="XT-001",
                    name="Cross-tenant station",
                    location=_LOCATION,
                    station_kind="river",
                    network="dhm",
                    timezone="Asia/Kathmandu",
                    measured_parameters=["discharge"],
                    ownership="own",
                    tenant_id=other_tenant_id,
                )
                .returning(stations.c.id)
            ).scalar_one()

        with engine.connect() as conn:
            group_store = PgStationGroupStore(conn)
            group = StationGroup(
                id=StationGroupId(uuid.uuid4()),
                name="sapphire-only-group",
                station_ids=frozenset(),
                created_at=_now(),
                tenant_id=DEFAULT_TENANT_ID,
            )
            group_store.store_group(group)

        with engine.connect() as conn, pytest.raises(IntegrityError):
            PgStationGroupStore(conn).add_station_to_group(
                group.id, StationId(station_id)
            )
            conn.commit()

    def test_raw_sql_insert_with_mismatched_tenant_also_rejected(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        """The composite FK is DB-level — it rejects even a raw SQL writer
        that bypasses the store entirely (no trigger, no session variable)."""
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        other_tenant_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(tenants).values(
                    id=other_tenant_id, code="dhm", name="DHM (Nepal)"
                )
            )
            # At head every tenant_id column is NOT NULL with no default — seed
            # the station on tenant B and the group on tenant A explicitly.
            station_id = _insert_pre_tenant_station(
                conn, code="XT-002", tenant_id=other_tenant_id
            )
            group_id = _insert_pre_tenant_group(
                conn, name="raw-sql-group", tenant_id=DEFAULT_TENANT_ID
            )

        with engine.connect() as conn, pytest.raises(IntegrityError):
            conn.execute(
                sa.insert(station_group_members).values(
                    group_id=group_id,
                    station_id=station_id,
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
            conn.commit()


class TestMigration0044MismatchGuard:
    """The backfill guard: an EXISTING member row whose station/group tenants
    already disagree makes the upgrade fail loudly, rather than silently
    coercing tenant identity.
    """

    def test_upgrade_raises_on_pre_existing_tenant_mismatch(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0043")

        other_tenant_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(tenants).values(
                    id=other_tenant_id, code="dhm", name="DHM (Nepal)"
                )
            )
            # A member row created BEFORE 0044 exists, straddling two
            # tenants: the station is tenant B, the group is the default
            # tenant A. 0044's backfill must refuse to paper over this.
            # At rev 0043: stations/station_groups carry tenant_id (no default),
            # station_group_members does NOT yet (added by 0044). The station is
            # tenant B, the group tenant A; the pre-0044 member row (no tenant
            # column) straddles them.
            station_id = _insert_pre_tenant_station(
                conn, code="MISMATCH-001", tenant_id=other_tenant_id
            )
            group_id = _insert_pre_tenant_group(
                conn, name="mismatch-group", tenant_id=DEFAULT_TENANT_ID
            )
            _insert_pre_tenant_member(conn, group_id=group_id, station_id=station_id)

        with pytest.raises(RuntimeError, match="tenant_id disagrees"):
            command.upgrade(cfg, "0044")

        # Atomic: nothing was added by the failed upgrade.
        with engine.connect() as conn:
            has_column = conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'station_group_members' "
                    "AND column_name = 'tenant_id'"
                )
            ).one_or_none()
        assert has_column is None


class TestMigrationDowngradeReversesCleanly:
    """F14: the tenant migration upgrades AND downgrades cleanly on
    populated data."""

    def test_downgrade_from_head_removes_tenant_columns_keeps_data(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            # At head all three tenant_id columns are NOT NULL with no default.
            sid = _insert_pre_tenant_station(
                conn, code="DOWNGRADE-001", tenant_id=DEFAULT_TENANT_ID
            )
            gid = _insert_pre_tenant_group(
                conn, name="downgrade-group", tenant_id=DEFAULT_TENANT_ID
            )
            _insert_pre_tenant_member(
                conn, group_id=gid, station_id=sid, tenant_id=DEFAULT_TENANT_ID
            )

        command.downgrade(cfg, "0040")

        with engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'stations'"
                    )
                )
            }
            assert "tenant_id" not in columns

            group_columns = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'station_groups'"
                    )
                )
            }
            assert "tenant_id" not in group_columns

            # Data itself survives the round trip.
            code = conn.execute(
                sa.select(stations.c.code).where(stations.c.id == sid)
            ).scalar_one()
            assert code == "DOWNGRADE-001"

    def test_downgrade_fails_loudly_on_cross_tenant_group_name_collision(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        other_tenant_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(tenants).values(
                    id=other_tenant_id, code="dhm", name="DHM (Nepal)"
                )
            )
            conn.execute(
                sa.insert(station_groups).values(
                    id=uuid.uuid4(),
                    name="colliding-name",
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
            conn.execute(
                sa.insert(station_groups).values(
                    id=uuid.uuid4(),
                    name="colliding-name",
                    tenant_id=other_tenant_id,
                )
            )

        # The downgrade cannot honestly re-establish a global UNIQUE(name)
        # with two tenants holding the same name — it must fail loudly, not
        # silently drop a row or arbitrarily pick a survivor.
        with pytest.raises(IntegrityError):
            command.downgrade(cfg, "0040")


def _now():
    from datetime import UTC, datetime

    from sapphire_flow.types.datetime import ensure_utc

    return ensure_utc(datetime(2026, 7, 23, tzinfo=UTC))
