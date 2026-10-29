from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import bafu_unit_write_scopes, observations
from sapphire_flow.store.bafu_batch_journal import BafuBatchIntegrityError
from sapphire_flow.store.bafu_unit_documents import document_digest, encode_document
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.bafu_write_result import BafuWriteStatus
from tests.integration.db.test_role_bootstrap import (
    bootstrapped as bootstrapped,
)
from tests.integration.db.test_role_bootstrap import (
    role_harness as role_harness,
)
from tests.integration.store.test_bafu_batch_journal import batch as batch
from tests.integration.store.test_bafu_unit_writer import QUOTA, later, normalized

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def isolated_committed_db_engine(bootstrapped: _RoleBootstrapHarness) -> sa.Engine:
    return bootstrapped.owner_engine


@pytest.fixture
def worker(bootstrapped: _RoleBootstrapHarness) -> Iterator[sa.Engine]:
    engine = sa.create_engine(
        bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def protected(
    batch: BafuStationCaptureBatch, isolated_committed_db_engine: sa.Engine
) -> BafuStationCaptureBatch:
    candidate = normalized(batch)
    with isolated_committed_db_engine.begin() as conn:
        conn.execute(
            sa.insert(bafu_unit_write_scopes).values(
                id=uuid4(),
                tenant_id=batch.tenant_id,
                station_id=batch.station_id,
                network="bafu",
                gauge_code=batch.key.gauge.value,
                lindas_kind=batch.key.kind,
                starts_at=batch.received_at,
                ends_at=batch.received_at + timedelta(days=1),
                registry_sha256=candidate.registry_sha256,
                approval_sha256="a" * 64,
            )
        )
    return candidate


class TestBafuWriteGuards:
    @pytest.mark.parametrize("path", ["raw", "full", "sql"])
    def test_bypass_fails_at_commit(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch, path: str
    ) -> None:
        with worker.connect() as conn:
            transaction = conn.begin()
            if path == "raw":
                PgObservationStore(conn).store_raw_observations(
                    list(protected.observations)
                )
            elif path == "full":
                from tests.conftest import make_observation

                PgObservationStore(conn).store_observations(
                    [
                        make_observation(
                            station_id=protected.station_id,
                            timestamp=protected.received_at,
                            value=0.022,
                        )
                    ]
                )
            else:
                conn.execute(
                    sa.insert(observations).values(
                        id=uuid4(),
                        station_id=protected.station_id,
                        timestamp=protected.received_at,
                        parameter="discharge",
                        source="measured",
                        value=0.022,
                        qc_status="raw",
                    )
                )
            with pytest.raises(DBAPIError, match="lacks current admitted proof"):
                transaction.commit()

    def test_scoped_writer_and_restatement_commit(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        writer = PgBafuUnitWriter(worker.begin, quota=QUOTA)
        assert writer.write(protected).status is BafuWriteStatus.COMMITTED
        assert (
            writer.write(later(protected, literal="44")).status
            is BafuWriteStatus.COMMITTED
        )
        assert writer.write(protected).status is BafuWriteStatus.REPLAYED

    @pytest.mark.parametrize("change", ["value", "source", "timestamp", "parameter"])
    def test_old_scope_cannot_escape_or_reuse_old_proof(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch, change: str
    ) -> None:
        assert (
            PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected).status
            is BafuWriteStatus.COMMITTED
        )
        changes = {
            "value": 9.0,
            "source": "manual_import",
            "timestamp": protected.received_at + timedelta(days=2),
            "parameter": "water_level",
        }
        with worker.connect() as conn:
            transaction = conn.begin()
            conn.execute(
                sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
                {"batch": str(protected.batch_id)},
            )
            conn.execute(
                sa.update(observations)
                .where(observations.c.station_id == protected.station_id)
                .values(**{change: changes[change]})
            )
            with pytest.raises(DBAPIError, match="BAFU protected"):
                transaction.commit()

    def test_final_state_mismatch(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        # Use the transaction factory to mutate after the writer produced its receipt.
        from contextlib import contextmanager

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                yield conn
                conn.execute(
                    sa.update(observations)
                    .where(observations.c.station_id == protected.station_id)
                    .values(value=9.0)
                )

        with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
            PgBafuUnitWriter(transaction, quota=QUOTA).write(protected)

    def test_delete_fails_closed(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        # Seed a legacy row outside protection, then approve its interval.
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=protected.station_id,
                    timestamp=protected.received_at - timedelta(days=1),
                    parameter="discharge",
                    source="measured",
                    value=22.0,
                    qc_status="raw",
                )
            )
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.insert(bafu_unit_write_scopes).values(
                    id=uuid4(),
                    tenant_id=protected.tenant_id,
                    station_id=protected.station_id,
                    network="bafu",
                    gauge_code=protected.key.gauge.value,
                    lindas_kind="river",
                    starts_at=protected.received_at - timedelta(days=2),
                    registry_sha256=protected.registry_sha256,
                    approval_sha256="b" * 64,
                )
            )
        with isolated_committed_db_engine.connect() as conn:
            transaction = conn.begin()
            conn.execute(
                sa.delete(observations).where(
                    observations.c.station_id == protected.station_id
                )
            )
            with pytest.raises(DBAPIError, match="deletion has no D1 operation"):
                transaction.commit()

    @pytest.mark.parametrize(
        "role,password",
        [("sapphire_worker", "worker-pw-initial"), ("sapphire_api", "api-pw-initial")],
    )
    def test_private_payloads_and_maintenance_denied(
        self, bootstrapped: _RoleBootstrapHarness, role: str, password: str
    ) -> None:
        url = bootstrapped.role_url(role, password)
        for sql in (
            "SELECT capture FROM bafu_unit_events",
            "SELECT before_state FROM bafu_unit_events",
            "SELECT result FROM bafu_unit_batch_receipts",
            "DELETE FROM bafu_unit_events",
            "TRUNCATE bafu_unit_events",
            "INSERT INTO bafu_unit_hold_releases DEFAULT VALUES",
            "INSERT INTO bafu_unit_write_scopes DEFAULT VALUES",
            "INSERT INTO bafu_unit_events(event_id) OVERRIDING SYSTEM VALUE VALUES (3)",
            "INSERT INTO bafu_unit_batch_admissions(admitted_at) VALUES (now())",
        ):
            assert bootstrapped.denied(url, sql), sql

    def test_safe_api_hold_predicate(self, bootstrapped: _RoleBootstrapHarness) -> None:
        from sapphire_flow.store.bafu_unit_read_filter import trusted_unit_predicate

        engine = sa.create_engine(
            bootstrapped.role_url("sapphire_api", "api-pw-initial")
        )
        try:
            with engine.connect() as conn:
                conn.execute(
                    sa.select(observations.c.id).where(
                        trusted_unit_predicate(observations)
                    )
                ).all()
        finally:
            engine.dispose()

    @pytest.mark.parametrize("zone", ["UTC", "Europe/Zurich"])
    def test_python_digest_projection_contract(
        self, isolated_committed_db_engine: sa.Engine, zone: str
    ) -> None:
        data = {
            "unicode": "é😀\n",
            "numbers": [-0.0, 1e23, 0.12345678901234567, 9223372036854775807],
            "keys": {"z": None, "a": True},
        }
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('TimeZone', :zone, true)"), {"zone": zone}
            )
            actual = conn.execute(
                sa.text(
                    "SELECT encode(sha256(convert_to(bafu_unit_canonical_json("
                    "CAST(:data AS jsonb)), 'UTF8')), 'hex')"
                ),
                {"data": encode_document(data)},
            ).scalar_one()
        assert actual == document_digest(data)


class TestBafuRoleIsolation:
    def test_dhm_commit_never_needs_private_bafu_reads(
        self, bootstrapped: _RoleBootstrapHarness, batch: BafuStationCaptureBatch
    ) -> None:
        from sapphire_flow.db.metadata import stations, tenants
        from sapphire_flow.types.dhm_delivery import DELIVERY_ID

        with bootstrapped.owner_engine.begin() as conn:
            conn.execute(
                sa.update(tenants)
                .where(tenants.c.id == batch.tenant_id)
                .values(code="chwrr")
            )
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(network="dhm")
            )
        result = bootstrapped.run_bootstrap(
            "api-pw-initial", "worker-pw-initial", operator_password="operator-test"
        )
        assert result.returncode == 0, result.stderr
        url = bootstrapped.role_url("sapphire_operator", "operator-test")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                assert (
                    conn.scalar(sa.text("SELECT session_user")) == "sapphire_operator"
                )
                for parameter, source in (
                    ("discharge", "measured"),
                    ("discharge", "manual_import"),
                    ("water_level", "measured"),
                ):
                    conn.execute(
                        sa.insert(observations).values(
                            id=uuid4(),
                            station_id=batch.station_id,
                            timestamp=batch.received_at,
                            parameter=parameter,
                            source=source,
                            value=2.0,
                            qc_status="raw",
                            delivery_id=DELIVERY_ID,
                        )
                    )
                conn.execute(
                    sa.update(observations)
                    .where(observations.c.station_id == batch.station_id)
                    .values(value=3.0)
                )
            with engine.begin() as conn:
                conn.execute(
                    sa.delete(observations).where(
                        observations.c.station_id == batch.station_id
                    )
                )
            for sql in (
                "SELECT * FROM bafu_unit_write_scopes",
                "SELECT * FROM bafu_unit_event_reconciliation",
                "INSERT INTO bafu_unit_write_scopes DEFAULT VALUES",
                "INSERT INTO bafu_unit_hold_releases DEFAULT VALUES",
                "SET ROLE sapphire_bafu_maintenance",
            ):
                assert bootstrapped.denied(url, sql), sql
        finally:
            engine.dispose()

    def test_maintenance_has_only_disposition_authority(
        self, bootstrapped: _RoleBootstrapHarness, batch: BafuStationCaptureBatch
    ) -> None:
        with bootstrapped.owner_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE sapphire_bafu_maintenance"))
            conn.execute(
                sa.insert(bafu_unit_write_scopes).values(
                    id=uuid4(),
                    tenant_id=batch.tenant_id,
                    station_id=batch.station_id,
                    network="bafu",
                    gauge_code=batch.key.gauge.value,
                    lindas_kind="river",
                    starts_at=batch.received_at,
                    registry_sha256=batch.registry_sha256,
                    approval_sha256="b" * 64,
                )
            )
        with bootstrapped.owner_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE sapphire_bafu_maintenance"))
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(sa.update(observations).values(value=3.0))


class TestBafuAdmissionProof:
    @pytest.mark.parametrize("disposition", ["rejected", "superseded"])
    def test_rejected_batch_cannot_append_effect(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch, disposition: str
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.db.metadata import bafu_unit_events
        from sapphire_flow.store.bafu_batch_journal import PgBafuBatchJournal
        from sapphire_flow.types.bafu_capture_batch import BafuCaptureBatchId

        journal = PgBafuBatchJournal(worker.begin)
        journal.admit(protected)
        if disposition == "rejected":
            rejected = replace(protected, batch_id=BafuCaptureBatchId(uuid4()))
            journal.admit(rejected)
        else:
            journal.admit(later(protected))
            rejected = protected
        with worker.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
                {"batch": str(rejected.batch_id)},
            )
            with pytest.raises(DBAPIError, match="require admitted bound batch"):
                conn.execute(
                    sa.insert(bafu_unit_events).values(
                        batch_id=rejected.batch_id,
                        tenant_id=rejected.tenant_id,
                        station_id=rejected.station_id,
                        outcome="quarantined",
                        fingerprint="a" * 64,
                        capture={},
                        reason="test",
                    )
                )

    def test_wrong_event_after_image_cannot_commit(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        # Disposable owner-only fault injector; the writer remains a real worker.
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text("""CREATE FUNCTION guard_test_bad_image() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN NEW.after_state := jsonb_set(
                    NEW.after_state, '{value}', '999'::jsonb);
                RETURN NEW; END $$""")
            )
            conn.execute(
                sa.text(
                    "CREATE TRIGGER guard_test_bad_image BEFORE INSERT "
                    "ON bafu_unit_events FOR EACH ROW "
                    "EXECUTE FUNCTION guard_test_bad_image()"
                )
            )
        try:
            with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
                PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text("DROP TRIGGER guard_test_bad_image ON bafu_unit_events")
                )
                conn.execute(sa.text("DROP FUNCTION guard_test_bad_image()"))
