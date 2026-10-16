from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.cli.import_basin_package import run_nepal_static_import_transaction
from sapphire_flow.services.nepal21_delivery_authority import OLD6_STATION_CODES
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
    _tenant_with_code,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.services.test_nepal21_static_import import (
    _copy_full21_static_package,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )


def test_registers_only_missing_new15_with_package_lineage_and_no_activation(
    role_harness: _RoleBootstrapHarness, tmp_path: Path
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    with role_harness.owner_engine.connect() as conn:
        conn.begin()
        tenant_id = TenantId(_tenant(conn))
        for code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant_id, code=code)
        before = conn.execute(
            sa.text(
                "SELECT code, to_jsonb(s) - 'basin_id' FROM stations s ORDER BY code"
            )
        ).all()
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        assert conn.scalar(sa.text("SELECT session_user")) == "sapphire_static_importer"
        first_import_state: dict[str, list[object]] | None = None
        for _ in range(2):
            report = run_nepal_static_import_transaction(
                conn,
                tenant_id=tenant_id,
                package_dir=package,
                expected_package_id="nepal21_20261012",
                expected_hru="nepal21_20261012",
                clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
            assert len(report.accepted) == 21
            current_state = {
                table: list(
                    conn.execute(
                        sa.text(
                            f"SELECT to_jsonb(t) FROM {table} t "
                            "ORDER BY to_jsonb(t)::text"
                        )
                    )
                    .scalars()
                    .all()
                )
                for table in (
                    "stations",
                    "basins",
                    "basin_versions",
                    "basin_static_packages",
                    "recap_gateway_polygon_bindings",
                )
            }
            if first_import_state is None:
                first_import_state = current_state
            else:
                assert current_state == first_import_state
        conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        rows = (
            conn.execute(sa.text("SELECT to_jsonb(s) FROM stations s ORDER BY code"))
            .scalars()
            .all()
        )
        assert len(rows) == 21
        assert [
            (r["code"], {k: v for k, v in r.items() if k != "basin_id"})
            for r in rows
            if r["code"] in OLD6_STATION_CODES
        ] == before
        candidates = [r for r in rows if r["code"] not in OLD6_STATION_CODES]
        assert len(candidates) == 15
        for row in candidates:
            assert (
                row["name"] == f"DHM {row['code']} [TEST package outlet candidate: "
                "nepal21_20261012]"
            )
            assert row["station_status"] == "onboarding"
            assert row["gauging_status"] == "ungauged"
            assert row["forecast_targets"] is None
            assert row["measured_parameters"] == []
            assert row["water_level_datum_masl"] is None
            assert row["water_level_unit"] is None
            assert row["altitude_masl"] is None
            assert row["wigos_id"] is None
        assert conn.scalar(sa.text("SELECT count(*) FROM station_weather_sources")) == 0
        assert conn.scalar(sa.text("SELECT count(*) FROM model_assignments")) == 0
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM stations s "
                    "JOIN basins b ON b.id=s.basin_id "
                    "JOIN basin_static_packages p ON p.package_id=b.package_id "
                    "WHERE p.fingerprint IS NOT NULL"
                )
            )
            == 21
        )


@pytest.mark.parametrize(
    ("code", "lon", "reason"),
    [
        ("999", 85.0, "valid new15 package coordinates"),
        ("447", 85.0, "valid new15 package coordinates"),
        ("225", 181.0, "valid new15 package coordinates"),
        ("225", 85.0, "matching fingerprinted package lineage"),
    ],
)
def test_scoped_registrar_refuses_invalid_or_unlinked_candidates(
    role_harness: _RoleBootstrapHarness, code: str, lon: float, reason: str
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    with role_harness.owner_engine.connect() as conn:
        conn.begin()
        tenant_id = _tenant(conn)
        for old_code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant_id, code=old_code)
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        with pytest.raises(DBAPIError, match=reason), conn.begin_nested():
            conn.execute(
                sa.text(
                    "SELECT public.sf_register_nepal21_candidate("
                    ":id, :tenant, :code, :lon, 27.0, 'unlinked', :created)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "code": code,
                    "lon": lon,
                    "created": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert conn.scalar(sa.text("SELECT count(*) FROM stations")) == 6
        with (
            pytest.raises(DBAPIError, match="permission denied for table stations"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.text("INSERT INTO stations (id) VALUES (:id)"), {"id": uuid.uuid4()}
            )


@pytest.mark.parametrize("conflict", ["missing_old6", "foreign_new15"])
def test_registration_preflight_refuses_identity_conflicts_without_writes(
    role_harness: _RoleBootstrapHarness, tmp_path: Path, conflict: str
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    with role_harness.owner_engine.connect() as conn:
        conn.begin()
        tenant_id = TenantId(_tenant(conn))
        codes = sorted(OLD6_STATION_CODES)
        for code in codes[1:] if conflict == "missing_old6" else codes:
            _station(conn, tenant_id, code=code)
        if conflict == "foreign_new15":
            foreign_tenant = _tenant_with_code(conn, "foreign")
            _station(conn, foreign_tenant, code="225")
        before = (
            conn.execute(sa.text("SELECT to_jsonb(s) FROM stations s ORDER BY code"))
            .scalars()
            .all()
        )
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        reason = "all old6" if conflict == "missing_old6" else "different tenant"
        with pytest.raises(ValueError, match=reason):
            run_nepal_static_import_transaction(
                conn,
                tenant_id=tenant_id,
                package_dir=package,
                expected_package_id="nepal21_20261012",
                expected_hru="nepal21_20261012",
                clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
        assert (
            conn.execute(sa.text("SELECT to_jsonb(s) FROM stations s ORDER BY code"))
            .scalars()
            .all()
            == before
        )
        assert conn.scalar(sa.text("SELECT count(*) FROM basin_static_packages")) == 0


def test_onboarding_hold_rolls_back_new15_and_partial_package_import(
    role_harness: _RoleBootstrapHarness, tmp_path: Path
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    validation_path = package / "validation_report.json"
    validation = json.loads(validation_path.read_text())
    held = next(row for row in validation["basins"] if row["station_code"] == "225")
    held["checks"]["coverage_status"] = "outside"
    held["status"] = "failed"
    held["errors"] = ["Synthetic basin outside required coverage"]
    validation["summary"]["passed"] = 20
    validation["summary"]["failed"] = 1
    validation_path.write_text(json.dumps(validation))
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["checksums"]["validation_report.json"] = (
        "sha256:" + hashlib.sha256(validation_path.read_bytes()).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest))
    (package / "checksums.sha256").write_text(
        "".join(
            f"{value.removeprefix('sha256:')}  {name}\n"
            for name, value in manifest["checksums"].items()
        )
    )
    with role_harness.owner_engine.connect() as conn:
        conn.begin()
        tenant_id = TenantId(_tenant(conn))
        for code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant_id, code=code)
        before = (
            conn.execute(sa.text("SELECT to_jsonb(s) FROM stations s ORDER BY code"))
            .scalars()
            .all()
        )
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        with pytest.raises(
            ValueError, match="all 21 registered stations without onboarding holds"
        ):
            run_nepal_static_import_transaction(
                conn,
                tenant_id=tenant_id,
                package_dir=package,
                expected_package_id="nepal21_20261012",
                expected_hru="nepal21_20261012",
                clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
        conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
        assert (
            conn.execute(sa.text("SELECT to_jsonb(s) FROM stations s ORDER BY code"))
            .scalars()
            .all()
            == before
        )
        for table in (
            "basins",
            "basin_versions",
            "basin_static_packages",
            "recap_gateway_polygon_bindings",
            "rating_curves",
            "measurement_feed_evidence",
            "rating_reference_proofs",
            "provisional_discharges",
        ):
            assert conn.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 0
