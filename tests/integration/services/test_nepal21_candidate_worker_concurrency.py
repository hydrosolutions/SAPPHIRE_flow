from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.cli.import_basin_package import run_nepal_static_import_transaction
from sapphire_flow.services.basin_importer import import_loaded_basin_package
from sapphire_flow.services.basin_package_loader import load_basin_package
from sapphire_flow.services.nepal21_delivery_authority import OLD6_STATION_CODES
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.services.test_nepal21_candidate_update_transactions import (
    role_harness as role_harness,
)
from tests.integration.services.test_nepal21_static_import import (
    _copy_full21_static_package,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )


def _as_role(
    conn: sa.Connection, role: Literal["sapphire_worker", "sapphire_static_importer"]
) -> None:
    conn.execute(sa.text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
    assert conn.scalar(sa.text("SELECT session_user")) == role
    conn.execute(sa.text("SET LOCAL lock_timeout='100ms'"))


def _import_candidates(conn: sa.Connection, tenant: TenantId, package: Path) -> None:
    _as_role(conn, "sapphire_static_importer")
    report = run_nepal_static_import_transaction(
        conn,
        tenant_id=tenant,
        package_dir=package,
        expected_package_id="nepal21_20261012",
        expected_hru="nepal21_20261012",
        clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
    )
    assert len(report.accepted) == 21


@pytest.fixture
def candidate_seed(
    role_harness: _RoleBootstrapHarness, tmp_path: Path
) -> tuple[TenantId, Path]:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    with role_harness.owner_engine.begin() as conn:
        tenant = TenantId(_tenant(conn))
        for code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant, code=code)
    return tenant, package


@pytest.fixture
def registered_candidates(
    role_harness: _RoleBootstrapHarness, candidate_seed: tuple[TenantId, Path]
) -> tuple[TenantId, UUID]:
    tenant, package = candidate_seed
    alternate = uuid4()
    with role_harness.owner_engine.begin() as conn:
        _import_candidates(conn, tenant, package)
        conn.execute(
            sa.text(
                "INSERT INTO basins (id,code,name,geometry,network,package_id) "
                "SELECT :id,'alternate-candidate','Alternate test "
                "basin',geometry,network,package_id "
                "FROM basins WHERE id=(SELECT basin_id FROM stations WHERE code='225')"
            ),
            {"id": alternate},
        )
    return tenant, alternate


def _candidate_has_lineage(conn: sa.Connection) -> bool:
    return (
        conn.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM stations s JOIN basins b ON "
                "b.id=s.basin_id "
                "JOIN basin_static_packages p ON p.package_id=b.package_id "
                "JOIN recap_gateway_polygon_bindings r ON r.station_id=s.id "
                "AND r.basin_id=b.id AND r.package_id=p.package_id "
                "WHERE s.code='225' AND s.network='dhm' AND b.network='dhm' "
                "AND p.fingerprint IS NOT NULL AND p.checksums ? 'basins.gpkg' "
                "AND s.name=format('DHM %s [TEST package outlet candidate: "
                "%s]',s.code,p.package_id))"
            )
        )
        is True
    )


def _finish(
    conn: sa.Connection,
    statement: str | None = None,
    parameters: dict[str, object] | None = None,
    *,
    refusal: Literal["lock_conflict", "isolation"] = "lock_conflict",
) -> Literal["committed", "refused"]:
    try:
        if statement is not None:
            conn.execute(sa.text(statement), parameters or {})
        conn.commit()
    except DBAPIError as exc:
        conn.rollback()
        state = getattr(exc.orig, "sqlstate", None)
        if refusal == "lock_conflict" and state == "55P03":
            return "refused"
        if (
            refusal == "isolation"
            and state == "P0001"
            and "protected candidate writes require READ COMMITTED" in str(exc)
        ):
            return "refused"
        raise
    return "committed"


@pytest.mark.parametrize("mutation", ["rename", "rename_and_detach"])
def test_worker_cannot_erase_candidate_identity(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
    mutation: Literal["rename", "rename_and_detach"],
) -> None:
    with (
        pytest.raises(DBAPIError, match="candidate marker cannot be removed"),
        role_harness.owner_engine.begin() as conn,
    ):
        _as_role(conn, "sapphire_worker")
        assignment = (
            "name='Ordinary station'"
            if mutation == "rename"
            else "name='Ordinary station',basin_id=NULL"
        )
        conn.execute(sa.text(f"UPDATE stations SET {assignment} WHERE code='225'"))


def test_worker_invisible_move_cannot_race_basin_invalidation(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    _, alternate = registered_candidates
    with (
        role_harness.owner_engine.connect() as mover,
        role_harness.owner_engine.connect() as invalidator,
    ):
        _as_role(mover, "sapphire_worker")
        mover.execute(
            sa.text("UPDATE stations SET basin_id=:id WHERE code='225'"),
            {"id": alternate},
        )
        mover.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET basin_id=:id "
                "WHERE station_id=(SELECT id FROM stations WHERE code='225')"
            ),
            {"id": alternate},
        )
        mover.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        _as_role(invalidator, "sapphire_worker")
        outcome = _finish(
            invalidator,
            "UPDATE basins SET package_id=NULL WHERE id=:id",
            {"id": alternate},
        )
        mover.commit()
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn), (
            f"dependency mutation {outcome} left invalid final lineage"
        )


def test_normal_static_prelock_blocks_concurrent_static_move(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    tenant, alternate = registered_candidates
    with (
        role_harness.owner_engine.connect() as mover,
        role_harness.owner_engine.connect() as invalidator,
    ):
        _as_role(mover, "sapphire_static_importer")
        mover.execute(
            sa.text("SELECT sf_lock_nepal_static_guard(:tenant)"), {"tenant": tenant}
        )
        mover.execute(
            sa.text("UPDATE stations SET basin_id=:id WHERE code='225'"),
            {"id": alternate},
        )
        mover.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET basin_id=:id "
                "WHERE station_id=(SELECT id FROM stations WHERE code='225')"
            ),
            {"id": alternate},
        )
        mover.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        _as_role(invalidator, "sapphire_static_importer")
        with pytest.raises(DBAPIError, match="lock timeout"):
            invalidator.execute(
                sa.text("SELECT sf_lock_nepal_static_guard(:tenant)"),
                {"tenant": tenant},
            )
        invalidator.rollback()
        mover.commit()
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn)


def test_repeatable_read_direct_binding_updates_cannot_write_skew(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    with role_harness.owner_engine.begin() as conn:
        _as_role(conn, "sapphire_static_importer")
        conn.execute(
            sa.text(
                "INSERT INTO recap_gateway_polygon_bindings "
                "(station_id,basin_id,gateway_hru_name,name,spatial_type,band"
                "_id,package_id) "
                "SELECT station_id,basin_id,gateway_hru_name,'alternate-band'"
                ",'elevation_band',1,package_id "
                "FROM recap_gateway_polygon_bindings WHERE "
                "station_id=(SELECT id FROM stations WHERE code='225')"
            )
        )
    with (
        role_harness.owner_engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as first,
        role_harness.owner_engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as second,
    ):
        for conn in (first, second):
            _as_role(conn, "sapphire_static_importer")
            assert (
                conn.scalar(sa.text("SHOW transaction_isolation")) == "repeatable read"
            )
            assert _candidate_has_lineage(conn)
        first.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET package_id=NULL "
                "WHERE station_id=(SELECT id FROM stations WHERE code='225') "
                "AND spatial_type='basin_average'"
            )
        )
        second.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET package_id=NULL "
                "WHERE station_id=(SELECT id FROM stations WHERE code='225') "
                "AND spatial_type='elevation_band'"
            )
        )
        outcomes = [
            _finish(first, refusal="isolation"),
            _finish(second, refusal="isolation"),
        ]
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn), (
            f"RR outcomes {outcomes} left invalid final lineage"
        )
    assert outcomes == ["refused", "refused"]


def test_uncommitted_candidates_cannot_lose_preexisting_package(
    role_harness: _RoleBootstrapHarness,
    candidate_seed: tuple[TenantId, Path],
) -> None:
    tenant, package = candidate_seed
    with role_harness.owner_engine.begin() as conn:
        stations = {
            row.code: StationId(row.id)
            for row in conn.execute(
                sa.text("SELECT id,code FROM stations WHERE network='dhm'")
            )
        }
        report = import_loaded_basin_package(
            conn,
            load_basin_package(package),
            resolve_station=lambda code, network: (
                stations.get(code) if network == "dhm" else None
            ),
            clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
        assert len(report.accepted) == 6
        assert len(report.onboarding_held) == 15
    with (
        role_harness.owner_engine.connect() as importer,
        role_harness.owner_engine.connect() as invalidator,
    ):
        _import_candidates(importer, tenant, package)
        importer.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        _as_role(invalidator, "sapphire_static_importer")
        assert (
            invalidator.scalar(
                sa.text(
                    "SELECT count(*) FROM stations WHERE name LIKE '%[TEST "
                    "package outlet candidate:%'"
                )
            )
            == 0
        )
        outcome = _finish(
            invalidator,
            "UPDATE basin_static_packages SET fingerprint=NULL WHERE "
            "package_id='nepal21_20261012'",
        )
        importer.commit()
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn), (
            f"package mutation {outcome} left invalid final lineage"
        )


def test_new_lineage_helpers_are_not_public_or_worker_entrypoints(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    with role_harness.owner_engine.connect() as conn:
        _as_role(conn, "sapphire_worker")
        signatures = (
            "public.sf_assert_nepal21_candidate_lineage(uuid)",
            "public.sf_nepal21_candidate_commit_guard()",
            "public.sf_nepal21_candidate_dependency_guard()",
        )
        for signature in signatures:
            assert (
                conn.scalar(
                    sa.text("SELECT to_regprocedure(:signature)"),
                    {"signature": signature},
                )
                is not None
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                    "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                    "WHERE p.oid=to_regprocedure(:signature) AND a.grantee=0 "
                    "AND a.privilege_type='EXECUTE')"
                ),
                {"signature": signature},
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_function_privilege(session_user,:signature,'EXECUTE')"
                ),
                {"signature": signature},
            )
        with (
            pytest.raises(
                DBAPIError,
                match="permission denied for function "
                "sf_assert_nepal21_candidate_lineage",
            ),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.text(
                    "SELECT sf_assert_nepal21_candidate_lineage(id) FROM "
                    "stations WHERE code='225'"
                )
            )


def test_worker_can_restore_marker_before_final_check(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    with role_harness.owner_engine.begin() as conn:
        _as_role(conn, "sapphire_worker")
        name = conn.scalar(sa.text("SELECT name FROM stations WHERE code='225'"))
        conn.execute(
            sa.text("UPDATE stations SET name='Temporary label' WHERE code='225'")
        )
        conn.execute(
            sa.text("UPDATE stations SET name=:name WHERE code='225'"), {"name": name}
        )
        conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert _candidate_has_lineage(conn)


def test_dependency_first_conflict_refuses_late_check_without_waiting(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    _, alternate = registered_candidates
    with (
        role_harness.owner_engine.connect() as invalidator,
        role_harness.owner_engine.connect() as mover,
    ):
        _as_role(invalidator, "sapphire_worker")
        invalidator.execute(
            sa.text("UPDATE basins SET package_id=NULL WHERE id=:id"),
            {"id": alternate},
        )
        _as_role(mover, "sapphire_worker")
        mover.execute(sa.text("SET LOCAL lock_timeout='5s'"))
        mover.execute(
            sa.text("UPDATE stations SET basin_id=:id WHERE code='225'"),
            {"id": alternate},
        )
        mover.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET basin_id=:id "
                "WHERE station_id=(SELECT id FROM stations WHERE code='225')"
            ),
            {"id": alternate},
        )
        with pytest.raises(DBAPIError, match="could not obtain lock on row"):
            mover.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        mover.rollback()
        invalidator.commit()
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn)
        assert (
            conn.scalar(
                sa.text("SELECT package_id FROM basins WHERE id=:id"), {"id": alternate}
            )
            is None
        )


def test_worker_cannot_hide_candidate_marker_removal_by_changing_id(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
) -> None:
    with (
        pytest.raises(DBAPIError, match="candidate marker cannot be removed"),
        role_harness.owner_engine.begin() as conn,
    ):
        _as_role(conn, "sapphire_worker")
        conn.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET station_id="
                "(SELECT id FROM stations WHERE code='447'),"
                "spatial_type='elevation_band',band_id=1 "
                "WHERE station_id=(SELECT id FROM stations WHERE code='225')"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE stations SET name='Ordinary station',basin_id=NULL "
                "WHERE code='225'"
            )
        )
        conn.execute(
            sa.text("UPDATE stations SET id=:id WHERE code='225'"), {"id": uuid4()}
        )


def test_dependency_isolation_is_admitted_before_stale_candidate_scan(
    role_harness: _RoleBootstrapHarness,
    candidate_seed: tuple[TenantId, Path],
) -> None:
    tenant, package = candidate_seed
    with role_harness.owner_engine.begin() as conn:
        stations = {
            row.code: StationId(row.id)
            for row in conn.execute(
                sa.text("SELECT id,code FROM stations WHERE network='dhm'")
            )
        }
        report = import_loaded_basin_package(
            conn,
            load_basin_package(package),
            resolve_station=lambda code, network: (
                stations.get(code) if network == "dhm" else None
            ),
            clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
        assert len(report.accepted) == 6
    with role_harness.owner_engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as stale:
        _as_role(stale, "sapphire_static_importer")
        assert (
            stale.scalar(
                sa.text(
                    "SELECT count(*) FROM stations WHERE name LIKE '%[TEST "
                    "package outlet candidate:%'"
                )
            )
            == 0
        )
        assert (
            stale.scalar(
                sa.text(
                    "SELECT fingerprint FROM basin_static_packages WHERE "
                    "package_id='nepal21_20261012'"
                )
            )
            is not None
        )
        with role_harness.owner_engine.begin() as importer:
            _import_candidates(importer, tenant, package)
        outcome = _finish(
            stale,
            "UPDATE basin_static_packages SET fingerprint=NULL "
            "WHERE package_id='nepal21_20261012'",
            refusal="isolation",
        )
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn), (
            f"stale candidate scan {outcome} left invalid final lineage"
        )
    assert outcome == "refused"


@pytest.mark.parametrize("path", ["unrelated_rr", "legacy_dependency_rc"])
def test_existing_unrelated_and_read_committed_old6_paths_remain_allowed(
    role_harness: _RoleBootstrapHarness,
    registered_candidates: tuple[TenantId, UUID],
    path: Literal["unrelated_rr", "legacy_dependency_rc"],
) -> None:
    isolation = "REPEATABLE READ" if path == "unrelated_rr" else "READ COMMITTED"
    with role_harness.owner_engine.connect().execution_options(
        isolation_level=isolation
    ) as conn:
        _as_role(conn, "sapphire_static_importer")
        if path == "unrelated_rr":
            conn.execute(
                sa.text(
                    "UPDATE basins SET name='Legacy metadata edit' "
                    "WHERE id=(SELECT basin_id FROM stations WHERE code='447')"
                )
            )
        else:
            conn.execute(
                sa.text(
                    "UPDATE basins SET package_id=NULL "
                    "WHERE id=(SELECT basin_id FROM stations WHERE code='447')"
                )
            )
        conn.commit()
    with role_harness.owner_engine.connect() as conn:
        assert _candidate_has_lineage(conn)
        expected = "Legacy metadata edit" if path == "unrelated_rr" else None
        column = "name" if path == "unrelated_rr" else "package_id"
        assert (
            conn.scalar(
                sa.text(
                    f"SELECT {column} FROM basins "
                    "WHERE id=(SELECT basin_id FROM stations WHERE code='447')"
                )
            )
            == expected
        )
