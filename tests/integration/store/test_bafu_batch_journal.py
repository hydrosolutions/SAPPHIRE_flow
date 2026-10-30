from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import bafu_unit_batch_admissions, tenants
from sapphire_flow.store.bafu_batch_journal import (
    BafuBatchBindingError,
    BafuBatchIntegrityError,
    PgBafuBatchJournal,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.bafu_capture import (
    BafuDischargeCapture,
    QuarantinedDischargeCapture,
)
from sapphire_flow.types.bafu_capture_batch import (
    BafuAdmissionDecision,
    BafuCaptureBatchId,
    BafuStationCaptureBatch,
)
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    BafuUnitRegistry,
    UnitResolutionReason,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.conftest import make_station_config

_codes = count(8000)


@pytest.fixture
def batch(isolated_committed_db_engine: sa.Engine) -> BafuStationCaptureBatch:
    tenant, station = TenantId(uuid4()), StationId(uuid4())
    code = str(next(_codes))
    timestamp = ensure_utc(datetime(2026, 10, 28, tzinfo=UTC))
    with isolated_committed_db_engine.begin() as conn:
        conn.execute(
            sa.insert(tenants).values(
                id=tenant, code=str(tenant), name="unit journal test"
            )
        )
        PgStationStore(conn).store_station(
            make_station_config(station_id=station, code=code, tenant_id=tenant)
        )
    key = BafuDischargeKey(gauge=BafuGaugeCode(value=code), kind="river")
    capture = BafuDischargeCapture(
        key=key,
        source_literals=("22",),
        measurement_literals=(timestamp.isoformat(),),
        payload_sha256="a" * 64,
        received_at=timestamp,
        result=QuarantinedDischargeCapture(
            measurement_time=timestamp, reason=UnitResolutionReason.MISSING
        ),
    )
    return BafuStationCaptureBatch(
        batch_id=BafuCaptureBatchId(uuid4()),
        tenant_id=tenant,
        station_id=station,
        key=key,
        received_at=timestamp,
        registry=BafuUnitRegistry(version="test", rules=()),
        observations=(),
        captures=(capture,),
    )


class TestPgBafuBatchJournal:
    def test_same_id_same_input_reuses_admission(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        first = journal.admit(batch)
        retry = journal.admit(batch)
        assert first.decision is BafuAdmissionDecision.ADMITTED
        assert retry.decision is BafuAdmissionDecision.RETRY
        assert retry.sequence == first.sequence

    def test_same_id_changed_input_is_integrity_failure(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        journal.admit(batch)
        with pytest.raises(BafuBatchIntegrityError, match="different captured input"):
            journal.admit(
                replace(batch, registry=replace(batch.registry, version="test-v2"))
            )

    def test_later_started_batch_blocks_older_even_without_receipts(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        journal.admit(batch)
        later = ensure_utc(batch.received_at + timedelta(microseconds=1))
        journal.admit(
            replace(
                batch,
                batch_id=BafuCaptureBatchId(uuid4()),
                received_at=later,
                captures=(replace(batch.captures[0], received_at=later),),
            )
        )
        assert journal.admit(batch).decision is BafuAdmissionDecision.SUPERSEDED

    def test_rejects_wrong_persisted_source_binding(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        with pytest.raises(
            BafuBatchBindingError, match="persisted BAFU station binding"
        ):
            journal.admit(replace(batch, tenant_id=TenantId(uuid4())))

    def test_rejects_autocommit_factory(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine.execution_options(
            isolation_level="AUTOCOMMIT"
        )
        with pytest.raises(ValueError, match="non-AUTOCOMMIT"):
            PgBafuBatchJournal(engine.begin).admit(batch)

    def test_database_rejects_journal_mutation(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        PgBafuBatchJournal(isolated_committed_db_engine.begin).admit(batch)
        with (
            pytest.raises(DBAPIError, match="immutable"),
            isolated_committed_db_engine.begin() as conn,
        ):
            conn.execute(
                sa.delete(bafu_unit_batch_admissions).where(
                    bafu_unit_batch_admissions.c.batch_id == batch.batch_id
                )
            )

    def test_concurrent_same_batch_gets_one_admission(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        barrier = Barrier(2)

        def admit() -> BafuAdmissionDecision:
            barrier.wait(timeout=10)
            return (
                PgBafuBatchJournal(isolated_committed_db_engine.begin)
                .admit(batch)
                .decision
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(admit), executor.submit(admit))
            decisions = [future.result(timeout=15) for future in futures]
        assert set(decisions) == {
            BafuAdmissionDecision.ADMITTED,
            BafuAdmissionDecision.RETRY,
        }

    def test_older_capture_cannot_arrive_as_newer_work(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        journal.admit(batch)
        older = ensure_utc(batch.received_at - timedelta(seconds=1))
        stale = replace(
            batch,
            batch_id=BafuCaptureBatchId(uuid4()),
            received_at=older,
            captures=(replace(batch.captures[0], received_at=older),),
        )
        result = journal.admit(stale)
        assert result.decision is BafuAdmissionDecision.SUPERSEDED
        assert result.sequence is None
        assert journal.admit(batch).decision is BafuAdmissionDecision.RETRY

    @pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
    def test_rejects_snapshot_isolation(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolation: str,
    ) -> None:
        engine = isolated_committed_db_engine.execution_options(
            isolation_level=isolation
        )
        with pytest.raises(ValueError, match="requires READ COMMITTED"):
            PgBafuBatchJournal(engine.begin).admit(batch)

    def test_equal_time_new_identity_cannot_supersede_original(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        journal.admit(batch)
        assert (
            journal.admit(replace(batch, batch_id=BafuCaptureBatchId(uuid4()))).decision
            is BafuAdmissionDecision.SUPERSEDED
        )
        assert journal.admit(batch).decision is BafuAdmissionDecision.RETRY

    def test_concurrent_different_ids_with_equal_time_admit_only_one(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        barrier = Barrier(2)

        def admit(candidate: BafuStationCaptureBatch) -> BafuAdmissionDecision:
            barrier.wait(timeout=10)
            return (
                PgBafuBatchJournal(isolated_committed_db_engine.begin)
                .admit(candidate)
                .decision
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(admit, batch),
                executor.submit(
                    admit, replace(batch, batch_id=BafuCaptureBatchId(uuid4()))
                ),
            )
            decisions = [future.result(timeout=15) for future in futures]
        assert set(decisions) == {
            BafuAdmissionDecision.ADMITTED,
            BafuAdmissionDecision.SUPERSEDED,
        }

    @pytest.mark.parametrize("offset", [-1, 0])
    def test_rejected_identity_remains_bound_without_fencing_admitted_work(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        offset: int,
    ) -> None:
        journal = PgBafuBatchJournal(isolated_committed_db_engine.begin)
        journal.admit(batch)
        received = ensure_utc(batch.received_at + timedelta(seconds=offset))
        rejected = replace(
            batch,
            batch_id=BafuCaptureBatchId(uuid4()),
            received_at=received,
            captures=(replace(batch.captures[0], received_at=received),),
        )
        assert journal.admit(rejected).decision is BafuAdmissionDecision.SUPERSEDED
        assert journal.admit(rejected).decision is BafuAdmissionDecision.SUPERSEDED
        assert journal.admit(batch).decision is BafuAdmissionDecision.RETRY
        newer = ensure_utc(batch.received_at + timedelta(seconds=1))
        mutated = replace(
            rejected,
            received_at=newer,
            captures=(replace(rejected.captures[0], received_at=newer),),
        )
        with pytest.raises(BafuBatchIntegrityError, match="different captured input"):
            journal.admit(mutated)
