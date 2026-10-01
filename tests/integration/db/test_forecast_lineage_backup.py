"""Committed dump fixtures live only in this disposable module's container."""

from __future__ import annotations

from tests.integration.db.test_role_bootstrap import role_harness as role_harness


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
