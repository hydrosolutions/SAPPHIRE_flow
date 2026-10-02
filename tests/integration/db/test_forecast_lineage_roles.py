from __future__ import annotations

import pytest

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
        engine.dispose()


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
