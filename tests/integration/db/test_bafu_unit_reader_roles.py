from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import bafu_unit_read_holds, observations
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.observation_units import DischargeSourceUnit
from tests.integration.db.test_bafu_unit_guards import batch as batch
from tests.integration.db.test_bafu_unit_guards import bootstrapped as bootstrapped
from tests.integration.db.test_bafu_unit_guards import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)
from tests.integration.db.test_bafu_unit_guards import protected as protected
from tests.integration.db.test_bafu_unit_guards import role_harness as role_harness
from tests.integration.db.test_bafu_unit_guards import worker as worker
from tests.integration.store.test_bafu_unit_writer import QUOTA, later, normalized

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


def add_hold(engine: sa.Engine, candidate: BafuStationCaptureBatch) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(bafu_unit_read_holds).values(
                id=uuid4(),
                tenant_id=candidate.tenant_id,
                station_id=candidate.station_id,
                starts_at=candidate.received_at,
                reason="unit history uncertain",
                origin="maintenance",
                registry_sha256=candidate.registry_sha256,
                approval_sha256="a" * 64,
            )
        )


class TestUnitReaderRoles:
    def test_unapproved_new_unit_evidence_is_not_canonical(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        candidate = normalized(batch)
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(candidate)
        add_hold(isolated_committed_db_engine, candidate)
        with worker.connect() as conn:
            assert (
                PgObservationStore(conn)
                .qc_context_reader()
                .fetch_observations(
                    candidate.station_id,
                    "discharge",
                    candidate.received_at,
                    candidate.received_at + timedelta(days=1),
                )
                == []
            )

    def test_canonical_held_row_is_qc_only(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        bootstrapped: _RoleBootstrapHarness,
    ) -> None:
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        add_hold(isolated_committed_db_engine, protected)
        with worker.begin() as conn:
            store = PgObservationStore(conn)
            qc_store = store.qc_context_reader()
            start, end = (
                protected.received_at,
                protected.received_at + timedelta(days=1),
            )
            assert (
                store.fetch_observations(protected.station_id, "discharge", start, end)
                == []
            )
            rows = qc_store.fetch_observations(
                protected.station_id, "discharge", start, end
            )
            assert len(rows) == 1
            assert qc_store.update_qc_if_current(rows[0], QcStatus.QC_PASSED, [])
            assert store.fetch_observation_by_id(rows[0].id) is None
        api = sa.create_engine(bootstrapped.role_url("sapphire_api", "api-pw-initial"))
        try:
            with api.connect() as conn:
                assert (
                    PgObservationStore(conn).fetch_observation_by_id(rows[0].id) is None
                )
                with pytest.raises(DBAPIError, match="permission denied"):
                    conn.execute(sa.text("SELECT capture FROM bafu_unit_events"))
        finally:
            api.dispose()

    def test_latest_quarantine_does_not_fall_back_to_old_matching_event(
        self, worker: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        writer = PgBafuUnitWriter(worker.begin, quota=QUOTA)
        first = normalized(batch)
        writer.write(first)
        writer.write(later(batch))
        with worker.connect() as conn:
            store = PgObservationStore(conn).qc_context_reader()
            assert (
                store.fetch_observations(
                    first.station_id,
                    "discharge",
                    first.received_at,
                    first.received_at + timedelta(days=1),
                )
                == []
            )

    def test_zero_semantic_change_rejects_previous_verdict(
        self, worker: sa.Engine, protected: BafuStationCaptureBatch
    ) -> None:
        writer = PgBafuUnitWriter(worker.begin, quota=QUOTA)
        first = normalized(protected, literal="0")
        writer.write(first)
        with worker.connect() as conn:
            store = PgObservationStore(conn).qc_context_reader()
            evaluated = store.fetch_observations(
                first.station_id,
                "discharge",
                first.received_at,
                first.received_at + timedelta(days=1),
            )[0]
        changed = normalized(
            later(first),
            literal="0",
            unit=DischargeSourceUnit.CUBIC_METRES_PER_SECOND,
            policy="new",
        )
        writer.write(changed)
        with worker.begin() as conn:
            assert not PgObservationStore(conn).update_qc_if_current(
                evaluated, QcStatus.QC_PASSED, []
            )
            assert (
                conn.scalar(
                    sa.select(observations.c.qc_status).where(
                        observations.c.id == evaluated.id
                    )
                )
                == "raw"
            )

    def test_current_value_mismatch_is_hidden_without_a_scope(
        self,
        worker: sa.Engine,
        batch: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        candidate = normalized(batch)
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(candidate)
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.update(observations)
                .where(observations.c.station_id == candidate.station_id)
                .values(value=999.0)
            )
        with worker.connect() as conn:
            store = PgObservationStore(conn)
            assert (
                store.fetch_latest_timestamp(candidate.station_id, "discharge") is None
            )
            assert (
                store.qc_context_reader().fetch_latest_timestamp(
                    candidate.station_id, "discharge"
                )
                is None
            )


class TestNumericApiHolds:
    def test_station_and_generic_numeric_routes_hide_held_value(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
        bootstrapped: _RoleBootstrapHarness,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sapphire_flow.api.routes import tables
        from tests.integration.api.test_dashboard_forecasts import (
            _client,
            app_overrides_clear,
        )

        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        add_hold(isolated_committed_db_engine, protected)
        engine = sa.create_engine(
            bootstrapped.role_url("sapphire_api", "api-pw-initial")
        )
        try:
            with engine.connect() as conn:
                monkeypatch.setattr(tables, "_reflected", None)
                client = _client(conn)
                try:
                    response = client.get(
                        f"/api/v1/stations/{protected.station_id}/observations.json",
                        params={
                            "parameter": "discharge",
                            "start": protected.received_at.isoformat(),
                            "end": (
                                protected.received_at + timedelta(days=1)
                            ).isoformat(),
                        },
                    )
                    assert response.status_code == 200
                    assert response.json()["values"] == []
                    for suffix in ("/", "/rows"):
                        response = client.get("/tables/observations" + suffix)
                        assert response.status_code == 200
                        assert str(protected.station_id) not in response.text
                finally:
                    client.close()
                    app_overrides_clear()
        finally:
            engine.dispose()

    def test_predicate_permission_failure_never_falls_back(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "REVOKE SELECT ON bafu_unit_observation_read_state "
                    "FROM sapphire_worker"
                )
            )
        try:
            with (
                worker.connect() as conn,
                pytest.raises(DBAPIError, match="permission denied"),
            ):
                PgObservationStore(conn).fetch_latest_timestamp(
                    protected.station_id, "discharge"
                )
        finally:
            with isolated_committed_db_engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "GRANT SELECT ON bafu_unit_observation_read_state "
                        "TO sapphire_worker"
                    )
                )


class TestConcurrentConditionalQc:
    @pytest.mark.parametrize("change", ["numeric", "unit_zero"])
    def test_value_change_commits_before_blocked_verdict(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        change: str,
    ) -> None:
        if change == "unit_zero":
            protected = normalized(protected, literal="0")
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        changed = (
            normalized(
                later(protected),
                literal="0",
                unit=DischargeSourceUnit.CUBIC_METRES_PER_SECOND,
                policy="new",
            )
            if change == "unit_zero"
            else later(protected, literal="44")
        )
        with worker.connect() as conn:
            evaluated = (
                PgObservationStore(conn)
                .qc_context_reader()
                .fetch_observations(
                    protected.station_id,
                    "discharge",
                    protected.received_at,
                    protected.received_at + timedelta(days=1),
                )[0]
            )
        value_written, allow_commit, qc_started = Event(), Event(), Event()

        def pause(label: str) -> None:
            if label == "after_value":
                value_written.set()
                assert allow_commit.wait(timeout=10)

        def judge() -> bool:
            with worker.begin() as conn:
                qc_started.set()
                return PgObservationStore(conn).update_qc_if_current(
                    evaluated, QcStatus.QC_PASSED, []
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(
                PgBafuUnitWriter(worker.begin, quota=QUOTA, fault_point=pause).write,
                changed,
            )
            try:
                assert value_written.wait(timeout=10)
                judging = pool.submit(judge)
                assert qc_started.wait(timeout=10)
            finally:
                allow_commit.set()
            writing.result(timeout=10)
            assert not judging.result(timeout=10)
        with worker.connect() as conn:
            assert (
                conn.scalar(
                    sa.select(observations.c.qc_status).where(
                        observations.c.id == evaluated.id
                    )
                )
                == "raw"
            )


class TestConditionalQcBoundary:
    @pytest.mark.parametrize("missing", ["state", "context"])
    def test_null_token_cannot_write(
        self,
        worker: sa.Engine,
        protected: BafuStationCaptureBatch,
        missing: str,
    ) -> None:
        PgBafuUnitWriter(worker.begin, quota=QUOTA).write(protected)
        with worker.begin() as conn:
            row = (
                PgObservationStore(conn)
                .qc_context_reader()
                .fetch_observations(
                    protected.station_id,
                    "discharge",
                    protected.received_at,
                    protected.received_at + timedelta(days=1),
                )[0]
            )
            token = row.qc_read_token
            assert token is not None
            assert not conn.scalar(
                sa.text(
                    "SELECT public.observation_qc_compare_and_set"
                    "(:id, :state, :context, 'qc_passed', NULL, NULL)"
                ),
                {
                    "id": row.id,
                    "state": None if missing == "state" else token.state_sha256,
                    "context": None if missing == "context" else token.context_sha256,
                },
            )
            assert (
                conn.scalar(
                    sa.select(observations.c.qc_status).where(
                        observations.c.id == row.id
                    )
                )
                == "raw"
            )

    def test_api_has_no_conditional_write_function_grant(
        self,
        bootstrapped: _RoleBootstrapHarness,
    ) -> None:
        api = sa.create_engine(bootstrapped.role_url("sapphire_api", "api-pw-initial"))
        try:
            with api.connect() as conn:
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege(current_user, "
                        "'public.observation_qc_compare_and_set"
                        "(uuid,text,text,text,jsonb,text)', 'EXECUTE')"
                    )
                )
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege(current_user, "
                        "'public.observation_qc_batch_compare_and_set"
                        "(uuid,text,timestamptz,timestamptz,text,text,jsonb)', "
                        "'EXECUTE')"
                    )
                )
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege(current_user, "
                        "'public.observation_qc_boundaries', 'SELECT')"
                    )
                )
                with pytest.raises(DBAPIError, match="permission denied"):
                    conn.execute(
                        sa.text(
                            "SELECT public.observation_qc_compare_and_set"
                            "(NULL,NULL,NULL,'qc_passed',NULL,NULL)"
                        )
                    )
        finally:
            api.dispose()
