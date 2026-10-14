from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )

from tests.integration.db.test_migration_provisional_discharge import (
    TABLES as PROVISIONAL_TABLES,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness

NEPAL21_TABLES = (
    "restricted_delivery_releases",
    "restricted_delivery_release_witnesses",
    "restricted_delivery_generations",
    "dhm_delivery_origins",
    "dhm_origin_sets",
    "dhm_origin_set_members",
    "dhm_rating_declarations",
    "dhm_delivery_observation_attributions",
    "expired_rating_test_permissions",
)
TABLES = (*PROVISIONAL_TABLES, "forecast_input_stations")


class TestProvisionalDischargeRoleBoundary:
    @pytest.mark.parametrize(
        "role", ["sapphire_api", "sapphire_worker", "sapphire_operator"]
    )
    def test_real_login_cannot_read_or_write_protected_data(
        self, role_harness: _RoleBootstrapHarness, role: str
    ) -> None:
        result = role_harness.run_bootstrap(
            "api-fixture", "worker-fixture", operator_password="operator-fixture"
        )
        assert result.returncode == 0, result.stderr
        password = {
            "sapphire_api": "api-fixture",
            "sapphire_worker": "worker-fixture",
            "sapphire_operator": "operator-fixture",
        }[role]
        url = role_harness.role_url(role, password)
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                assert conn.scalar(sa.text("SELECT session_user")) == role
                if role != "sapphire_operator":
                    assert (
                        conn.execute(
                            sa.text(
                                "SELECT tenant_id, state "
                                "FROM provisional_discharge_permissions"
                            )
                        ).all()
                        == []
                    )
            for state in ("enabled", "disabled"):
                assert role_harness.denied(
                    url,
                    "UPDATE public.provisional_discharge_permissions SET "
                    f"state='{state}'",
                )
            for table in TABLES:
                assert role_harness.denied(url, f"SELECT * FROM {table}")
                assert role_harness.denied(url, f"INSERT INTO {table} DEFAULT VALUES")
                assert role_harness.denied(url, f"DELETE FROM {table}")
                assert role_harness.denied(url, f"TRUNCATE {table} CASCADE")
        finally:
            engine.dispose()

    def test_dhm21_writer_role_is_dormant_and_release_immutable_scoped(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert conn.scalar(
                sa.text(
                    "SELECT NOT rolcanlogin FROM pg_catalog.pg_roles "
                    "WHERE rolname='sapphire_dhm21_delivery_writer'"
                )
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_table_privilege "
                    "('sapphire_dhm21_delivery_writer', "
                    "'public.restricted_delivery_generations', 'INSERT')"
                )
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_table_privilege "
                    "('sapphire_dhm21_delivery_writer', "
                    "'public.restricted_delivery_generations', 'UPDATE')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege "
                    "('sapphire_dhm21_delivery_writer', "
                    "'public.restricted_delivery_releases', 'UPDATE')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege "
                    "('sapphire_dhm21_delivery_writer', "
                    "'public.expired_rating_test_permissions', 'INSERT')"
                )
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_function_privilege "
                    "('sapphire_dhm21_delivery_writer', "
                    "'public.sf_lock_dhm21_delivery_write(uuid,uuid)', 'EXECUTE')"
                )
            )

    def test_static_importer_role_is_dormant_and_static_scoped(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert conn.scalar(
                sa.text(
                    "SELECT NOT rolcanlogin FROM pg_catalog.pg_roles "
                    "WHERE rolname='sapphire_static_importer'"
                )
            )
            for table in NEPAL21_TABLES:
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege "
                        "('sapphire_static_importer', :table, 'SELECT')"
                    ),
                    {"table": f"public.{table}"},
                )
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege "
                        "('sapphire_static_importer', :table, 'INSERT')"
                    ),
                    {"table": f"public.{table}"},
                )
            assert conn.scalar(
                sa.text(
                    "SELECT has_table_privilege "
                    "('sapphire_static_importer', "
                    "'public.basin_static_packages', 'INSERT')"
                )
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_function_privilege "
                    "('sapphire_static_importer', "
                    "'public.sf_lock_nepal_static_guard(uuid)', 'EXECUTE')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege "
                    "('sapphire_static_importer', 'public.observations', 'INSERT')"
                )
            )

    def test_rebootstrap_revokes_stale_nepal21_role_grants(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "GRANT UPDATE ON restricted_delivery_releases "
                    "TO sapphire_dhm21_delivery_writer"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT INSERT ON expired_rating_test_permissions "
                    "TO sapphire_dhm21_delivery_writer"
                )
            )
            conn.execute(
                sa.text("GRANT INSERT ON observations TO sapphire_static_importer")
            )
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege("
                    "'sapphire_dhm21_delivery_writer', "
                    "'public.restricted_delivery_releases', 'UPDATE')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege("
                    "'sapphire_dhm21_delivery_writer', "
                    "'public.expired_rating_test_permissions', 'INSERT')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege("
                    "'sapphire_static_importer', 'public.observations', 'INSERT')"
                )
            )

    def test_rebootstrap_revokes_stale_column_grants(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        role_harness.run_bootstrap("api-fixture", "worker-fixture")
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "GRANT SELECT(content), INSERT(content), UPDATE(content) "
                    "ON measurement_feed_evidence TO sapphire_api"
                )
            )
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            for privilege in ("SELECT", "INSERT", "UPDATE"):
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege('sapphire_api', "
                        "'measurement_feed_evidence', 'content', :privilege)"
                    ),
                    {"privilege": privilege},
                )

    @pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
    @pytest.mark.parametrize("stale_grant", ["none", "column", "public_column"])
    def test_failed_backup_preflight_never_exposes_protected_canary(
        self,
        role_harness: _RoleBootstrapHarness,
        role: str,
        stale_grant: Literal["none", "column", "public_column"],
    ) -> None:
        from tests.integration.store.test_provisional_discharge_store import seed

        first = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert first.returncode == 0, first.stderr
        with role_harness.owner_engine.begin() as conn:
            _, _, feed, _ = seed(conn)
            if stale_grant != "none":
                grantee = "PUBLIC" if stale_grant == "public_column" else role
                conn.execute(
                    sa.text(
                        "GRANT SELECT(content), INSERT(content), UPDATE(content) "
                        f"ON public.measurement_feed_evidence TO {grantee}"
                    )
                )
            conn.execute(
                sa.text("CREATE TABLE public.protected_bootstrap_abort (id integer)")
            )
            conn.execute(
                sa.text(
                    "ALTER TABLE public.protected_bootstrap_abort OWNER TO "
                    "sapphire_backup"
                )
            )
        try:
            failed = role_harness.run_bootstrap("api-fixture", "worker-fixture")
            assert failed.returncode != 0
            assert "sapphire_backup owns" in failed.stderr
            password = "api-fixture" if role == "sapphire_api" else "worker-fixture"
            url = role_harness.role_url(role, password)
            engine = sa.create_engine(url)
            try:
                with engine.connect() as conn:
                    assert conn.scalar(sa.text("SELECT session_user")) == role
                    with pytest.raises(DBAPIError, match="permission denied"):
                        conn.scalar(
                            sa.text(
                                "SELECT content FROM "
                                "public.measurement_feed_evidence WHERE id=:id"
                            ),
                            {"id": feed.id},
                        )
                for table in TABLES:
                    assert role_harness.denied(url, f"SELECT * FROM public.{table}")
                    assert role_harness.denied(
                        url, f"INSERT INTO public.{table} DEFAULT VALUES"
                    )
                    assert role_harness.denied(url, f"DELETE FROM public.{table}")
            finally:
                engine.dispose()
        finally:
            with role_harness.owner_engine.begin() as conn:
                conn.execute(sa.text("DROP TABLE public.protected_bootstrap_abort"))

    def test_catalog_grant_keeps_previous_relation_scope(
        self,
        role_harness: _RoleBootstrapHarness,
    ) -> None:
        initial = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert initial.returncode == 0, initial.stderr
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("CREATE ROLE ordinary_grant_baseline NOLOGIN"))
            conn.execute(
                sa.text(
                    "CREATE TABLE public.grant_scope_parent (id integer) PARTITION "
                    "BY RANGE (id)"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TABLE public.grant_scope_child PARTITION OF "
                    "public.grant_scope_parent FOR VALUES FROM (0) TO (10)"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE VIEW public.grant_scope_view AS SELECT * FROM "
                    "public.grant_scope_parent"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE MATERIALIZED VIEW public.grant_scope_materialized AS "
                    "SELECT * FROM public.grant_scope_parent"
                )
            )
            conn.execute(sa.text("CREATE SCHEMA grant_scope_other"))
            conn.execute(sa.text("CREATE TABLE grant_scope_other.hidden (id integer)"))
            conn.execute(
                sa.text(
                    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO "
                    "ordinary_grant_baseline"
                )
            )
        try:
            result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
            assert result.returncode == 0, result.stderr
            with role_harness.owner_engine.connect() as conn:
                rows = (
                    conn.execute(
                        sa.text(
                            "SELECT c.relname, "
                            "has_table_privilege('ordinary_grant_baseline', c.oid, "
                            "'SELECT') AS prior, "
                            "has_table_privilege('sapphire_api', c.oid, 'SELECT') "
                            "AS current "
                            "FROM pg_class c JOIN pg_namespace n ON "
                            "n.oid=c.relnamespace "
                            "WHERE n.nspname='public' AND c.relkind IN "
                            "('r','p','v','m','f')"
                        )
                    )
                    .mappings()
                    .all()
                )
                table_grant_exclusions = {
                    *TABLES,
                    *NEPAL21_TABLES,
                    "forecasts",
                    "rejected_forecasts",
                }
                assert all(
                    row["current"]
                    == (row["prior"] and row["relname"] not in table_grant_exclusions)
                    for row in rows
                )
                from sapphire_flow.db.metadata import forecasts, rejected_forecasts

                for table in (forecasts, rejected_forecasts):
                    for column in table.columns:
                        allowed = conn.scalar(
                            sa.text(
                                "SELECT has_column_privilege('sapphire_api', "
                                ":table, :column, 'SELECT')"
                            ),
                            {"table": table.name, "column": column.name},
                        )
                        assert allowed is (column.name != "input_lineage")
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege('sapphire_api', "
                        "'grant_scope_other.hidden', 'SELECT')"
                    )
                )
        finally:
            with role_harness.owner_engine.begin() as conn:
                conn.execute(sa.text("DROP OWNED BY ordinary_grant_baseline"))
                conn.execute(sa.text("DROP ROLE ordinary_grant_baseline"))
                conn.execute(sa.text("DROP TABLE public.grant_scope_parent CASCADE"))
                conn.execute(sa.text("DROP SCHEMA grant_scope_other CASCADE"))

    @pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
    def test_nonowner_cannot_disable_even_with_fixture_update_grant(
        self,
        role_harness: _RoleBootstrapHarness,
        role: str,
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO public.provisional_discharge_permissions "
                    "(tenant_id, state, permission_reference, inventory_digest) "
                    "SELECT id, 'enabled', 'fixture-only', repeat('a',64) FROM "
                    "public.tenants "
                    "ON CONFLICT DO NOTHING"
                )
            )
            # Disposable test grant probes the owner-session trigger independently
            # of the production ACL denial. Production bootstrap grants no UPDATE.
            conn.execute(
                sa.text(
                    "GRANT UPDATE(state) ON "
                    f"public.provisional_discharge_permissions TO {role}"
                )
            )
        password = "api-fixture" if role == "sapphire_api" else "worker-fixture"
        engine = sa.create_engine(role_harness.role_url(role, password))
        try:
            with engine.connect() as conn:
                assert conn.scalar(sa.text("SELECT session_user")) == role
                with pytest.raises(DBAPIError, match="immutable except owner disable"):
                    conn.execute(
                        sa.text(
                            "UPDATE public.provisional_discharge_permissions SET "
                            "state='disabled'"
                        )
                    )
        finally:
            engine.dispose()
            with role_harness.owner_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "REVOKE UPDATE(state) ON "
                        f"public.provisional_discharge_permissions FROM {role}"
                    )
                )

    @pytest.mark.parametrize("abort", [False, True])
    @pytest.mark.parametrize("grantee", ["sapphire_api", "sapphire_worker", "PUBLIC"])
    def test_lineage_stale_acl_revoked_even_before_failed_preflight(
        self, role_harness: _RoleBootstrapHarness, abort: bool, grantee: str
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "GRANT SELECT, INSERT, UPDATE ON forecast_input_stations "
                    f"TO {grantee}"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT SELECT(station_id), INSERT(station_id), "
                    "UPDATE(station_id) ON forecast_input_stations "
                    f"TO {grantee}"
                )
            )
            if abort:
                conn.execute(
                    sa.text("CREATE TABLE public.lineage_bootstrap_abort (id integer)")
                )
                conn.execute(
                    sa.text(
                        "ALTER TABLE public.lineage_bootstrap_abort "
                        "OWNER TO sapphire_backup"
                    )
                )
        try:
            result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
            assert (result.returncode != 0) == abort, result.stderr
            for role, password in (
                ("sapphire_api", "api-fixture"),
                ("sapphire_worker", "worker-fixture"),
            ):
                url = role_harness.role_url(role, password)
                assert role_harness.denied(
                    url, "SELECT station_id FROM forecast_input_stations"
                )
                assert role_harness.denied(
                    url,
                    "INSERT INTO forecast_input_stations (station_id) VALUES (NULL)",
                )
            with role_harness.owner_engine.connect() as conn:
                assert conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege('sapphire_backup', "
                        "'forecast_input_stations', 'SELECT')"
                    )
                )
        finally:
            if abort:
                with role_harness.owner_engine.begin() as conn:
                    conn.execute(sa.text("DROP TABLE public.lineage_bootstrap_abort"))
