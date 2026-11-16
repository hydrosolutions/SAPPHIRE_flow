from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from tests.integration.db.test_forecast_rating_history_roles import _bootstrap
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_station_history import (
    read,
    reader,
    reader_request,
    seed,
)

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness

ENTRY = "public.station_rating_history(uuid,uuid,timestamptz,timestamptz,boolean)"


def test_api_only_projection_with_raw_evidence_denied(
    role_harness: _RoleBootstrapHarness,
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.connect() as conn, conn.begin():
        row = seed(conn, 2)
        expected = read(conn, row)
        for role in (
            "sapphire_api",
            "sapphire_worker",
            "sapphire_operator",
            "sapphire_publication_health",
        ):
            assert conn.scalar(
                sa.text("SELECT has_function_privilege(:role,:entry,'EXECUTE')"),
                {"role": role, "entry": ENTRY},
            ) is (role == "sapphire_api")
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert read(conn, row) == expected
        assert (
            reader(conn, row).read(reader_request(row)).series[2].points[0].value
            == row["discharge"]
        )
        for table in (
            "rating_derived_discharge",
            "native_measurement_captures",
            "rating_reference_proofs",
        ):
            with (
                pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.text(f"SELECT * FROM public.{table}"))


def test_projection_is_stable_bounded_definer_without_public_execute(
    role_harness: _RoleBootstrapHarness,
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.connect() as conn:
        function = conn.execute(
            sa.text(
                "SELECT provolatile,prosecdef,proconfig FROM pg_proc "
                "WHERE oid=to_regprocedure(:entry)"
            ),
            {"entry": ENTRY},
        ).one()
        assert function.provolatile == "s" and function.prosecdef
        assert "search_path=pg_catalog, pg_temp" in function.proconfig
        assert not conn.scalar(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_proc p, LATERAL aclexplode(p.proacl) a "
                "WHERE p.oid=to_regprocedure(:entry) AND a.grantee=0 "
                "AND a.privilege_type='EXECUTE')"
            ),
            {"entry": ENTRY},
        )


def test_migration_downgrade_upgrade_leaves_records_intact(
    role_harness: _RoleBootstrapHarness,
) -> None:
    from runpy import run_path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = run_path("alembic/versions/0085_station_rating_history.py")
    with role_harness.owner_engine.connect() as conn, conn.begin():
        row = seed(conn)
        expected = read(conn, row)
        with Operations.context(MigrationContext.configure(conn)):
            migration["downgrade"]()
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT to_regclass("
                        "'public.ix_rating_derived_observation_latest')"
                    )
                )
                is None
            )
            assert (
                conn.scalar(sa.text("SELECT to_regprocedure(:entry)"), {"entry": ENTRY})
                is None
            )
            migration["upgrade"]()
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT to_regclass("
                        "'public.ix_rating_derived_observation_latest')"
                    )
                )
                is not None
            )
        assert read(conn, row) == expected
