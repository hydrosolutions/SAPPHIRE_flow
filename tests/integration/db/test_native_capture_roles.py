from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.native_capture_store import PgNativeCaptureStore
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_native_capture_store import NOW, inputs, seed_station
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


class TestNativeCaptureRoles:
    def test_worker_can_capture_and_api_cannot_read_protected_rows(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        station = seed_station(role_harness.owner_engine)
        batch, rows = inputs(station)
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            store = PgNativeCaptureStore(
                savepoint_factory(conn), clock=lambda: NOW, id_gen=uuid4
            )
            assert store.commit_batch(batch, rows).inserted_count == 1
            conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        for table in (
            "native_measurement_captures",
            "native_capture_batches",
            "native_capture_sightings",
        ):
            with role_harness.owner_engine.begin() as conn:
                conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
                with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
                    conn.execute(sa.text(f"SELECT * FROM {table}"))

    def test_owner_cannot_truncate_or_delete_capture_evidence(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        station = seed_station(role_harness.owner_engine)
        batch, rows = inputs(station)
        store = PgNativeCaptureStore(
            role_harness.owner_engine.begin, clock=lambda: NOW, id_gen=uuid4
        )
        store.commit_batch(batch, rows)
        with (
            role_harness.owner_engine.begin() as conn,
            pytest.raises(
                sa.exc.DBAPIError, match="native rating evidence is immutable"
            ),
        ):
            conn.execute(sa.text("TRUNCATE native_capture_sightings"))
        with (
            role_harness.owner_engine.begin() as conn,
            pytest.raises(
                sa.exc.DBAPIError, match="native rating evidence is immutable"
            ),
        ):
            conn.execute(
                sa.text("DELETE FROM native_capture_batches WHERE id=:identity"),
                {"identity": batch.request.batch_id},
            )
