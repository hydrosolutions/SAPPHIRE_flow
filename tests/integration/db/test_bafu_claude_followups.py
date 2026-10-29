from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import observations
from sapphire_flow.store.bafu_batch_journal import BafuBatchIntegrityError
from sapphire_flow.store.bafu_unit_documents import document_digest, encode_document
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from tests.integration.db.test_bafu_guard_regressions import (
    batch as batch,
)
from tests.integration.db.test_bafu_guard_regressions import (
    bootstrapped as bootstrapped,
)
from tests.integration.db.test_bafu_guard_regressions import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)
from tests.integration.db.test_bafu_guard_regressions import (
    protected as protected,
)
from tests.integration.db.test_bafu_guard_regressions import (
    role_harness as role_harness,
)
from tests.integration.db.test_bafu_guard_regressions import (
    worker as worker,
)
from tests.integration.store.test_bafu_unit_writer import QUOTA

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch


class TestClaudeFollowups:
    def test_created_at_is_not_qc(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        with worker.connect() as conn:
            tx = conn.begin()
            conn.execute(
                sa.update(observations)
                .where(observations.c.station_id == protected.station_id)
                .values(created_at=protected.received_at + timedelta(days=1))
            )
            with pytest.raises(DBAPIError, match="unsupported non-QC"):
                tx.commit()

    def test_control_unicode_keys_and_values(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        chars = [chr(n) for n in (*range(1, 33), *range(127, 160))] + [
            "é",
            "😀",
            chr(0x10FFFF),
        ]
        with isolated_committed_db_engine.connect() as conn:
            for char in chars:
                data = {char: char}
                actual = conn.scalar(
                    sa.text(
                        "SELECT "
                        "encode(sha256(convert_to(public.bafu_unit_canonical_json("
                        "CAST(:data AS jsonb)), 'UTF8')), 'hex')"
                    ),
                    {"data": encode_document(data)},
                )
                assert actual == document_digest(data), repr(char)

    def test_postgres_rejects_nul(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        with (
            isolated_committed_db_engine.begin() as conn,
            pytest.raises(DBAPIError, match="unsupported Unicode escape"),
        ):
            conn.execute(
                sa.text("SELECT CAST(:data AS jsonb)"),
                {"data": encode_document({"nul": "\0"})},
            )

    @pytest.mark.parametrize("fault", ["duplicates", "counts", "other_state"])
    def test_receipt_consistency(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        fault: str,
    ) -> None:
        from dataclasses import replace

        candidate = replace(
            protected,
            observations=(
                *protected.observations,
                replace(protected.observations[0], parameter="water_level", value=4.1),
            ),
        )
        changes = {
            "duplicates": """NEW.result := jsonb_set(NEW.result, '{event_ids}',
                (NEW.result->'event_ids') || (NEW.result->'event_ids'));
                NEW.result := jsonb_set(NEW.result, '{expected_captures}', '2');
                NEW.result := jsonb_set(NEW.result, '{counts,inserted}', '2');""",
            "counts": """NEW.result := jsonb_set(NEW.result, '{counts,inserted}', '0');
                NEW.result := jsonb_set(NEW.result, '{counts,restated}', '1');""",
            "other_state": """NEW.result := jsonb_set(NEW.result,
                '{observations,0,value}', '999');""",
        }
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE FUNCTION claude_receipt_fault() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN "
                    + changes[fault]
                    + """
                NEW.result_digest := encode(sha256(convert_to(
                    public.bafu_unit_canonical_json(NEW.result), 'UTF8')), 'hex');
                RETURN NEW; END $$"""
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TRIGGER aaa_claude_receipt_fault BEFORE INSERT "
                    "ON bafu_unit_batch_receipts FOR EACH ROW "
                    "EXECUTE FUNCTION claude_receipt_fault()"
                )
            )
        try:
            with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
                PgBafuUnitWriter(worker.begin, quota=QUOTA).write(candidate)
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DROP TRIGGER aaa_claude_receipt_fault ON "
                        "bafu_unit_batch_receipts"
                    )
                )
                conn.execute(sa.text("DROP FUNCTION claude_receipt_fault()"))

    def test_receipt_only_keeps_earlier_unchanged_reference(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.types.bafu_write_result import BafuWriteStatus
        from tests.integration.store.test_bafu_unit_writer import later

        writer = PgBafuUnitWriter(worker.begin, quota=QUOTA)
        first = writer.write(protected)
        candidate = later(protected)
        candidate = replace(
            candidate,
            observations=(
                *candidate.observations,
                replace(candidate.observations[0], parameter="water_level", value=4.1),
            ),
        )
        second = writer.write(candidate)
        assert second.status is BafuWriteStatus.COMMITTED
        assert second.event_ids == first.event_ids
        assert second.counts is not None
        assert second.counts.unchanged == 1

    def test_qc_change_still_works(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        with worker.begin() as conn:
            conn.execute(sa.text("SET LOCAL extra_float_digits = -15"))
            conn.execute(
                sa.update(observations)
                .where(observations.c.station_id == protected.station_id)
                .values(qc_status="qc_unchecked", qc_rule_version="test")
            )

    @pytest.mark.parametrize("field", ["station_id", "tenant_id"])
    def test_event_scope_composite_fk(
        self, worker: sa.Engine, batch: BafuStationCaptureBatch, field: str
    ) -> None:
        from uuid import uuid4

        from sapphire_flow.db.metadata import bafu_unit_events
        from sapphire_flow.store.bafu_batch_journal import PgBafuBatchJournal
        from sapphire_flow.store.bafu_unit_documents import capture_document

        PgBafuBatchJournal(worker.begin).admit(batch)
        capture = capture_document(
            batch.captures[0],
            registry_version=batch.registry_version,
            registry_sha256=batch.registry_sha256,
        )
        values: dict[str, Any] = dict(
            batch_id=batch.batch_id,
            tenant_id=batch.tenant_id,
            station_id=batch.station_id,
            outcome="quarantined",
            reason="test",
            measurement_time=batch.captures[0].result.measurement_time,
            capture=capture,
            fingerprint=document_digest(capture["scientific"]),
        )
        values[field] = uuid4()
        with worker.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
                {"batch": str(batch.batch_id)},
            )
            with pytest.raises(DBAPIError, match="fk_bafu_event_batch"):
                conn.execute(sa.insert(bafu_unit_events).values(**values))

    def test_keyed_projection_plan(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        import json
        from dataclasses import replace
        from pathlib import Path

        from tests.integration.store.test_bafu_unit_writer import later

        writer = PgBafuUnitWriter(
            worker.begin,
            quota=replace(
                QUOTA,
                max_station_events=1000,
                max_station_receipts=1000,
                max_station_evidence_bytes=10_000_000,
            ),
        )
        writer.write(protected)
        candidate = protected
        for number in range(128):
            candidate = later(candidate, literal=str(number + 23))
            writer.write(candidate)
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(sa.text("ANALYZE bafu_unit_batch_receipts"))
        with worker.connect() as conn:
            plan = conn.scalar(
                sa.text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                    "SELECT digest_valid FROM public.bafu_unit_receipt_reconciliation "
                    "WHERE batch_id = :batch"
                ),
                {"batch": protected.batch_id},
            )
        Path(
            "/tmp/daily-evaluation-implementation-evidence/overnight-return/must-have/"
            "p1-inventory/readiness/unit-write-guards/claude-followups/keyed-plan.json"
        ).write_text(json.dumps(plan, indent=2))
        assert plan[0]["Plan"]["Actual Rows"] == 1
        with worker.connect() as conn:
            seal_plan = conn.scalar(
                sa.text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                    "SELECT states, summary FROM bafu_unit_receipt_reconciliation "
                    "WHERE transaction_id = pg_current_xact_id()::text AND batch_id = ("
                    "SELECT batch_id FROM bafu_unit_batch_admissions "
                    "WHERE tenant_id = :tenant AND station_id = :station "
                    "AND disposition = 'admitted' ORDER BY sequence DESC LIMIT 1)"
                ),
                {"tenant": protected.tenant_id, "station": protected.station_id},
            )
        Path(
            "/tmp/daily-evaluation-implementation-evidence/overnight-return/must-have/"
            "p1-inventory/readiness/unit-write-guards/early-constraint-fix/seal-plan.json"
        ).write_text(json.dumps(seal_plan, indent=2))
        assert seal_plan[0]["Plan"]["Actual Rows"] == 0

    def test_catalog_contract(self, isolated_committed_db_engine: sa.Engine) -> None:
        with isolated_committed_db_engine.connect() as conn:
            for role in ("sapphire_worker", "sapphire_api", "sapphire_operator"):
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege(:role, "
                        "'public.observations', 'TRUNCATE')"
                    ),
                    {"role": role},
                )
                assert not conn.scalar(
                    sa.text("SELECT rolsuper FROM pg_roles WHERE rolname=:role"),
                    {"role": role},
                )
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege(:role, "
                        "'public.bafu_unit_read_holds', 'approval_sha256', 'INSERT')"
                    ),
                    {"role": role},
                )
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege(:role, "
                        "'public.bafu_unit_events', 'capture', 'SELECT')"
                    ),
                    {"role": role},
                )
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege(:role, "
                        "'public.bafu_unit_batch_receipts', 'result', 'SELECT')"
                    ),
                    {"role": role},
                )
            assert conn.scalar(
                sa.text(
                    "SELECT attnotnull FROM pg_attribute WHERE "
                    "attrelid='public.bafu_unit_events'::regclass AND attname='capture'"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_default_acl d, "
                    "LATERAL aclexplode(d.defaclacl) a JOIN pg_roles r "
                    "ON r.oid=a.grantee WHERE r.rolname IN "
                    "('sapphire_worker','sapphire_api','sapphire_operator'))"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT rolcanlogin FROM pg_roles "
                    "WHERE rolname='sapphire_bafu_maintenance'"
                )
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_column_privilege('sapphire_bafu_maintenance', "
                    "'public.bafu_unit_read_holds', 'approval_sha256', 'INSERT')"
                )
            )
            assert not conn.scalar(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_class c "
                    "JOIN pg_roles r ON r.oid=c.relowner WHERE "
                    "c.relname IN ('observations', 'bafu_unit_event_reconciliation', "
                    "'bafu_unit_receipt_reconciliation') AND r.rolname IN "
                    "('sapphire_worker', 'sapphire_api', 'sapphire_operator', "
                    "'sapphire_bafu_maintenance'))"
                )
            )

    def test_hold_registry_bound_to_admission(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text("""CREATE FUNCTION claude_hold_fault() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN NEW.registry_sha256 := repeat('f',64);
                RETURN NEW; END $$""")
            )
            conn.execute(
                sa.text(
                    "CREATE TRIGGER aaa_claude_hold_fault BEFORE INSERT "
                    "ON bafu_unit_read_holds FOR EACH ROW EXECUTE FUNCTION "
                    "claude_hold_fault()"
                )
            )
        try:
            with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
                PgBafuUnitWriter(worker.begin, quota=QUOTA).write(batch)
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DROP TRIGGER aaa_claude_hold_fault ON bafu_unit_read_holds"
                    )
                )
                conn.execute(sa.text("DROP FUNCTION claude_hold_fault()"))

    @pytest.mark.parametrize("inherited", [False, True])
    def test_worker_maintenance_authority_is_not_role_name(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        inherited: bool,
    ) -> None:
        from uuid import uuid4

        with isolated_committed_db_engine.begin() as conn:
            if inherited:
                conn.execute(
                    sa.text("CREATE ROLE claude_inherited_worker NOLOGIN INHERIT")
                )
                conn.execute(
                    sa.text("GRANT sapphire_worker TO claude_inherited_worker")
                )
        try:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "SET LOCAL ROLE "
                        + (
                            "claude_inherited_worker"
                            if inherited
                            else "sapphire_worker"
                        )
                    )
                )
                with pytest.raises(DBAPIError, match="cannot approve maintenance"):
                    conn.execute(
                        sa.text("""INSERT INTO bafu_unit_read_holds
                        (id,tenant_id,station_id,starts_at,reason,origin,registry_sha256)
                        VALUES (:id,:tenant,:station,:start,
                            'test','maintenance',:registry)
                        """),
                        dict(
                            id=uuid4(),
                            tenant=batch.tenant_id,
                            station=batch.station_id,
                            start=batch.received_at,
                            registry=batch.registry_sha256,
                        ),
                    )
        finally:
            if inherited:
                with isolated_committed_db_engine.begin() as conn:
                    conn.execute(sa.text("DROP ROLE claude_inherited_worker"))

    def test_schema_drift_fails_closed(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text("ALTER TABLE observations ADD COLUMN new_metadata text")
            )
        try:
            with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
                PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text("ALTER TABLE observations DROP COLUMN new_metadata")
                )

    def test_generic_raise_is_not_mapped_to_unit_integrity(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
    ) -> None:
        from contextlib import contextmanager

        from sapphire_flow.types.bafu_write_result import BafuWriteStatus

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with worker.begin() as conn:
                yield conn
                if conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM bafu_unit_receipt_reconciliation "
                        "WHERE batch_id = :batch"
                    ),
                    {"batch": protected.batch_id},
                ):
                    conn.execute(
                        sa.text("DO $$ BEGIN RAISE EXCEPTION 'unrelated'; END $$")
                    )

        result = PgBafuUnitWriter(transaction, quota=QUOTA).write(protected)
        assert result.status is BafuWriteStatus.NOT_COMMITTED

    def test_mixed_new_and_unchanged_receipt(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
        from sapphire_flow.types.bafu_write_result import BafuWriteStatus
        from sapphire_flow.types.datetime import ensure_utc
        from tests.integration.store.test_bafu_unit_writer import later

        writer = PgBafuUnitWriter(worker.begin, quota=QUOTA)
        first = writer.write(protected)
        candidate = later(protected)
        capture = candidate.captures[0]
        assert isinstance(capture.result, NormalizedDischargeCapture)
        timestamp = ensure_utc(
            candidate.observations[0].timestamp + timedelta(microseconds=1)
        )
        candidate = replace(
            candidate,
            captures=(
                capture,
                replace(
                    capture,
                    measurement_literals=(timestamp.isoformat(),),
                    result=replace(capture.result, measurement_time=timestamp),
                ),
            ),
            observations=(
                *candidate.observations,
                replace(candidate.observations[0], timestamp=timestamp),
            ),
        )
        second = writer.write(candidate)
        assert second.status is BafuWriteStatus.COMMITTED
        assert second.event_ids[0] == first.event_ids[0]
        assert second.counts is not None
        assert second.counts.unchanged == 1
        assert second.counts.inserted == 1

    @pytest.mark.parametrize(
        "field", ["station_id", "tenant_id", "parameter", "source"]
    )
    def test_hold_existing_scope_constraints(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        field: str,
    ) -> None:
        from contextlib import contextmanager
        from uuid import uuid4

        from sapphire_flow.types.bafu_write_result import BafuWriteStatus

        value = str(uuid4()) if field.endswith("_id") else "wrong"
        with isolated_committed_db_engine.begin() as conn:
            if field == "station_id":
                from uuid import UUID

                from sapphire_flow.store.station_store import PgStationStore
                from sapphire_flow.types.ids import StationId
                from tests.conftest import make_station_config

                PgStationStore(conn).store_station(
                    make_station_config(
                        station_id=StationId(UUID(value)),
                        code="7998",
                        tenant_id=batch.tenant_id,
                    )
                )
            conn.execute(
                sa.text(
                    "CREATE FUNCTION claude_hold_scope_fault() RETURNS trigger "
                    f"LANGUAGE plpgsql AS $$ BEGIN NEW.{field} := '{value}'; RETURN "
                    f"NEW; END $$"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TRIGGER aaa_claude_hold_scope_fault BEFORE INSERT "
                    "ON bafu_unit_read_holds FOR EACH ROW EXECUTE FUNCTION "
                    "claude_hold_scope_fault()"
                )
            )
        failures: list[str] = []

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            try:
                with worker.begin() as conn:
                    yield conn
            except DBAPIError as exc:
                failures.append(str(exc))
                raise

        try:
            assert (
                PgBafuUnitWriter(transaction, quota=QUOTA).write(batch).status
                is BafuWriteStatus.NOT_COMMITTED
            )
            expected = (
                "foreign key constraint"
                if field.endswith("_id")
                else "check constraint"
            )
            assert len(failures) == 1 and expected in failures[0]
            if field == "station_id":
                assert "fk_bafu_hold_event" in failures[0]
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DROP TRIGGER aaa_claude_hold_scope_fault ON "
                        "bafu_unit_read_holds"
                    )
                )
                conn.execute(sa.text("DROP FUNCTION claude_hold_scope_fault()"))

    def test_cross_station_receipt_reference_rejected(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        from dataclasses import replace
        from uuid import uuid4

        from sapphire_flow.db.metadata import bafu_unit_batch_receipts
        from sapphire_flow.store.bafu_batch_journal import PgBafuBatchJournal
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.bafu_capture_batch import BafuCaptureBatchId
        from sapphire_flow.types.bafu_unit_registry import BafuGaugeCode
        from sapphire_flow.types.ids import StationId
        from tests.conftest import make_station_config

        first = PgBafuUnitWriter(worker.begin, quota=QUOTA).write(batch)
        station = StationId(uuid4())
        key = replace(batch.key, gauge=BafuGaugeCode(value="7999"))
        with isolated_committed_db_engine.begin() as conn:
            PgStationStore(conn).store_station(
                make_station_config(
                    station_id=station, code="7999", tenant_id=batch.tenant_id
                )
            )
        candidate = replace(
            batch,
            station_id=station,
            key=key,
            batch_id=BafuCaptureBatchId(uuid4()),
            captures=(replace(batch.captures[0], key=key),),
        )
        PgBafuBatchJournal(worker.begin).admit(candidate)
        result = dict(
            schema_version=1,
            input_digest=candidate.input_digest,
            expected_captures=1,
            event_ids=list(first.event_ids),
            observations=[],
            counts=dict(
                inserted=0,
                restated=0,
                unchanged=1,
                quarantined=0,
                rejected=0,
                evidence_only=0,
            ),
        )
        with worker.connect() as conn:
            tx = conn.begin()
            conn.execute(
                sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
                {"batch": str(candidate.batch_id)},
            )
            conn.execute(
                sa.insert(bafu_unit_batch_receipts).values(
                    batch_id=candidate.batch_id,
                    input_digest=candidate.input_digest,
                    result=result,
                    result_digest=document_digest(result),
                )
            )
            with pytest.raises(DBAPIError, match="receipt event scope mismatch"):
                tx.commit()

    @pytest.mark.parametrize(
        "role", ["sapphire_worker", "sapphire_api", "sapphire_operator"]
    )
    @pytest.mark.parametrize(
        "command",
        [
            "TRUNCATE public.observations",
            "ALTER TABLE public.observations DISABLE TRIGGER bafu_observation_guard",
            "SET LOCAL session_replication_role = replica",
        ],
    )
    def test_runtime_cannot_disable_protection(
        self,
        isolated_committed_db_engine: sa.Engine,
        role: str,
        command: str,
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE " + role))
            with pytest.raises(DBAPIError, match="permission denied|must be owner"):
                conn.execute(sa.text(command))

    @pytest.mark.parametrize("role", [None, "sapphire_bafu_maintenance"])
    def test_positive_maintenance_authority(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        role: str | None,
    ) -> None:
        from uuid import uuid4

        with isolated_committed_db_engine.begin() as conn:
            if role is not None:
                conn.execute(sa.text("SET LOCAL ROLE " + role))
            conn.execute(
                sa.text("""INSERT INTO bafu_unit_read_holds
                (id,tenant_id,station_id,starts_at,reason,origin,registry_sha256,approval_sha256)
                VALUES (:id,:tenant,:station,:start,
                    'test','maintenance',:registry,:approval)"""),
                dict(
                    id=uuid4(),
                    tenant=batch.tenant_id,
                    station=batch.station_id,
                    start=batch.received_at,
                    registry=batch.registry_sha256,
                    approval="a" * 64,
                ),
            )

    @pytest.mark.parametrize("fault", ["not_latest", "changed_row"])
    def test_old_receipt_reference_must_remain_applicable(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        fault: str,
    ) -> None:
        from sapphire_flow.db.metadata import bafu_unit_batch_receipts
        from sapphire_flow.store.bafu_batch_journal import PgBafuBatchJournal
        from tests.integration.store.test_bafu_unit_writer import later, normalized

        candidate = normalized(batch)
        writer = PgBafuUnitWriter(worker.begin, quota=QUOTA)
        first = writer.write(candidate)
        if fault == "not_latest":
            candidate = later(candidate, literal="44")
            writer.write(candidate)
        else:
            with worker.begin() as conn:
                conn.execute(
                    sa.update(observations)
                    .where(observations.c.station_id == batch.station_id)
                    .values(value=4.0)
                )
        candidate = later(candidate)
        PgBafuBatchJournal(worker.begin).admit(candidate)
        result = dict(
            schema_version=1,
            input_digest=candidate.input_digest,
            expected_captures=1,
            event_ids=list(first.event_ids),
            observations=[],
            counts=dict(
                inserted=0,
                restated=0,
                unchanged=1,
                quarantined=0,
                rejected=0,
                evidence_only=0,
            ),
        )
        with worker.connect() as conn:
            tx = conn.begin()
            conn.execute(
                sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
                {"batch": str(candidate.batch_id)},
            )
            conn.execute(
                sa.insert(bafu_unit_batch_receipts).values(
                    batch_id=candidate.batch_id,
                    input_digest=candidate.input_digest,
                    result=result,
                    result_digest=document_digest(result),
                )
            )
            with pytest.raises(DBAPIError, match="receipt unchanged"):
                tx.commit()


class TestObservationCandidateIsolation:
    def test_repeatable_read_without_visible_scope_is_rejected(
        self, worker: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        with worker.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn:
            transaction = conn.begin()
            conn.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=batch.station_id,
                    timestamp=batch.received_at,
                    parameter="discharge",
                    source="measured",
                    value=1.0,
                    qc_status="raw",
                )
            )
            with pytest.raises(
                DBAPIError, match="candidate effects require READ COMMITTED"
            ):
                transaction.commit()
