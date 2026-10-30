from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import (
    bafu_unit_hold_releases,
    bafu_unit_read_holds,
    observations,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.enums import QcStatus
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
    role_harness as role_harness,
)
from tests.integration.db.test_bafu_unit_guards import (
    worker as worker,
)

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch
    from sapphire_flow.types.observation import Observation
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def cohort(worker: sa.Engine, batch: BafuStationCaptureBatch) -> list[Observation]:
    with worker.begin() as conn:
        conn.execute(
            sa.insert(observations),
            [
                dict(
                    id=uuid4(),
                    station_id=batch.station_id,
                    parameter="discharge",
                    timestamp=batch.received_at + timedelta(hours=i),
                    value=float(i + 1),
                    source="manual_import",
                    qc_status="raw",
                )
                for i in (0, 2)
            ],
        )
    with worker.connect() as conn:
        return (
            PgObservationStore(conn)
            .qc_context_reader()
            .fetch_observations(
                batch.station_id,
                "discharge",
                batch.received_at,
                batch.received_at + timedelta(days=1),
            )
        )


def mutate(
    conn: sa.Connection,
    kind: str,
    cohort: list[Observation],
    batch: BafuStationCaptureBatch,
    hold_id: object,
) -> None:
    neighbour = cohort[0]
    if kind in ("value", "provenance"):
        conn.execute(
            sa.update(observations)
            .where(observations.c.id == neighbour.id)
            .values(
                **(
                    {"value": 99.0}
                    if kind == "value"
                    else {"rating_curve_correction_version": "revised"}
                )
            )
        )
    elif kind == "delete":
        conn.execute(sa.delete(observations).where(observations.c.id == neighbour.id))
    elif kind == "insert":
        conn.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=batch.station_id,
                parameter="discharge",
                timestamp=batch.received_at + timedelta(hours=1),
                value=4.0,
                source="manual_import",
                qc_status="raw",
            )
        )
    elif kind == "hold":
        conn.execute(
            sa.insert(bafu_unit_read_holds).values(
                id=uuid4(),
                station_id=batch.station_id,
                tenant_id=batch.tenant_id,
                starts_at=batch.received_at + timedelta(hours=1),
                reason="bounded absent boundary",
                origin="maintenance",
                registry_sha256=batch.registry_sha256,
                approval_sha256="a" * 64,
            )
        )
    else:
        conn.execute(
            sa.insert(bafu_unit_hold_releases).values(
                id=uuid4(),
                hold_id=hold_id,
                starts_at=batch.received_at + timedelta(hours=1),
                ends_at=batch.received_at + timedelta(hours=1, minutes=30),
                approval_sha256="b" * 64,
            )
        )


class TestBoundedQcSerialization:
    @pytest.mark.parametrize(
        "kind", ["value", "provenance", "insert", "delete", "hold", "release"]
    )
    def test_committed_mutator_invalidates_whole_batch(
        self,
        worker: sa.Engine,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
        kind: str,
    ) -> None:
        hold_id = uuid4()
        if kind == "release":
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.insert(bafu_unit_read_holds).values(
                        id=hold_id,
                        station_id=batch.station_id,
                        tenant_id=batch.tenant_id,
                        starts_at=batch.received_at + timedelta(hours=1),
                        reason="absent interval",
                        origin="maintenance",
                        approval_sha256="a" * 64,
                        registry_sha256=batch.registry_sha256,
                    )
                )
            with worker.connect() as conn:
                cohort = (
                    PgObservationStore(conn)
                    .qc_context_reader()
                    .fetch_observations(
                        batch.station_id,
                        "discharge",
                        batch.received_at,
                        batch.received_at + timedelta(days=1),
                    )
                )
        written, commit, judging = Event(), Event(), Event()

        def write() -> None:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "SET LOCAL ROLE "
                        + (
                            "sapphire_bafu_maintenance"
                            if kind in ("hold", "release")
                            else "test"
                            if kind == "delete"
                            else "sapphire_worker"
                        )
                    )
                )
                mutate(conn, kind, cohort, batch, hold_id)
                conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
                written.set()
                assert commit.wait(timeout=10)

        def judge() -> bool:
            with worker.begin() as conn:
                judging.set()
                return PgObservationStore(conn).update_qc_batch_if_current(
                    [(o, QcStatus.QC_PASSED, []) for o in cohort],
                    "v1",
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write)
            try:
                assert written.wait(timeout=10)
                checker = pool.submit(judge)
                assert judging.wait(timeout=10)
            finally:
                commit.set()
            writer.result(timeout=10)
            assert not checker.result(timeout=10)
        with worker.begin() as conn:
            assert set(
                conn.scalars(
                    sa.select(observations.c.qc_status).where(
                        observations.c.station_id == batch.station_id
                    )
                )
            ) == {"raw"}
            fresh = (
                PgObservationStore(conn)
                .qc_context_reader()
                .fetch_observations(
                    batch.station_id,
                    "discharge",
                    batch.received_at,
                    batch.received_at + timedelta(days=1),
                )
            )
            assert PgObservationStore(conn).update_qc_batch_if_current(
                [(o, QcStatus.QC_PASSED, []) for o in fresh],
                "v1",
            )

    @pytest.mark.parametrize(
        "kind", ["value", "provenance", "insert", "delete", "hold", "release"]
    )
    def test_actual_mutator_cannot_commit_while_qc_transaction_owns_context(
        self,
        worker: sa.Engine,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
        kind: str,
    ) -> None:
        hold_id = uuid4()
        # Existing out-of-window hold: the new partial release creates an
        # in-window end boundary. It must join the same protocol.
        if kind == "release":
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.insert(bafu_unit_read_holds).values(
                        id=hold_id,
                        station_id=batch.station_id,
                        tenant_id=batch.tenant_id,
                        starts_at=batch.received_at - timedelta(days=1),
                        reason="absent interval",
                        origin="maintenance",
                        approval_sha256="a" * 64,
                        registry_sha256=batch.registry_sha256,
                    )
                )
            with worker.connect() as conn:
                cohort = (
                    PgObservationStore(conn)
                    .qc_context_reader()
                    .fetch_observations(
                        batch.station_id,
                        "discharge",
                        batch.received_at,
                        batch.received_at + timedelta(days=1),
                    )
                )
        with worker.begin() as qc_conn:
            # Only target is row-locked. Neighbour value/delete and absent inserts
            # have no target-row lock conflict, so this proves shared context locks.
            assert PgObservationStore(qc_conn).update_qc_if_current(
                cohort[-1],
                QcStatus.QC_PASSED,
                [],
            )
            qc_conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            qc_conn.execute(sa.text("RESET ALL"))
            with (
                isolated_committed_db_engine.connect() as conn,
                pytest.raises(DBAPIError, match="lock timeout"),
                conn.begin(),
            ):
                conn.execute(
                    sa.text(
                        "SET LOCAL ROLE "
                        + (
                            "sapphire_bafu_maintenance"
                            if kind in ("hold", "release")
                            else "test"
                            if kind == "delete"
                            else "sapphire_worker"
                        )
                    )
                )
                mutate(conn, kind, cohort, batch, hold_id)
        with worker.connect() as conn:
            assert (
                conn.scalar(
                    sa.select(observations.c.qc_status).where(
                        observations.c.id == cohort[-1].id
                    )
                )
                == "qc_passed"
            )


class TestOperatorNeighbourDelete:
    def test_delivery_operator_delete_is_serialized_without_private_reads(
        self,
        bootstrapped: _RoleBootstrapHarness,
    ) -> None:
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.store.tenant_store import PgTenantStore
        from sapphire_flow.types.dhm_delivery import DELIVERY_ID
        from sapphire_flow.types.ids import TenantId
        from tests.conftest import make_station_config

        harness = bootstrapped
        result = harness.run_bootstrap(
            "api-pw-initial",
            "worker-pw-initial",
            "backup-pw-initial",
            operator_password="qc-operator-test",
        )
        assert result.returncode == 0
        with harness.owner_engine.begin() as conn:
            tenant = PgTenantStore(conn).ensure_tenant(
                tenant_id=TenantId(uuid4()),
                code="chwrr",
                name="QC test tenant",
            )
            station = make_station_config(
                code="QC-DELETE", network="dhm", tenant_id=tenant.id
            )
            PgStationStore(conn).store_station(station)
            from tests.integration.store.test_bafu_unit_read_filter import NOW

            conn.execute(
                sa.insert(observations),
                [
                    dict(
                        id=uuid4(),
                        station_id=station.id,
                        parameter="discharge",
                        timestamp=NOW + timedelta(hours=i),
                        source="manual_import",
                        delivery_id=DELIVERY_ID,
                        value=1.0,
                        qc_status="raw",
                    )
                    for i in (0, 1)
                ],
            )
        worker_engine = sa.create_engine(
            harness.role_url("sapphire_worker", "worker-pw-initial")
        )
        operator_engine = sa.create_engine(
            harness.role_url("sapphire_operator", "qc-operator-test")
        )
        try:
            with worker_engine.begin() as conn:
                store = PgObservationStore(conn).qc_context_reader()
                rows = store.fetch_observations(
                    station.id, "discharge", NOW, NOW + timedelta(days=1)
                )
                assert store.update_qc_if_current(rows[-1], QcStatus.QC_PASSED, [])
                with (
                    operator_engine.connect() as deleting,
                    pytest.raises(DBAPIError, match="lock timeout"),
                ):
                    deleting.execute(
                        sa.delete(observations).where(observations.c.id == rows[0].id)
                    )
            with operator_engine.begin() as deleting:
                deleting.execute(
                    sa.delete(observations).where(observations.c.id == rows[0].id)
                )
            with worker_engine.begin() as conn:
                assert not PgObservationStore(conn).update_qc_if_current(
                    rows[-1], QcStatus.QC_PASSED, []
                )
        finally:
            operator_engine.dispose()
            worker_engine.dispose()


class TestQcTransactionRollback:
    def test_mutator_rollback_preserves_dependency_and_cas_rollback_restores_verdict(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
    ) -> None:
        with worker.connect() as conn:
            mutate(conn, "value", cohort, batch, uuid4())
            conn.rollback()
        with worker.connect() as conn:
            store = PgObservationStore(conn)
            assert store.update_qc_if_current(cohort[-1], QcStatus.QC_PASSED, [])
            conn.rollback()
            assert (
                conn.scalar(
                    sa.select(observations.c.qc_status).where(
                        observations.c.id == cohort[-1].id
                    )
                )
                == "raw"
            )
        with worker.begin() as conn:
            assert PgObservationStore(conn).update_qc_if_current(
                cohort[-1],
                QcStatus.QC_PASSED,
                [],
            )

    def test_before_rewriter_final_parameter_joins_context_lock(
        self,
        worker: sa.Engine,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
    ) -> None:
        # Disposable-schema instrumentation only; final-row trigger must not use
        # the water_level originally submitted by the supported worker role.
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text("""
                CREATE FUNCTION public.qc_test_rewrite() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    NEW.parameter := 'discharge'; RETURN NEW;
                END $$;
                CREATE TRIGGER zzzz_qc_test_rewrite BEFORE INSERT ON observations
                FOR EACH ROW EXECUTE FUNCTION public.qc_test_rewrite();
            """)
            )
        try:
            with worker.begin() as qc_conn:
                assert PgObservationStore(qc_conn).update_qc_if_current(
                    cohort[-1],
                    QcStatus.QC_PASSED,
                    [],
                )
                qc_conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
                with (
                    worker.connect() as conn,
                    pytest.raises(DBAPIError, match="lock timeout"),
                ):
                    conn.execute(
                        sa.insert(observations).values(
                            id=uuid4(),
                            station_id=batch.station_id,
                            parameter="water_level",
                            timestamp=batch.received_at + timedelta(hours=1),
                            value=5.0,
                            source="manual_import",
                            qc_status="raw",
                        )
                    )
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text("DROP TRIGGER zzzz_qc_test_rewrite ON observations")
                )
                conn.execute(sa.text("DROP FUNCTION public.qc_test_rewrite()"))


class TestQcReviewCorrections:
    @pytest.mark.parametrize("batch_read", [False, True])
    def test_context_rejects_status_filter(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
        batch_read: bool,
    ) -> None:
        with worker.connect() as conn:
            store = PgObservationStore(conn).qc_context_reader()
            with pytest.raises(ValueError, match="full cohort"):
                if batch_read:
                    store.fetch_observations_batch(
                        [batch.station_id],
                        "discharge",
                        batch.received_at,
                        batch.received_at + timedelta(days=1),
                        qc_status=QcStatus.RAW,
                    )
                else:
                    store.fetch_observations(
                        batch.station_id,
                        "discharge",
                        batch.received_at,
                        batch.received_at + timedelta(days=1),
                        qc_status=QcStatus.RAW,
                    )

    def test_old_pending_verdict_cannot_overwrite_new_verdict(
        self,
        worker: sa.Engine,
        cohort: list[Observation],
    ) -> None:
        with worker.begin() as conn:
            assert PgObservationStore(conn).update_qc_batch_if_current(
                [(o, QcStatus.QC_PASSED, []) for o in cohort], "new-rule"
            )
        with worker.begin() as conn:
            assert not PgObservationStore(conn).update_qc_batch_if_current(
                [(o, QcStatus.QC_FAILED, []) for o in cohort], "old-rule"
            )
        with worker.connect() as conn:
            assert set(
                conn.scalars(
                    sa.select(observations.c.qc_rule_version).where(
                        observations.c.id.in_([o.id for o in cohort])
                    )
                )
            ) == {"new-rule"}

    @pytest.mark.parametrize(
        "effect", ["RETURN NULL", "NEW.qc_status := 'qc_failed'; RETURN NEW"]
    )
    def test_qc_trigger_interference_rolls_back_whole_batch(
        self,
        worker: sa.Engine,
        isolated_committed_db_engine: sa.Engine,
        cohort: list[Observation],
        effect: str,
    ) -> None:
        target = cohort[1].id
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text(f"""
                CREATE FUNCTION qc_test_interference() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.id = '{target}'::uuid THEN {effect}; END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER qc_test_interference BEFORE UPDATE ON observations
                    FOR EACH ROW EXECUTE FUNCTION qc_test_interference();
            """)
            )
        try:
            with (
                pytest.raises(ValueError, match="QC batch postcondition"),
                worker.begin() as conn,
            ):
                PgObservationStore(conn).update_qc_batch_if_current(
                    [(o, QcStatus.QC_PASSED, []) for o in cohort], "v1"
                )
            with worker.connect() as conn:
                assert set(
                    conn.scalars(
                        sa.select(observations.c.qc_status).where(
                            observations.c.id.in_([o.id for o in cohort])
                        )
                    )
                ) == {"raw"}
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DROP TRIGGER qc_test_interference ON observations; "
                        "DROP FUNCTION qc_test_interference()"
                    )
                )

    @pytest.mark.parametrize(
        "invalid",
        [
            "context_null",
            "state_null",
            "qc_state_missing",
            "id_malformed",
            "not_array",
            "duplicate",
            "empty",
        ],
    )
    def test_live_batch_invalid_input_is_not_staleness(
        self,
        worker: sa.Engine,
        cohort: list[Observation],
        invalid: str,
    ) -> None:
        from sqlalchemy.dialects.postgresql import JSONB

        token = cohort[0].qc_read_token
        assert token is not None and token.window is not None
        window = token.window
        verdict: dict[str, object] = {
            "id": str(cohort[0].id),
            "state_sha256": token.state_sha256,
            "qc_state_sha256": token.qc_state_sha256,
            "status": "qc_passed",
            "flags": None,
            "version": "v1",
        }
        payload: object = [verdict]
        if invalid == "state_null":
            verdict["state_sha256"] = None
        elif invalid == "qc_state_missing":
            del verdict["qc_state_sha256"]
        elif invalid == "id_malformed":
            verdict["id"] = "not-a-uuid"
        elif invalid == "not_array":
            payload = verdict
        elif invalid == "duplicate":
            payload = [verdict, verdict]
        elif invalid == "empty":
            payload = []
        with (
            pytest.raises(DBAPIError, match="invalid QC batch") as caught,
            worker.begin() as conn,
        ):
            conn.execute(
                sa.select(
                    sa.func.public.observation_qc_batch_compare_and_set(
                        window.station_id,
                        window.parameter,
                        window.start,
                        window.end,
                        None,
                        None if invalid == "context_null" else token.context_sha256,
                        sa.bindparam("verdicts", payload, type_=JSONB),
                    )
                )
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "22023"
        with worker.connect() as conn:
            assert set(
                conn.scalars(
                    sa.select(observations.c.qc_status).where(
                        observations.c.id.in_([o.id for o in cohort])
                    )
                )
            ) == {"raw"}

    @pytest.mark.parametrize("missing", ["station", "parameter"])
    def test_lock_rejects_null_input(
        self, worker: sa.Engine, batch: BafuStationCaptureBatch, missing: str
    ) -> None:
        with (
            pytest.raises(DBAPIError, match="QC lock requires") as caught,
            worker.begin() as conn,
        ):
            conn.execute(
                sa.select(
                    sa.func.public.observation_qc_lock(
                        None if missing == "station" else batch.station_id,
                        None if missing == "parameter" else "discharge",
                    )
                )
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "22023"

    def test_target_qc_change_preserves_context_and_other_target_token(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
    ) -> None:
        with worker.begin() as conn:
            store = PgObservationStore(conn).qc_context_reader()
            assert store.update_qc_if_current(cohort[0], QcStatus.QC_PASSED, [], "v2")
        with worker.begin() as conn:
            store = PgObservationStore(conn).qc_context_reader()
            fresh = store.fetch_observations(
                batch.station_id,
                "discharge",
                batch.received_at,
                batch.received_at + timedelta(days=1),
            )
            assert (
                fresh[0].qc_read_token is not None
                and cohort[0].qc_read_token is not None
            )
            assert (
                fresh[0].qc_read_token.context_sha256
                == cohort[0].qc_read_token.context_sha256
            )
            assert (
                fresh[0].qc_read_token.state_sha256
                == cohort[0].qc_read_token.state_sha256
            )
            assert store.update_qc_if_current(cohort[1], QcStatus.QC_PASSED, [], "v2")
            assert not store.update_qc_if_current(
                cohort[0], QcStatus.QC_PASSED, [], "v2"
            )

    def test_null_value_has_scientific_digest_and_can_receive_missing_verdict(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        cohort: list[Observation],
    ) -> None:
        with worker.begin() as conn:
            conn.execute(
                sa.update(observations)
                .where(observations.c.id == cohort[0].id)
                .values(value=None, qc_status="missing")
            )
        with worker.begin() as conn:
            store = PgObservationStore(conn).qc_context_reader()
            fresh = store.fetch_observations(
                batch.station_id,
                "discharge",
                batch.received_at,
                batch.received_at + timedelta(days=1),
            )
            assert fresh[0].value is None
            assert fresh[0].qc_read_token is not None
            assert len(fresh[0].qc_read_token.state_sha256) == 64
            assert store.update_qc_if_current(fresh[0], QcStatus.MISSING, [], "v1")
