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


def test_backup_keeps_both_protected_column_privileges_and_full_dump(
    role_harness,
) -> None:
    import sqlalchemy as sa

    assert role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
    from tests.integration.store.test_forecast_data_use import _pair, _stores

    with role_harness.owner_engine.begin() as conn:
        standard, test = _pair(conn)
        conn.execute(
            sa.text("DROP TRIGGER trg_forecast_test_write_refused ON forecasts")
        )
        standard_store, test_store = _stores(conn)
        standard_store.store_forecast(standard)
        test_store.store_forecast(test)
        conn.execute(
            sa.text(
                "CREATE TRIGGER trg_forecast_test_write_refused "
                "BEFORE INSERT ON forecasts FOR EACH ROW "
                "EXECUTE FUNCTION public.forecast_test_write_refused()"
            )
        )
    engine = sa.create_engine(
        role_harness.role_url("sapphire_backup", "backup-pw-default")
    )
    try:
        with engine.connect() as conn:
            retained = conn.execute(
                sa.text(
                    "SELECT data_use, input_lineage "
                    "FROM forecasts WHERE id IN (:standard, :test)"
                ),
                {"standard": standard.id, "test": test.id},
            ).all()
            assert dict(retained) == {
                "standard": None,
                "expired_rating_test": test.input_lineage.content,
            }
            for table in ["forecasts", "rejected_forecasts"]:
                assert conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege(current_user, :table, "
                        "'input_lineage', 'SELECT')"
                    ),
                    {"table": table},
                )
                conn.execute(sa.text(f"SELECT * FROM {table}"))
    finally:
        engine.dispose()
    result = role_harness.dump_as_backup_role("backup-pw-default")
    assert result.returncode == 0, result.stderr


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
