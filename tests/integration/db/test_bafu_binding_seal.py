from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import observations, stations
from sapphire_flow.store.bafu_batch_journal import BafuBatchIntegrityError
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from sapphire_flow.types.bafu_write_result import BafuWriteStatus
from tests.integration.db.test_bafu_guard_regressions import (
    append_admission,
    drain_checks,
    receipts,
)
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
    role_harness as role_harness,
)
from tests.integration.db.test_bafu_guard_regressions import (
    worker as worker,
)
from tests.integration.store.test_bafu_unit_writer import QUOTA, normalized

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch


class TestBindingSeal:
    @pytest.mark.parametrize("attack", ["network_toggle", "late_before"])
    def test_drained_receipt_cannot_be_bypassed(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        attack: str,
    ) -> None:
        candidate = normalized(batch)
        candidate = replace(
            candidate,
            observations=(
                *candidate.observations,
                replace(candidate.observations[0], parameter="water_level", value=4.1),
            ),
        )
        with isolated_committed_db_engine.begin() as conn:
            assert (
                conn.scalar(sa.text("SELECT count(*) FROM bafu_unit_write_scopes")) == 0
            )
            if attack == "late_before":
                conn.execute(
                    sa.text("""CREATE FUNCTION test_late_rewrite() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                    IF NEW.parameter = 'water_level' THEN
                        NEW.value := NEW.value + 1;
                    END IF;
                    RETURN NEW; END $$""")
                )
                conn.execute(
                    sa.text("""CREATE TRIGGER zzz_test_late_rewrite
                    BEFORE UPDATE ON observations FOR EACH ROW
                    EXECUTE FUNCTION test_late_rewrite()""")
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
                        is None
                    ):
                        return
                    drain_checks(conn, "deferred")
                    if attack == "network_toggle":
                        conn.execute(
                            sa.update(stations)
                            .where(stations.c.id == batch.station_id)
                            .values(network="dhm")
                        )
                    conn.execute(
                        sa.update(observations)
                        .where(
                            observations.c.station_id == batch.station_id,
                            observations.c.parameter == "water_level",
                        )
                        .values(
                            **(
                                {"value": 9.0}
                                if attack == "network_toggle"
                                else {"qc_rule_version": "late-qc"}
                            )
                        )
                    )
                    if attack == "network_toggle":
                        conn.execute(
                            sa.update(stations)
                            .where(stations.c.id == batch.station_id)
                            .values(network="bafu")
                        )
            except DBAPIError as exc:
                errors.append(exc)
                raise

        try:
            with pytest.raises(BafuBatchIntegrityError, match="database unit guard"):
                result = PgBafuUnitWriter(transaction, quota=QUOTA).write(candidate)
                assert result.status is BafuWriteStatus.COMMITTED
                pytest.fail(f"UNSAFE ACTUAL COMMITTED: {attack}")
            assert len(errors) == 1
            assert getattr(errors[0].orig, "sqlstate", None) == "PBU01"
            assert (
                "binding is immutable"
                if attack == "network_toggle"
                else "current-transaction receipt"
            ) in str(errors[0])
        finally:
            if attack == "late_before":
                with isolated_committed_db_engine.begin() as conn:
                    conn.execute(
                        sa.text("DROP TRIGGER zzz_test_late_rewrite ON observations")
                    )
                    conn.execute(sa.text("DROP FUNCTION test_late_rewrite()"))

    @pytest.mark.parametrize("disposition", ["admitted", "rejected"])
    @pytest.mark.parametrize("same_transaction", [True, False])
    @pytest.mark.parametrize(
        "field,value", [("network", "dhm"), ("code", "9998"), ("station_kind", "lake")]
    )
    def test_any_journal_identity_pins_binding(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        disposition: str,
        same_transaction: bool,
        field: str,
        value: str,
    ) -> None:
        def admit(conn: sa.Connection) -> None:
            if disposition == "rejected":
                # Rejected-only admissions also pin the binding.
                from sapphire_flow.db.metadata import bafu_unit_batch_admissions

                conn.execute(
                    sa.insert(bafu_unit_batch_admissions).values(
                        batch_id=batch.batch_id,
                        tenant_id=batch.tenant_id,
                        station_id=batch.station_id,
                        gauge_code=batch.key.gauge.value,
                        lindas_kind=batch.key.kind,
                        received_at=batch.received_at,
                        registry_version=batch.registry_version,
                        registry_sha256=batch.registry_sha256,
                        input_digest=batch.input_digest,
                        disposition="rejected",
                    )
                )
            else:
                append_admission(conn, batch)

        if not same_transaction:
            with worker.begin() as conn:
                admit(conn)
        with worker.begin() as conn:
            if same_transaction:
                admit(conn)
            with pytest.raises(DBAPIError, match="binding is immutable") as caught:
                conn.execute(
                    sa.update(stations)
                    .where(stations.c.id == batch.station_id)
                    .values(**{field: value})
                )
            assert getattr(caught.value.orig, "sqlstate", None) == "PBU01"

    def test_metadata_and_unjournalled_binding_remain_editable(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        with worker.begin() as conn:
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(network="dhm", code="9998", station_kind="lake")
            )
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(
                    network="bafu",
                    code=batch.key.gauge.value,
                    station_kind=batch.key.kind,
                )
            )
            append_admission(conn, batch)
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(name="Renamed station", altitude_masl=123.0)
            )

    @pytest.mark.parametrize("first", ["admission", "binding"])
    def test_concurrent_binding_and_admission_exclude_each_other(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        first: str,
    ) -> None:
        with worker.connect() as holder, worker.connect() as contender:
            held = holder.begin()
            if first == "admission":
                append_admission(holder, batch)
            else:
                holder.execute(
                    sa.update(stations)
                    .where(stations.c.id == batch.station_id)
                    .values(network="dhm")
                )
            attempted = contender.begin()
            contender.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises(DBAPIError, match="lock timeout") as caught:
                if first == "admission":
                    contender.execute(
                        sa.update(stations)
                        .where(stations.c.id == batch.station_id)
                        .values(network="dhm")
                    )
                else:
                    append_admission(contender, batch)
            assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
            attempted.rollback()
            held.commit()
            with (
                contender.begin(),
                pytest.raises(
                    DBAPIError,
                    match=(
                        "binding is immutable"
                        if first == "admission"
                        else "binding mismatch"
                    ),
                ) as caught,
            ):
                if first == "admission":
                    contender.execute(
                        sa.update(stations)
                        .where(stations.c.id == batch.station_id)
                        .values(network="dhm")
                    )
                else:
                    append_admission(contender, batch)
            assert getattr(caught.value.orig, "sqlstate", None) == "PBU01"

    def test_first_admission_cannot_be_hidden_by_stale_snapshot(
        self, worker: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        with worker.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as stale:
            tx = stale.begin()
            stale.execute(sa.text("SELECT count(*) FROM bafu_unit_batch_admissions"))
            with worker.begin() as fresh:
                append_admission(fresh, batch)
            try:
                stale.execute(
                    sa.update(stations)
                    .where(stations.c.id == batch.station_id)
                    .values(network="dhm")
                )
                tx.commit()
            except DBAPIError as exc:
                tx.rollback()
                assert getattr(exc.orig, "sqlstate", None) == "PBU01"
                assert "READ COMMITTED" in str(exc)
            else:
                pytest.fail("UNSAFE ACTUAL COMMITTED: first-admission stale snapshot")

    @pytest.mark.parametrize(
        "field,value", [("network", "dhm"), ("code", "9998"), ("station_kind", "lake")]
    )
    def test_station_pin_sees_late_before_final_tuple(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        field: str,
        value: str,
    ) -> None:
        with worker.begin() as conn:
            append_admission(conn, batch)
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text(f"""CREATE FUNCTION test_station_rewrite() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN NEW.{field} := '{value}';
                RETURN NEW; END $$""")
            )
            conn.execute(
                sa.text("""CREATE TRIGGER zzz_test_station_rewrite BEFORE UPDATE
                ON stations FOR EACH ROW EXECUTE FUNCTION test_station_rewrite()""")
            )
        try:
            try:
                with worker.begin() as conn:
                    conn.execute(
                        sa.update(stations)
                        .where(stations.c.id == batch.station_id)
                        .values(name="Metadata edit")
                    )
            except DBAPIError as exc:
                assert getattr(exc.orig, "sqlstate", None) == "PBU01"
                assert "binding is immutable" in str(exc)
            else:
                pytest.fail(f"UNSAFE ACTUAL COMMITTED: late station {field}")
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text("DROP TRIGGER zzz_test_station_rewrite ON stations")
                )
                conn.execute(sa.text("DROP FUNCTION test_station_rewrite()"))

    @pytest.mark.parametrize("change", ["value", "delivery_id", "qc_rule_version"])
    @pytest.mark.parametrize("parameter", ["water_level", "discharge"])
    def test_direct_receipt_retains_referenced_row_lock(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        change: str,
        parameter: str,
    ) -> None:
        from sapphire_flow.db.metadata import bafu_unit_batch_receipts
        from sapphire_flow.store.bafu_unit_documents import document_digest
        from tests.integration.store.test_bafu_unit_writer import later

        candidate = normalized(batch)
        candidate = replace(
            candidate,
            observations=(
                *candidate.observations,
                replace(candidate.observations[0], parameter="water_level", value=4.1),
            ),
        )
        assert (
            PgBafuUnitWriter(worker.begin, quota=QUOTA).write(candidate).status
            is BafuWriteStatus.COMMITTED
        )
        # Owner reads private fixture data only; both attack transactions are worker.
        with isolated_committed_db_engine.connect() as conn:
            saved_result = conn.scalar(
                sa.select(bafu_unit_batch_receipts.c.result).where(
                    bafu_unit_batch_receipts.c.batch_id == candidate.batch_id
                )
            )
            assert isinstance(saved_result, dict)
            result = dict(cast("dict[str, Any]", saved_result))
        next_batch = later(candidate)
        result["input_digest"] = next_batch.input_digest
        result["counts"] = {
            key: (1 if key == "unchanged" else 0) for key in result["counts"]
        }
        with worker.connect() as holder:
            tx = holder.begin()
            append_admission(holder, next_batch)
            holder.execute(
                sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
                {"batch": str(next_batch.batch_id)},
            )
            holder.execute(
                sa.insert(bafu_unit_batch_receipts).values(
                    batch_id=next_batch.batch_id,
                    input_digest=next_batch.input_digest,
                    result=result,
                    result_digest=document_digest(result),
                )
            )
            drain_checks(holder, "deferred")
            blocked = False
            try:
                with worker.begin() as contender:
                    contender.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
                    contender.execute(
                        sa.update(observations)
                        .where(
                            observations.c.station_id == batch.station_id,
                            observations.c.parameter == parameter,
                        )
                        .values(
                            **{
                                change: 9.0
                                if change == "value"
                                else "concurrent-change"
                            }
                        )
                    )
            except DBAPIError as exc:
                assert getattr(exc.orig, "sqlstate", None) == "55P03"
                blocked = True
            tx.commit()
            assert blocked, (
                "UNSAFE ACTUAL COMMITTED: direct receipt lost referenced row lock"
            )

    def test_scope_approval_requires_read_committed(
        self,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        from uuid import uuid4

        from sapphire_flow.db.metadata import bafu_unit_write_scopes

        with isolated_committed_db_engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn:
            with (
                conn.begin(),
                pytest.raises(DBAPIError, match="READ COMMITTED") as caught,
            ):
                conn.execute(
                    sa.insert(bafu_unit_write_scopes).values(
                        id=uuid4(),
                        tenant_id=batch.tenant_id,
                        station_id=batch.station_id,
                        network="bafu",
                        gauge_code=batch.key.gauge.value,
                        lindas_kind=batch.key.kind,
                        starts_at=batch.received_at,
                        registry_sha256=batch.registry_sha256,
                        approval_sha256="a" * 64,
                    )
                )
            assert getattr(caught.value.orig, "sqlstate", None) == "PBU01"

    def test_repeatable_read_metadata_and_dhm_binding_unaffected(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        with (
            worker.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(name="Metadata only")
            )
        with worker.begin() as conn:
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(network="dhm")
            )
        with (
            worker.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == batch.station_id)
                .values(code="dhm-renamed")
            )
