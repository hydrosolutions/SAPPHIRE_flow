from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    forecast_publication_decisions,
    forecast_publication_events,
    forecast_publication_selections,
    forecasts,
    rejected_forecasts,
)
from sapphire_flow.store.forecast_publication_store import (
    PgForecastPublicationStore,
    PublicationConflictError,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_forecast_publication_data_use import (
    seed_test_candidate,
)
from tests.integration.store.test_forecast_publication_store import _publish

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


def test_real_roles_cannot_publish_or_capture_test_data(
    role_harness: _RoleBootstrapHarness,
) -> None:
    result = role_harness.run_bootstrap(
        "api-fixture", "worker-fixture", operator_password="operator-fixture"
    )
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.begin() as conn:
        store, principal, ordinary_id, candidate = seed_test_candidate(conn)
        _publish(store, principal, ordinary_id)
        decision = dict(
            conn.execute(sa.select(forecast_publication_decisions)).mappings().one()
        )
        event = dict(
            conn.execute(sa.select(forecast_publication_events)).mappings().one()
        )
    api = sa.create_engine(role_harness.role_url("sapphire_api", "api-fixture"))
    worker = sa.create_engine(
        role_harness.role_url("sapphire_worker", "worker-fixture")
    )
    operator = sa.create_engine(
        role_harness.role_url("sapphire_operator", "operator-fixture")
    )
    try:
        with api.connect() as conn:
            assert conn.scalar(sa.text("SELECT session_user")) == "sapphire_api"
            with pytest.raises(
                PublicationConflictError, match="test forecast cannot be published"
            ):
                _publish(
                    PgForecastPublicationStore(conn),
                    principal,
                    candidate.id,
                    expected_selection_version=1,
                    idempotency_key="test",
                )
        for reference in ("forecast_id", "replaced_forecast_id"):
            for table, saved in (
                (forecast_publication_decisions, decision),
                (forecast_publication_events, event),
            ):
                row = {**saved, reference: candidate.id}
                if table is forecast_publication_decisions:
                    row.update(id=uuid4(), idempotency_key=uuid4().hex)
                else:
                    row["sequence"] += 1
                with (
                    api.begin() as conn,
                    pytest.raises(sa.exc.DBAPIError, match="test.*publication"),
                    conn.begin_nested(),
                ):
                    conn.execute(sa.insert(table).values(**row))
        with (
            api.begin() as conn,
            pytest.raises(sa.exc.DBAPIError, match="test.*publication"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.update(forecast_publication_selections).values(
                    selected_forecast_id=candidate.id
                )
            )
        for status in ("reviewed", "published"):
            with (
                worker.begin() as conn,
                pytest.raises(sa.exc.DBAPIError, match="ck_forecasts_test_publication"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.update(forecasts)
                    .where(forecasts.c.id == candidate.id)
                    .values(status=status)
                )
        values = dict(
            id=uuid4(),
            attempt_id=uuid4(),
            station_id=candidate.station_id,
            model_id=candidate.model_id,
            issued_at=candidate.issued_at,
            parameter="discharge",
            units="m3/s",
            representation="members",
            time_step_seconds=3600,
            values={},
            qc_status="qc_failed",
            data_use="expired_rating_test",
            input_lineage=candidate.input_lineage.content,
        )
        for engine, reason in (
            (role_harness.owner_engine, "test rejection writes are disabled"),
            (worker, "test rejection writes are disabled"),
            (api, "permission denied"),
            (operator, "permission denied"),
        ):
            with (
                engine.begin() as conn,
                pytest.raises(sa.exc.DBAPIError, match=reason),
                conn.begin_nested(),
            ):
                conn.execute(sa.insert(rejected_forecasts).values(**values))
        import psycopg

        for engine in (role_harness.owner_engine, worker):
            with (
                engine.begin() as conn,
                pytest.raises(
                    psycopg.errors.RaiseException,
                    match="test rejection writes are disabled",
                ),
                conn.begin_nested(),
                conn.connection.driver_connection.cursor() as cursor,
                cursor.copy(
                    "COPY rejected_forecasts (id,attempt_id,station_id,model_id,"
                    "issued_at,parameter,units,representation,time_step_seconds,"
                    "values,qc_status,data_use,input_lineage) FROM STDIN"
                ) as copy,
            ):
                copy.write_row(
                    (
                        uuid4(),
                        uuid4(),
                        candidate.station_id,
                        candidate.model_id,
                        candidate.issued_at,
                        "discharge",
                        "m3/s",
                        "members",
                        3600,
                        "{}",
                        "qc_failed",
                        "expired_rating_test",
                        candidate.input_lineage.content,
                    )
                )
        with worker.begin() as conn:
            values.update(id=uuid4(), data_use="standard", input_lineage=None)
            conn.execute(sa.insert(rejected_forecasts).values(**values))
        with (
            worker.begin() as conn,
            pytest.raises(sa.exc.DBAPIError, match="permission denied|append-only"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.update(rejected_forecasts).values(data_use="expired_rating_test")
            )
        assert role_harness.dump_as_backup_role("backup-pw-default").returncode == 0
    finally:
        api.dispose()
        worker.dispose()
        operator.dispose()
