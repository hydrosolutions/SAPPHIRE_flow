from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from math import nextafter
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import bafu_unit_batch_admissions as admissions
from sapphire_flow.db.metadata import bafu_unit_events as events
from sapphire_flow.db.metadata import bafu_unit_receipt_reconciliation as receipts
from sapphire_flow.db.metadata import observations
from sapphire_flow.store.bafu_batch_journal import (
    BafuBatchIntegrityError,
    PgBafuBatchJournal,
)
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
from sapphire_flow.types.bafu_write_result import BafuWriteStatus
from tests.integration.db.test_bafu_unit_guards import (
    batch as batch,
)
from tests.integration.db.test_bafu_unit_guards import (
    bootstrapped as bootstrapped,
)
from tests.integration.db.test_bafu_unit_guards import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)
from tests.integration.db.test_bafu_unit_guards import (
    protected as protected,
)
from tests.integration.db.test_bafu_unit_guards import (
    role_harness as role_harness,
)
from tests.integration.db.test_bafu_unit_guards import (
    worker as worker,
)
from tests.integration.store.test_bafu_unit_writer import QUOTA, later, normalized

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch


def append_admission(conn: sa.Connection, candidate: BafuStationCaptureBatch) -> None:
    conn.execute(
        sa.insert(admissions).values(
            batch_id=candidate.batch_id,
            disposition="admitted",
            tenant_id=candidate.tenant_id,
            station_id=candidate.station_id,
            gauge_code=candidate.key.gauge.value,
            lindas_kind=candidate.key.kind,
            received_at=candidate.received_at,
            registry_version=candidate.registry_version,
            registry_sha256=candidate.registry_sha256,
            input_digest=candidate.input_digest,
        )
    )


def drain_checks(conn: sa.Connection, mode: str) -> None:
    if mode == "none":
        return
    if mode == "rollback":
        conn.execute(sa.text("SAVEPOINT before_checks"))
    conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    if mode == "rollback":
        conn.execute(sa.text("ROLLBACK TO SAVEPOINT before_checks"))
    if mode in {"deferred", "rollback"}:
        conn.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
    # A caller-controlled correlation setting is not authority for finality.
    conn.execute(sa.text("SELECT set_config('sapphire.bafu_unit_batch', '', true)"))


class TestGuardReviewRegressions:
    @pytest.mark.parametrize("value", [0.023, nextafter(0.022, 1.0)])
    @pytest.mark.parametrize("path", ["sql", "store"])
    def test_low_precision_cannot_hide_mutation(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        value: float,
        path: str,
    ) -> None:
        assert (
            PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected).status
            is BafuWriteStatus.COMMITTED
        )
        with worker.connect() as conn:
            tx = conn.begin()
            conn.execute(sa.text("SET LOCAL extra_float_digits = -15"))
            if path == "sql":
                conn.execute(
                    sa.update(observations)
                    .where(observations.c.station_id == protected.station_id)
                    .values(value=value)
                )
            else:
                PgObservationStore(conn).store_raw_observations(
                    [replace(protected.observations[0], value=value)]
                )
            with pytest.raises(DBAPIError, match="lacks current admitted proof"):
                tx.commit()

    @pytest.mark.parametrize("zone", ["UTC", "Pacific/Auckland"])
    def test_writer_under_output_settings(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch, zone: str
    ) -> None:
        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                conn.execute(sa.text("SET LOCAL extra_float_digits = -15"))
                conn.execute(
                    sa.text("SELECT set_config('TimeZone', :zone, true)"),
                    {"zone": zone},
                )
                yield conn

        writer = PgBafuUnitWriter(transaction, quota=QUOTA)
        assert writer.write(protected).status is BafuWriteStatus.COMMITTED
        assert (
            writer.write(later(protected, literal="23")).status
            is BafuWriteStatus.COMMITTED
        )

    @pytest.mark.parametrize("boundary", ["admission", "effect"])
    def test_stale_isolation_rejected(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch, boundary: str
    ) -> None:
        PgBafuBatchJournal(worker.begin).admit(protected)
        with (
            worker.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            conn.execute(sa.text("SELECT count(*) FROM bafu_unit_batch_admissions"))
            PgBafuBatchJournal(worker.begin).admit(later(protected))
            with pytest.raises(DBAPIError, match="READ COMMITTED"):
                if boundary == "admission":
                    append_admission(conn, later(later(protected)))
                else:
                    conn.execute(
                        sa.insert(events).values(
                            batch_id=protected.batch_id,
                            tenant_id=protected.tenant_id,
                            station_id=protected.station_id,
                            outcome="quarantined",
                            fingerprint="a" * 64,
                            capture={},
                            reason="test",
                        )
                    )

    @pytest.mark.parametrize("kind", ["quarantine", "evidence", "receipt"])
    @pytest.mark.parametrize("drain", ["none", "immediate", "deferred", "rollback"])
    def test_same_transaction_supersession_rejected(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        kind: str,
        drain: str,
    ) -> None:
        candidate = batch
        if kind != "quarantine":
            candidate = normalized(batch)
            if kind == "receipt":
                candidate = replace(
                    candidate,
                    observations=(
                        *candidate.observations,
                        replace(
                            candidate.observations[0],
                            parameter="water_level",
                            value=4.1,
                        ),
                    ),
                )
            assert (
                PgBafuUnitWriter(worker.begin, quota=QUOTA).write(candidate).status
                is BafuWriteStatus.COMMITTED
            )
            candidate = later(candidate)
            if kind == "evidence":
                assert isinstance(
                    candidate.captures[0].result, NormalizedDischargeCapture
                )
                rule = replace(
                    candidate.registry.rules[0], evidence_reference="new-proof"
                )
                candidate = replace(
                    candidate,
                    registry=replace(candidate.registry, rules=(rule,)),
                    captures=(
                        replace(
                            candidate.captures[0],
                            result=replace(
                                candidate.captures[0].result,
                                resolution=replace(
                                    candidate.captures[0].result.resolution, rule=rule
                                ),
                            ),
                        ),
                    ),
                )
            else:
                candidate = replace(
                    candidate,
                    observations=tuple(
                        replace(obs, value=4.2)
                        if obs.parameter == "water_level"
                        else obs
                        for obs in candidate.observations
                    ),
                )

        commit_errors: list[str] = []

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            try:
                with worker.begin() as conn:
                    yield conn
                    summary = conn.scalar(
                        sa.select(receipts.c.summary).where(
                            receipts.c.batch_id == candidate.batch_id
                        )
                    )
                    if summary is not None:
                        expected = {
                            "quarantine": "quarantined",
                            "evidence": "evidence_only",
                            "receipt": "unchanged",
                        }[kind]
                        assert summary["counts"][expected] == 1
                        drain_checks(conn, drain)
                        append_admission(conn, later(candidate))

            except DBAPIError as exc:
                assert getattr(exc.orig, "sqlstate", None) == "PBU01"
                commit_errors.append(str(exc))
                raise

        with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
            result = PgBafuUnitWriter(transaction, quota=QUOTA).write(candidate)
            assert result.status is BafuWriteStatus.COMMITTED

        assert len(commit_errors) == 1
        assert "current-transaction effects" in commit_errors[0]
        assert "PBU01" in commit_errors[0] or "BAFU" in commit_errors[0]

    @pytest.mark.parametrize("path", ["sql", "python"])
    def test_admission_waits_for_effect_station(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch, path: str
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        barrier = Barrier(2)
        candidate = later(protected)
        PgBafuBatchJournal(worker.begin).admit(protected)

        @contextmanager
        def bounded_transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                conn.execute(sa.text("SET LOCAL statement_timeout = '1s'"))
                yield conn

        def attempt() -> None:
            barrier.wait(timeout=10)
            if path == "sql":
                with bounded_transaction() as conn:
                    append_admission(conn, candidate)
            else:
                # Journal sets its own bounded lock_timeout (5s).
                PgBafuBatchJournal(worker.begin).admit(candidate)

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                yield conn
                if conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(receipts)
                    .where(receipts.c.batch_id == protected.batch_id)
                ):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(attempt)
                        barrier.wait(timeout=10)
                        with pytest.raises(DBAPIError, match="timeout"):
                            future.result(timeout=10)

        assert (
            PgBafuUnitWriter(transaction, quota=QUOTA).write(protected).status
            is BafuWriteStatus.COMMITTED
        )

    def test_direct_admission_binding_rejected(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        with (
            worker.begin() as conn,
            pytest.raises(DBAPIError, match="binding mismatch"),
        ):
            conn.execute(
                sa.insert(admissions).values(
                    batch_id=protected.batch_id,
                    disposition="rejected",
                    tenant_id=protected.tenant_id,
                    station_id=protected.station_id,
                    gauge_code="9999",
                    lindas_kind=protected.key.kind,
                    received_at=protected.received_at,
                    registry_version=protected.registry_version,
                    registry_sha256=protected.registry_sha256,
                    input_digest=protected.input_digest,
                )
            )

    def test_signed_zero_remains_semantically_equal(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        candidate = normalized(protected, literal="0")
        assert (
            PgBafuUnitWriter(worker.begin, quota=QUOTA).write(candidate).status
            is BafuWriteStatus.COMMITTED
        )
        with worker.begin() as conn:
            conn.execute(sa.text("SET LOCAL extra_float_digits = -15"))
            conn.execute(
                sa.update(observations)
                .where(observations.c.station_id == candidate.station_id)
                .values(value=-0.0)
            )

    @pytest.mark.parametrize("drain", ["immediate", "deferred", "rollback"])
    @pytest.mark.parametrize(
        "change",
        [
            "value",
            "delivery_id",
            "timestamp",
            "source",
            "parameter",
            "id",
            "station_id",
        ],
    )
    def test_receipt_snapshot_cannot_change_after_checks(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        drain: str,
        change: str,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        from datetime import timedelta
        from uuid import uuid4

        candidate = normalized(batch)
        candidate = replace(
            candidate,
            observations=(
                *candidate.observations,
                replace(candidate.observations[0], parameter="water_level", value=4.1),
            ),
        )
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.ids import StationId
        from tests.conftest import make_station_config

        destination = StationId(uuid4())
        with isolated_committed_db_engine.begin() as conn:
            PgStationStore(conn).store_station(
                make_station_config(
                    station_id=destination,
                    code=str(destination),
                    tenant_id=batch.tenant_id,
                )
            )
        errors: list[DBAPIError] = []

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            try:
                with worker.begin() as conn:
                    yield conn
                    if (
                        conn.scalar(
                            sa.select(receipts.c.batch_id).where(
                                receipts.c.batch_id == candidate.batch_id
                            )
                        )
                        is not None
                    ):
                        drain_checks(conn, drain)
                        changes = {
                            "value": 4.2,
                            "delivery_id": "late-delivery",
                            "timestamp": candidate.received_at + timedelta(days=2),
                            "source": "manual_import",
                            "parameter": "temperature",
                            "id": uuid4(),
                            "station_id": destination,
                        }
                        conn.execute(
                            sa.update(observations)
                            .where(
                                observations.c.station_id == candidate.station_id,
                                observations.c.parameter == "water_level",
                            )
                            .values(**{change: changes[change]})
                        )
            except DBAPIError as exc:
                errors.append(exc)
                raise

        with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
            result = PgBafuUnitWriter(transaction, quota=QUOTA).write(candidate)
            assert result.status is BafuWriteStatus.COMMITTED
        assert len(errors) == 1
        assert getattr(errors[0].orig, "sqlstate", None) == "PBU01"
        assert "current-transaction receipt" in str(errors[0])

    @pytest.mark.parametrize(
        "mode", ["qc", "signed_zero", "rejected", "rollback_mutation"]
    )
    def test_early_checks_preserve_legal_operations(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        mode: str,
    ) -> None:
        candidate = normalized(batch, literal="0")
        candidate = replace(
            candidate,
            observations=(
                *candidate.observations,
                replace(candidate.observations[0], parameter="water_level", value=0.0),
            ),
        )
        rejected = later(candidate)

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                yield conn
                if (
                    conn.scalar(
                        sa.select(receipts.c.batch_id).where(
                            receipts.c.batch_id == candidate.batch_id
                        )
                    )
                    is None
                ):
                    return
                drain_checks(conn, "deferred")
                if mode == "rejected":
                    # Equal received time is demoted and remains durably ID-bound.
                    append_admission(
                        conn, replace(candidate, batch_id=rejected.batch_id)
                    )
                elif mode == "rollback_mutation":
                    with (
                        pytest.raises(DBAPIError, match="current-transaction receipt"),
                        conn.begin_nested(),
                    ):
                        conn.execute(
                            sa.update(observations)
                            .where(
                                observations.c.station_id == candidate.station_id,
                                observations.c.parameter == "water_level",
                            )
                            .values(value=9.0)
                        )
                    # Rollback of a rejected attempt cannot erase surviving proof.
                    with (
                        pytest.raises(DBAPIError, match="current-transaction effects"),
                        conn.begin_nested(),
                    ):
                        append_admission(conn, rejected)
                else:
                    conn.execute(
                        sa.update(observations)
                        .where(
                            observations.c.station_id == candidate.station_id,
                        )
                        .values(
                            **(
                                {"qc_rule_version": "late-qc"}
                                if mode == "qc"
                                else {"value": -0.0}
                            )
                        )
                    )

        assert (
            PgBafuUnitWriter(transaction, quota=QUOTA).write(candidate).status
            is BafuWriteStatus.COMMITTED
        )
        if mode == "rejected":
            with worker.connect() as conn:
                assert (
                    conn.scalar(
                        sa.select(admissions.c.disposition).where(
                            admissions.c.batch_id == rejected.batch_id
                        )
                    )
                    == "rejected"
                )
            with pytest.raises(
                BafuBatchIntegrityError, match="different captured input"
            ):
                PgBafuBatchJournal(worker.begin).admit(rejected)
        # Previously committed receipts are NOT frozen by the transaction seal.
        with worker.begin() as conn:
            conn.execute(
                sa.update(observations)
                .where(
                    observations.c.station_id == candidate.station_id,
                    observations.c.parameter == "water_level",
                )
                .values(value=7.0)
            )
        assert (
            PgBafuUnitWriter(worker.begin, quota=QUOTA).write(later(rejected)).status
            is BafuWriteStatus.COMMITTED
        )

    def test_rolled_back_receipt_does_not_leave_phantom_seal(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        candidate = normalized(batch)
        next_batch = later(candidate)

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                savepoint = conn.begin_nested()
                yield conn
                if (
                    conn.scalar(
                        sa.select(receipts.c.batch_id).where(
                            receipts.c.batch_id == candidate.batch_id
                        )
                    )
                    is not None
                ):
                    drain_checks(conn, "immediate")
                    savepoint.rollback()
                    append_admission(conn, next_batch)
                else:
                    savepoint.commit()

        PgBafuUnitWriter(transaction, quota=QUOTA).write(candidate)
        with worker.connect() as conn:
            assert (
                conn.scalar(
                    sa.select(receipts.c.batch_id).where(
                        receipts.c.batch_id == candidate.batch_id
                    )
                )
                is None
            )
            assert (
                conn.scalar(
                    sa.select(admissions.c.disposition).where(
                        admissions.c.batch_id == next_batch.batch_id
                    )
                )
                == "admitted"
            )
