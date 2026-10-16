from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )

from tests.integration.db.test_role_bootstrap import role_harness as role_harness


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
@pytest.mark.parametrize("table", ["forecasts", "rejected_forecasts"])
@pytest.mark.parametrize("projection", ["input_lineage", "*", "to_jsonb(t)"])
def test_runtime_cannot_select_protected_lineage(
    role_harness, role: str, table: str, projection: str
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    password = "api-fixture" if role == "sapphire_api" else "worker-fixture"
    assert role_harness.denied(
        role_harness.role_url(role, password),
        f"SELECT {projection} FROM public.{table} AS t",
    )


@pytest.mark.parametrize("projection", ["input_lineage", "to_jsonb(f) AS record"])
def test_bootstrap_refuses_owner_view_of_protected_content(
    role_harness, projection: str
) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE VIEW public.lineage_view_canary AS "
                f"SELECT {projection} FROM forecasts f"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "protected forecast lineage" in result.stderr
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP VIEW public.lineage_view_canary"))


def test_failed_later_preflight_leaves_raw_columns_denied(role_harness) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("GRANT SELECT ON forecasts, rejected_forecasts TO PUBLIC"))
        conn.execute(
            sa.text(
                "GRANT SELECT (input_lineage) ON forecasts, rejected_forecasts "
                "TO sapphire_api, sapphire_worker"
            )
        )
        conn.execute(sa.text("CREATE TABLE public.lineage_abort_canary (id integer)"))
        conn.execute(
            sa.text("ALTER TABLE public.lineage_abort_canary OWNER TO sapphire_backup")
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        for role, password in [
            ("sapphire_api", "api-fixture"),
            ("sapphire_worker", "worker-fixture"),
        ]:
            for table in ["forecasts", "rejected_forecasts"]:
                assert role_harness.denied(
                    role_harness.role_url(role, password),
                    f"SELECT input_lineage FROM {table}",
                )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE public.lineage_abort_canary"))
            conn.execute(
                sa.text("REVOKE SELECT ON forecasts, rejected_forecasts FROM PUBLIC")
            )


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
def test_standard_read_projection_and_worker_retry(role_harness, role: str) -> None:
    import sqlalchemy as sa

    from sapphire_flow.store.forecast_store import PgForecastStore
    from tests.integration.store.test_forecast_data_use import _pair
    from tests.integration.store.test_forecast_store import savepoint_factory

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.connect() as conn, conn.begin():
        standard, _ = _pair(conn)
        store = PgForecastStore(conn, transaction_factory=savepoint_factory(conn))
        store.store_forecast(standard)
        import threading
        from datetime import timedelta

        from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
        from tests.integration.store.test_rejected_forecast_store import _NOW, _entry

        rejected_store = PgRejectedForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        )
        entry = _entry(station_id=standard.station_id, model_id=standard.model_id)
        rejected_store.write_batch([entry], abandon=threading.Event())
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        rejected, count = rejected_store.fetch_rejected_forecasts(
            standard.station_id, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        assert count == 1
        assert rejected[0].input_lineage is None
        loaded = store.fetch_forecast(standard.id)
        assert loaded is not None
        assert loaded.id == standard.id
        assert loaded.input_lineage is None
        assert loaded.ensemble.values.equals(standard.ensemble.values)
        if role == "sapphire_worker":
            assert store.store_forecast(standard) == standard.id
        conn.rollback()


def test_unknown_definer_refused_without_mutating_it(role_harness) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE FUNCTION public.lineage_dynamic_canary() "
                "RETURNS text LANGUAGE plpgsql SECURITY DEFINER AS $$ "
                "BEGIN RETURN (SELECT input_lineage FROM forecasts LIMIT 1); END $$"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "unknown definer authority" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert conn.scalar(
                sa.text(
                    "SELECT to_regprocedure('public.lineage_dynamic_canary()') "
                    "IS NOT NULL"
                )
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP FUNCTION public.lineage_dynamic_canary()"))


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
@pytest.mark.parametrize("kind", ["opaque", "parsed", "view"])
def test_live_runtime_temp_objects_do_not_abort_preflights(
    role_harness, role: str, kind: str
) -> None:
    import sqlalchemy as sa
    from alembic.config import Config

    from alembic import command

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    password = "api-fixture" if role == "sapphire_api" else "worker-fixture"
    engine = sa.create_engine(role_harness.role_url(role, password))
    statements = {
        "opaque": "CREATE FUNCTION pg_temp.runtime_probe() RETURNS text "
        "LANGUAGE sql SECURITY DEFINER AS 'SELECT current_user::text'",
        "parsed": "CREATE FUNCTION pg_temp.runtime_probe() RETURNS SETOF text "
        "LANGUAGE sql SECURITY DEFINER BEGIN ATOMIC "
        "SELECT input_lineage FROM public.forecasts; END",
        "view": "CREATE TEMP VIEW runtime_probe AS SELECT "
        "input_lineage FROM public.forecasts",
    }
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(statements[kind]))
            conn.commit()
            if kind != "opaque":
                with (
                    pytest.raises(sa.exc.DBAPIError, match="permission denied"),
                    conn.begin_nested(),
                ):
                    conn.execute(
                        sa.text(
                            "SELECT * FROM pg_temp.runtime_probe"
                            + ("()" if kind == "parsed" else "")
                        )
                    )
                conn.rollback()
            result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
            assert result.returncode == 0, result.stderr
            conn.execute(sa.text("SELECT id FROM stations LIMIT 1"))
            conn.commit()
            config = Config("alembic.ini")
            command.downgrade(config, "0070")
            command.upgrade(config, "0071")
            conn.execute(sa.text("SELECT id FROM forecasts LIMIT 1"))
            conn.commit()
            with (
                pytest.raises(sa.exc.DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.text("SELECT input_lineage FROM forecasts"))
    finally:
        import sys

        original_error = sys.exc_info()[1]
        cleanup_errors: list[Exception] = []
        try:
            engine.dispose()
        except Exception as exc:
            cleanup_errors.append(exc)
        try:
            command.upgrade(Config("alembic.ini"), "head")
        except Exception as exc:
            cleanup_errors.append(exc)
        if original_error is not None:
            for exc in cleanup_errors:
                original_error.add_note(f"head cleanup failed: {exc!r}")
        elif cleanup_errors:
            raise ExceptionGroup("head cleanup failed", cleanup_errors)


def test_bootstrap_normalizes_stale_health_set_membership(role_harness) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE stale_health_reader NOLOGIN"))
        conn.execute(
            sa.text("GRANT SELECT(input_lineage) ON forecasts TO stale_health_reader")
        )
        conn.execute(
            sa.text(
                "GRANT stale_health_reader TO "
                "sapphire_publication_health WITH INHERIT FALSE, SET TRUE"
            )
        )
        before = conn.execute(
            sa.text(
                "SELECT rolcanlogin, rolpassword FROM pg_authid WHERE "
                "rolname='sapphire_publication_health'"
            )
        ).one()
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            after = conn.execute(
                sa.text(
                    "SELECT rolcanlogin, rolpassword FROM pg_authid WHERE "
                    "rolname='sapphire_publication_health'"
                )
            ).one()
            assert tuple(after) == tuple(before)
            assert not conn.scalar(
                sa.text(
                    "SELECT pg_has_role('sapphire_publication_health', "
                    "'stale_health_reader', 'MEMBER')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_column_privilege('sapphire_publication_health', "
                    "'forecasts', 'input_lineage', 'SELECT')"
                )
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_table_privilege('sapphire_api', 'stations', 'SELECT')"
                )
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text("REVOKE stale_health_reader FROM sapphire_publication_health")
            )
            conn.execute(sa.text("DROP OWNED BY stale_health_reader"))
            conn.execute(sa.text("DROP ROLE stale_health_reader"))


@pytest.mark.parametrize(
    "role",
    [
        "sapphire_api",
        "sapphire_worker",
        "sapphire_publication_health",
        "sapphire_operator",
    ],
)
@pytest.mark.parametrize("table", ["forecasts", "rejected_forecasts"])
def test_protected_columns_deny_copy_and_returning(
    role_harness, role: str, table: str
) -> None:
    import psycopg
    import sqlalchemy as sa

    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        for query in [
            f"SELECT input_lineage FROM {table}",
            f"UPDATE {table} SET id=id WHERE false RETURNING input_lineage",
            f"INSERT INTO {table} (id) VALUES (gen_random_uuid()) "
            f"RETURNING input_lineage",
        ]:
            with (
                pytest.raises(sa.exc.DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.text(query))
        with (
            pytest.raises(
                psycopg.errors.InsufficientPrivilege, match="permission denied"
            ),
            conn.begin_nested(),
            conn.connection.driver_connection.cursor() as cursor,
            cursor.copy(
                f"COPY (SELECT input_lineage FROM {table}) TO STDOUT"
            ) as copied,
        ):
            list(copied)


def test_worker_supersession_status_and_summaries_under_column_grants(
    role_harness,
) -> None:
    from dataclasses import replace
    from datetime import timedelta
    from uuid import uuid4

    import polars as pl
    import sqlalchemy as sa

    from sapphire_flow.types.enums import ForecastStatus
    from sapphire_flow.types.ids import ForecastId
    from tests.integration.store.test_forecast_data_use import _pair, _stores

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.connect() as conn, conn.begin():
        standard, _ = _pair(conn)
        store = _stores(conn)[0]
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        store.store_forecast(standard)
        replacement = replace(
            standard,
            id=ForecastId(uuid4()),
            ensemble=replace(
                standard.ensemble,
                values=standard.ensemble.values.with_columns(pl.col("value") + 1),
            ),
        )
        store.store_forecast(replacement)
        assert store.fetch_forecast(standard.id).status is ForecastStatus.SUPERSEDED
        assert store.transition_status(replacement.id, 1, ForecastStatus.REVIEWED) == 2
        summaries, count = store.fetch_forecast_summaries(
            standard.station_id,
            standard.issued_at - timedelta(days=1),
            standard.issued_at + timedelta(days=1),
        )
        assert count == 2
        assert {row.status for row in summaries} == {
            ForecastStatus.SUPERSEDED,
            ForecastStatus.REVIEWED,
        }
        conn.rollback()


def test_api_legacy_and_browser_routes_under_column_grants(role_harness) -> None:
    import sqlalchemy as sa

    from sapphire_flow.api.routes import tables
    from tests.integration.api.test_dashboard_forecasts import (
        _client,
        app_overrides_clear,
    )
    from tests.integration.store.test_forecast_data_use import _pair, _stores

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.connect() as conn, conn.begin():
        standard, _ = _pair(conn)
        _stores(conn)[0].store_forecast(standard)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        tables._reflected = None
        client = _client(conn)
        try:
            for path in [
                "/",
                "/forecasts/",
                f"/forecasts/{standard.id}/",
                f"/api/v1/forecasts/{standard.id}/data.json",
                "/tables/",
                "/tables/forecasts/",
                "/tables/forecasts/rows",
                "/tables/rejected_forecasts/",
                "/tables/forecast_evidence_blobs/",
            ]:
                response = client.get(path)
                assert response.status_code == 200, path
                assert ">input_lineage<" not in response.text
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_api"
        finally:
            client.close()
            app_overrides_clear()
            tables._reflected = None
        conn.rollback()


def test_safe_grants_match_current_projection_and_future_columns_fail_closed(
    role_harness,
) -> None:
    import sqlalchemy as sa

    from sapphire_flow.db.metadata import forecasts, rejected_forecasts
    from sapphire_flow.store.forecast_read import forecast_columns
    from sapphire_flow.types.enums import ForecastDataUse

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.connect() as conn, conn.begin():
        for table in [forecasts, rejected_forecasts]:
            projection = str(
                sa.select(*forecast_columns(table, ForecastDataUse.STANDARD))
            )
            assert f"{table.name}.input_lineage" not in projection
            for role in ["sapphire_api", "sapphire_worker"]:
                for column in table.columns:
                    allowed = conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege(:role, :table, :column, "
                            "'SELECT')"
                        ),
                        {"role": role, "table": table.name, "column": column.name},
                    )
                    assert allowed is (column.name != "input_lineage")
            conn.execute(
                sa.text(
                    f"ALTER TABLE {table.name} ADD COLUMN unreviewed_future_column text"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT has_column_privilege('sapphire_api', :table, "
                    "'unreviewed_future_column', 'SELECT')"
                ),
                {"table": table.name},
            )
        conn.rollback()


def test_permanent_dependency_tracked_definer_still_refused(role_harness) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE FUNCTION public.lineage_parsed_canary() "
                "RETURNS SETOF text LANGUAGE sql SECURITY DEFINER BEGIN ATOMIC "
                "SELECT input_lineage FROM public.forecasts; END"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "review dependent definer functions" in result.stderr
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP FUNCTION public.lineage_parsed_canary()"))


@pytest.mark.parametrize("route", ["set", "inherit", "mixed", "admin", "acting_admin"])
def test_private_function_future_grant_route_refused_before_later_failure(
    role_harness: _RoleBootstrapHarness, route: str
) -> None:
    import sqlalchemy as sa

    lock = "public.lock_publication_candidate(uuid)"
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE private_acl_bridge NOLOGIN INHERIT"))
        conn.execute(
            sa.text("CREATE TABLE public.private_acl_late_failure(id integer)")
        )
        conn.execute(
            sa.text(
                "ALTER TABLE public.private_acl_late_failure OWNER TO sapphire_backup"
            )
        )
        statements = {
            "set": [
                "GRANT sapphire_api TO sapphire_backup WITH ADMIN "
                "FALSE, INHERIT FALSE, SET TRUE"
            ],
            "inherit": [
                "GRANT sapphire_api TO sapphire_backup WITH ADMIN "
                "FALSE, INHERIT TRUE, SET FALSE"
            ],
            "admin": [
                "GRANT sapphire_api TO sapphire_backup WITH ADMIN TRUE,"
                " INHERIT FALSE, SET FALSE"
            ],
            "acting_admin": [
                "GRANT sapphire_api TO private_acl_bridge WITH ADMIN TRUE, "
                "INHERIT FALSE, SET FALSE",
                "GRANT private_acl_bridge TO sapphire_backup WITH ADMIN FALSE, "
                "INHERIT FALSE, SET TRUE",
            ],
            "mixed": [
                "GRANT sapphire_api TO private_acl_bridge WITH ADMIN "
                "FALSE, INHERIT TRUE, SET FALSE",
                "GRANT private_acl_bridge TO sapphire_backup WITH "
                "ADMIN FALSE, INHERIT FALSE, SET TRUE",
            ],
        }
        for statement in statements[route]:
            conn.execute(sa.text(statement))
        if route == "admin":
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT pg_has_role('sapphire_backup',"
                        "'sapphire_api','MEMBER WITH ADMIN OPTION')"
                    )
                )
                is True
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT pg_has_role('sapphire_backup','sapphire_api','SET')"
                    )
                )
                is False
            )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL forbidden route:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege('sapphire_api',"
                        "to_regprocedure(:fn),'EXECUTE')"
                    ),
                    {"fn": lock},
                )
                is False
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT "
                        "has_function_privilege('sapphire_backup',"
                        "to_regprocedure(:fn),'EXECUTE')"
                    ),
                    {"fn": lock},
                )
                is False
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_auth_members WHERE "
                        "member='sapphire_backup'::regrole "
                        "AND roleid IN ('sapphire_api'::regrole,"
                        "'private_acl_bridge'::regrole)"
                    )
                )
                == 1
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE public.private_acl_late_failure"))
            conn.execute(sa.text("REVOKE sapphire_api FROM sapphire_backup"))
            conn.execute(sa.text("REVOKE private_acl_bridge FROM sapphire_backup"))
            conn.execute(sa.text("REVOKE sapphire_api FROM private_acl_bridge"))
            conn.execute(sa.text("DROP ROLE private_acl_bridge"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


def test_private_function_managed_owner_refused(
    role_harness: _RoleBootstrapHarness,
) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "ALTER FUNCTION public.lock_publication_candidate(uuid)"
                " OWNER TO sapphire_worker"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL identity/owner:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT pg_get_userbyid(proowner) FROM pg_proc "
                        "WHERE "
                        "oid='public.lock_publication_candidate(uuid)"
                        "'::regprocedure"
                    )
                )
                == "sapphire_worker"
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "ALTER FUNCTION "
                    "public.lock_publication_candidate(uuid) OWNER TO "
                    "test"
                )
            )
            conn.execute(
                sa.text(
                    "REVOKE EXECUTE ON FUNCTION "
                    "public.lock_publication_candidate(uuid) FROM "
                    "sapphire_worker"
                )
            )
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


def _assert_private_public_denied(
    conn: sa.Connection, *, except_signature: str = ""
) -> None:
    from tests.integration.ops.mixed_restore_fixture import PRIVATE_DEFINER_EXECUTORS

    for signature in PRIVATE_DEFINER_EXECUTORS:
        if signature == except_signature:
            continue
        assert (
            conn.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM "
                    "aclexplode(COALESCE(proacl,acldefault('f',"
                    "proowner))) a "
                    "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') "
                    "FROM pg_proc WHERE oid=to_regprocedure(:signature)"
                ),
                {"signature": signature},
            ).scalar_one()
            is False
        )


def _private_acl_rows(conn: sa.Connection) -> list[tuple[str, str, bool]]:
    rows = conn.execute(
        sa.text(
            "SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_get_userbyid(a.grantee) END, "
            "pg_get_userbyid(a.grantor),a.is_grantable FROM pg_proc p "
            "CROSS JOIN LATERAL aclexplode(COALESCE(proacl,"
            "acldefault('f',proowner))) a "
            "WHERE p.oid='public.lock_publication_candidate(uuid)'::regprocedure"
        )
    ).all()
    return [(str(row[0]), str(row[1]), bool(row[2])) for row in rows]


def test_private_function_managed_onward_dependency_preserves_public_revoke(
    role_harness: _RoleBootstrapHarness,
) -> None:
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    lock = "public.lock_publication_candidate(uuid)"
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE private_acl_custom NOLOGIN"))
        conn.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {lock} TO PUBLIC"))
        conn.execute(
            sa.text(
                f"GRANT EXECUTE ON FUNCTION {lock} TO sapphire_worker WITH GRANT OPTION"
            )
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        conn.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {lock} TO private_acl_custom"))
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL managed revoke dependency:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            _assert_private_public_denied(conn)
            rows = _private_acl_rows(conn)
            assert ("private_acl_custom", "sapphire_worker", False) in rows
            assert ("sapphire_worker", "test", True) in rows
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            conn.execute(
                sa.text(f"REVOKE EXECUTE ON FUNCTION {lock} FROM private_acl_custom")
            )
            conn.execute(sa.text("RESET ROLE"))
            conn.execute(
                sa.text(f"REVOKE EXECUTE ON FUNCTION {lock} FROM sapphire_worker")
            )
            conn.execute(sa.text("DROP ROLE private_acl_custom"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


@pytest.mark.parametrize("recipient", ["managed", "public", "membership"])
def test_private_function_custom_grantor_refused_without_custom_mutation(
    role_harness: _RoleBootstrapHarness,
    recipient: str,
) -> None:
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    lock = "public.lock_publication_candidate(uuid)"
    target = "PUBLIC" if recipient == "public" else "sapphire_worker"
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE private_acl_custom NOLOGIN"))
        conn.execute(
            sa.text(
                f"GRANT EXECUTE ON FUNCTION {lock} TO "
                "private_acl_custom WITH GRANT OPTION"
            )
        )
        if recipient == "membership":
            conn.execute(
                sa.text(
                    "GRANT private_acl_custom TO sapphire_backup WITH "
                    "INHERIT FALSE, SET TRUE"
                )
            )
        conn.execute(sa.text("SET LOCAL ROLE private_acl_custom"))
        conn.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {lock} TO {target}"))
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL custom authority:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            rows = _private_acl_rows(conn)
            assert (target, "private_acl_custom", False) in rows
            assert ("private_acl_custom", "test", True) in rows
            _assert_private_public_denied(
                conn, except_signature=lock if recipient == "public" else ""
            )
            if recipient == "membership":
                assert (
                    conn.scalar(
                        sa.text(
                            "SELECT pg_has_role('sapphire_backup',"
                            "'private_acl_custom','SET')"
                        )
                    )
                    is True
                )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE private_acl_custom"))
            conn.execute(sa.text(f"REVOKE EXECUTE ON FUNCTION {lock} FROM {target}"))
            conn.execute(sa.text("RESET ROLE"))
            conn.execute(sa.text("REVOKE private_acl_custom FROM sapphire_backup"))
            conn.execute(
                sa.text(f"REVOKE EXECUTE ON FUNCTION {lock} FROM private_acl_custom")
            )
            conn.execute(sa.text("DROP ROLE private_acl_custom"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


@pytest.mark.parametrize("route", ["superuser", "owner_set", "owner_admin"])
def test_private_function_later_role_owner_authority_refused(
    role_harness: _RoleBootstrapHarness,
    route: str,
) -> None:
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    statement = {
        "superuser": "ALTER ROLE sapphire_backup SUPERUSER",
        "owner_set": "GRANT test TO sapphire_backup WITH ADMIN FALSE, INHERIT "
        "FALSE, SET TRUE",
        "owner_admin": "GRANT test TO sapphire_backup WITH ADMIN TRUE, INHERIT "
        "FALSE, SET FALSE",
    }[route]
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text(statement))
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL forbidden route:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            _assert_private_public_denied(conn)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege('sapphire_api',"
                        "'public.lock_publication_candidate(uuid)',"
                        "'EXECUTE')"
                    )
                )
                is False
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("ALTER ROLE sapphire_backup NOSUPERUSER"))
            conn.execute(sa.text("REVOKE test FROM sapphire_backup"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


@pytest.mark.parametrize(
    "change", ["missing", "overload", "schema", "invoker", "other_owner"]
)
def test_private_function_identity_drift_refused_without_repair(
    role_harness: _RoleBootstrapHarness,
    change: str,
) -> None:
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    lock = "public.lock_publication_candidate(uuid)"
    before: str
    with role_harness.owner_engine.begin() as conn:
        before = conn.execute(
            sa.text("SELECT pg_get_functiondef(to_regprocedure(:fn))"), {"fn": lock}
        ).scalar_one()
        conn.execute(sa.text("CREATE SCHEMA private_acl_schema"))
        conn.execute(sa.text("CREATE ROLE private_acl_owner NOLOGIN"))
        conn.execute(
            sa.text(
                {
                    "missing": f"ALTER FUNCTION {lock} RENAME TO private_acl_renamed",
                    "overload": f"ALTER FUNCTION {lock} RENAME TO private_acl_renamed",
                    "schema": f"ALTER FUNCTION {lock} SET SCHEMA private_acl_schema",
                    "invoker": f"ALTER FUNCTION {lock} SECURITY INVOKER",
                    "other_owner": f"ALTER FUNCTION {lock} OWNER TO private_acl_owner",
                }[change]
            )
        )
        if change == "overload":
            conn.execute(
                sa.text(
                    "CREATE FUNCTION public.lock_publication_candidate(uuid,text) "
                    "RETURNS boolean LANGUAGE sql SECURITY DEFINER AS 'SELECT true'"
                )
            )
        changed_identity = {
            "missing": "public.private_acl_renamed(uuid)",
            "overload": "public.private_acl_renamed(uuid)",
            "schema": "private_acl_schema.lock_publication_candidate(uuid)",
            "invoker": lock,
            "other_owner": lock,
        }[change]
        mutated = conn.execute(
            sa.text(
                "SELECT pg_get_functiondef(oid),proowner,proacl::text FROM pg_proc "
                "WHERE oid=to_regprocedure(:fn)"
            ),
            {"fn": changed_identity},
        ).one()
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL identity/owner:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text(
                        "SELECT pg_get_functiondef(oid),proowner,proacl::text "
                        "FROM pg_proc "
                        "WHERE oid=to_regprocedure(:fn)"
                    ),
                    {"fn": changed_identity},
                ).one()
                == mutated
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            if change == "overload":
                conn.execute(
                    sa.text(
                        "DROP FUNCTION public.lock_publication_candidate(uuid,text)"
                    )
                )
            if change in {"missing", "overload"}:
                conn.execute(
                    sa.text(
                        "ALTER FUNCTION "
                        "public.private_acl_renamed(uuid) RENAME TO "
                        "lock_publication_candidate"
                    )
                )
            elif change == "schema":
                conn.execute(
                    sa.text(
                        "ALTER FUNCTION "
                        "private_acl_schema.lock_publication_candidate("
                        "uuid) SET SCHEMA public"
                    )
                )
            elif change == "invoker":
                conn.execute(sa.text(f"ALTER FUNCTION {lock} SECURITY DEFINER"))
            else:
                conn.execute(sa.text(f"ALTER FUNCTION {lock} OWNER TO test"))
                conn.execute(
                    sa.text(f"REVOKE EXECUTE ON FUNCTION {lock} FROM private_acl_owner")
                )
            assert (
                conn.scalar(
                    sa.text("SELECT pg_get_functiondef(to_regprocedure(:fn))"),
                    {"fn": lock},
                )
                == before
            )
            conn.execute(sa.text("DROP SCHEMA private_acl_schema"))
            conn.execute(sa.text("DROP ROLE private_acl_owner"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


@pytest.mark.parametrize(
    "role",
    [
        "sapphire_worker",
        "sapphire_api",
        "sapphire_dhm21_delivery_writer",
        "sapphire_static_importer",
    ],
)
def test_private_function_stale_direct_managed_grant_converges(
    role_harness: _RoleBootstrapHarness,
    role: str,
) -> None:
    from tests.integration.ops.mixed_restore_fixture import assert_private_function_acls

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "GRANT EXECUTE ON FUNCTION "
                "public.sf_mark_restricted_release_used(uuid) TO "
                f"{role} WITH GRANT OPTION"
            )
        )
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn:
        assert_private_function_acls(conn)


def test_private_function_later_failure_retains_only_intended_partial_grants(
    role_harness: _RoleBootstrapHarness,
) -> None:
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE public.private_acl_late_failure(id integer)")
        )
        conn.execute(
            sa.text(
                "ALTER TABLE public.private_acl_late_failure OWNER TO sapphire_backup"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "sapphire_backup owns" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            _assert_private_public_denied(conn)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege('sapphire_api',"
                        "'public.lock_publication_candidate(uuid)',"
                        "'EXECUTE')"
                    )
                )
                is True
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT "
                        "has_function_privilege('sapphire_backup',"
                        "'public.lock_publication_candidate(uuid)',"
                        "'EXECUTE')"
                    )
                )
                is False
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE public.private_acl_late_failure"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


@pytest.mark.parametrize("role", ["sapphire_operator", "sapphire_static_importer"])
def test_private_function_registrar_actual_login_boundary(
    role_harness: _RoleBootstrapHarness,
    role: str,
) -> None:
    from sqlalchemy.exc import DBAPIError

    result = role_harness.run_bootstrap(
        "api-fixture", "worker-fixture", operator_password="private-acl-test"
    )
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "ALTER ROLE sapphire_static_importer LOGIN PASSWORD 'private-acl-test'"
            )
        )
    engine = sa.create_engine(role_harness.role_url(role, "private-acl-test"))
    try:
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT session_user")) == role
            assert conn.scalar(sa.text("SELECT current_user")) == role
            with pytest.raises(DBAPIError) as exc_info:
                conn.execute(
                    sa.text(
                        "SELECT public.sf_register_nepal21_candidate("
                        "NULL::uuid,NULL::uuid,NULL::text,NULL::double precision,"
                        "NULL::double precision,NULL::text,NULL::timestamptz)"
                    )
                )
            sqlstate = getattr(exc_info.value.orig, "sqlstate", None)
            if role == "sapphire_operator":
                assert sqlstate == "42501"
                assert (
                    "permission denied for function sf_register_nepal21_candidate"
                    in str(exc_info.value.orig)
                )
            else:
                assert sqlstate == "P0001"
                assert "permission denied for function" not in str(exc_info.value.orig)
    finally:
        engine.dispose()
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


@pytest.mark.parametrize("cleanup", ["restores", "fails"])
def test_lineage_fixture_cleanup_preserves_primary_failure(
    role_harness: _RoleBootstrapHarness,
    monkeypatch: pytest.MonkeyPatch,
    cleanup: str,
) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from alembic import command

    original_upgrade = command.upgrade
    attempted: list[str] = []

    def fail_upgrade(config: Config, revision: str, **kwargs: object) -> None:
        attempted.append(revision)
        if revision == "0071":
            raise RuntimeError("intentional primary migration failure")
        if revision == "head" and cleanup == "fails":
            raise RuntimeError("intentional secondary cleanup failure")
        original_upgrade(config, revision)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(command, "upgrade", fail_upgrade)
            with pytest.raises(RuntimeError, match="intentional primary") as caught:
                test_live_runtime_temp_objects_do_not_abort_preflights(
                    role_harness, "sapphire_api", "opaque"
                )
            assert attempted == ["0071", "head"]
            if cleanup == "fails":
                assert any(
                    "secondary cleanup" in note for note in caught.value.__notes__
                )
            else:
                with role_harness.owner_engine.connect() as conn:
                    assert (
                        conn.scalar(sa.text("SELECT version_num FROM alembic_version"))
                        == ScriptDirectory.from_config(
                            Config("alembic.ini")
                        ).get_current_head()
                    )
    finally:
        original_upgrade(Config("alembic.ini"), "head")


@pytest.mark.parametrize(
    "hops,endpoint",
    [
        (1, "sapphire_api"),
        (2, "sapphire_api"),
        (3, "sapphire_api"),
        (1, "test"),
        (2, "test"),
    ],
)
def test_private_function_admin_bridge_refused_without_membership_mutation(
    role_harness: _RoleBootstrapHarness,
    hops: int,
    endpoint: str,
) -> None:
    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        for index in range(hops):
            conn.execute(
                sa.text(f"CREATE ROLE private_acl_bridge_{index} NOLOGIN INHERIT")
            )
        conn.execute(
            sa.text(
                f"GRANT {endpoint} TO private_acl_bridge_{hops - 1} "
                "WITH ADMIN FALSE, INHERIT TRUE, SET FALSE"
            )
        )
        for index in range(hops - 1):
            conn.execute(
                sa.text(
                    f"GRANT private_acl_bridge_{index + 1} "
                    f"TO private_acl_bridge_{index} "
                    "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
                )
            )
        conn.execute(
            sa.text(
                "GRANT private_acl_bridge_0 TO sapphire_backup "
                "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
            )
        )
        conn.execute(
            sa.text("CREATE TABLE public.private_acl_late_failure(id integer)")
        )
        conn.execute(
            sa.text(
                "ALTER TABLE public.private_acl_late_failure OWNER TO sapphire_backup"
            )
        )
        membership_sql = sa.text(
            "SELECT roleid,member,grantor,admin_option,inherit_option,set_option "
            "FROM pg_auth_members WHERE member IN (SELECT oid FROM pg_roles "
            "WHERE rolname='sapphire_backup' OR rolname LIKE 'private_acl_bridge_%') "
            "ORDER BY roleid,member,grantor"
        )
        before = conn.execute(membership_sql).all()
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode != 0
        assert "private function ACL forbidden route:" in result.stderr
        with role_harness.owner_engine.connect() as conn:
            _assert_private_public_denied(conn)
            assert conn.execute(membership_sql).all() == before
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege('sapphire_api',"
                        "'public.lock_publication_candidate(uuid)','EXECUTE')"
                    )
                )
                is False
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE public.private_acl_late_failure"))
            for index in range(hops):
                conn.execute(sa.text(f"DROP ROLE private_acl_bridge_{index}"))
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )


def test_private_function_unrelated_admin_bridge_allows_normal_convergence(
    role_harness: _RoleBootstrapHarness,
) -> None:
    from tests.integration.ops.mixed_restore_fixture import assert_private_function_acls

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE private_acl_unrelated NOLOGIN"))
        conn.execute(
            sa.text(
                "GRANT private_acl_unrelated TO sapphire_backup "
                "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert_private_function_acls(conn)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_roles "
                        "WHERE rolname='private_acl_unrelated'"
                    )
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT pg_has_role('sapphire_backup',"
                        "'private_acl_unrelated','MEMBER')"
                    )
                )
                is False
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP ROLE private_acl_unrelated"))
