# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from sapphire_flow.db.metadata import (
    audit_log,
    forecast_evidence,
    forecast_preservation_attestations,
    forecast_publication_decisions,
    forecast_publication_events,
    forecast_publication_selections,
    human_station_grants,
    pipeline_health,
    protected_backup_forecast_proofs,
    protected_backup_health,
    users,
)
from sapphire_flow.store.forecast_publication_store import (
    PgForecastPublicationStore,
    PublicationBackupOverdueError,
    PublicationConflictError,
    PublicationForbiddenError,
    PublicationUnavailableError,
)
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.forecast_values_integrity import forecast_values_integrity
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.forecast_evidence import EvidenceStatus, ForecastEvidence
from sapphire_flow.types.forecast_publication import (
    PreservationAtPublish,
    PublicationDecision,
    PublishRequest,
    WithdrawalReasonCode,
    WithdrawRequest,
)
from sapphire_flow.types.human_auth import HumanPrincipal
from sapphire_flow.types.ids import ForecastId, PublicationDecisionId, StationId, UserId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config
from tests.integration.store.test_forecast_evidence_store import _evidence
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


_NOW = ensure_utc(datetime(2026, 9, 27, tzinfo=UTC))


@contextmanager
def _transaction(connection: sa.Connection) -> Iterator[sa.Connection]:
    with connection.begin_nested():
        yield connection


def _seed(
    connection: sa.Connection, *, with_evidence: bool = True, qc_failed: bool = False
) -> tuple[PgForecastPublicationStore, HumanPrincipal, ForecastId, StationId]:
    station_id = _seed_station(connection)
    model_id = _seed_model(connection)
    artifact_id = _seed_artifact(connection, station_id, model_id)
    forecast = _make_forecast(station_id, model_id, artifact_id)
    if qc_failed:
        forecast = replace(forecast, qc_status=QcStatus.QC_FAILED)
    if with_evidence:
        snapshot = b"as-used-observations"
        artifact = b"model-weights"
        evidence = ForecastEvidence(
            status=EvidenceStatus.COMPLETE,
            manifest_json=json.dumps({"runtime_image_digest": "sha256:" + "a" * 64}),
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact).hexdigest(),
            thresholds=(),
        )
        forecast = replace(forecast, evidence=evidence)
    PgForecastStore(
        connection, transaction_factory=lambda: _transaction(connection)
    ).store_forecast(forecast)
    user_id = UserId(uuid4())
    connection.execute(
        sa.insert(users).values(
            id=user_id, tenant_id=DEFAULT_TENANT_ID, display_name="Duty hydrologist"
        )
    )
    for permission in ("review", "publish"):
        connection.execute(
            sa.insert(human_station_grants).values(
                user_id=user_id,
                tenant_id=DEFAULT_TENANT_ID,
                station_id=station_id,
                permission=permission,
            )
        )
    connection.execute(
        sa.insert(protected_backup_health).values(
            id=1,
            status="verified",
            backup_id=uuid4(),
            restored_at=_NOW - timedelta(hours=1),
            checked_at=_NOW - timedelta(minutes=5),
            target_separate=True,
            retention_ready=True,
            manifest_sha256="b" * 64,
        )
    )
    principal = HumanPrincipal(
        user_id=user_id, tenant_id=DEFAULT_TENANT_ID, grants=frozenset()
    )
    store = PgForecastPublicationStore(
        connection, transaction_factory=lambda: _transaction(connection)
    )
    return store, principal, forecast.id, station_id


def _publish(
    store: PgForecastPublicationStore,
    principal: HumanPrincipal,
    forecast_id: ForecastId,
    *,
    expected_selection_version: int | None = None,
    idempotency_key: str = "first",
    expected_forecast_version: int = 1,
    now: UtcDatetime = _NOW,
) -> PublicationDecision:
    return store.publish(
        PublishRequest(
            forecast_id=forecast_id,
            expected_forecast_version=expected_forecast_version,
            expected_selection_version=expected_selection_version,
            idempotency_key=idempotency_key,
        ),
        principal,
        decision_id=PublicationDecisionId(uuid4()),
        now=now,
    )


def _add_candidate(
    connection: sa.Connection,
    station_id: StationId,
    *,
    issued_at: UtcDatetime = _ISSUED_A,
    qc_failed: bool = False,
) -> ForecastId:
    model_id = _seed_model(connection, model_id="linreg_v2")
    artifact_id = _seed_artifact(connection, station_id, model_id)
    evidence = replace(
        _evidence(),
        manifest_json=json.dumps({"runtime_image_digest": "sha256:" + "a" * 64}),
    )
    forecast = replace(
        _make_forecast(station_id, model_id, artifact_id, issued_at=issued_at),
        evidence=evidence,
        qc_status=QcStatus.QC_FAILED if qc_failed else QcStatus.RAW,
    )
    PgForecastStore(
        connection, transaction_factory=lambda: _transaction(connection)
    ).store_forecast(forecast)
    return forecast.id


def _insert_proof(
    connection: sa.Connection,
    forecast_id: ForecastId,
    *,
    verified_at: UtcDatetime,
    publication_decision_id: PublicationDecisionId | None = None,
) -> None:
    evidence = (
        connection.execute(
            sa.select(forecast_evidence).where(
                forecast_evidence.c.forecast_id == forecast_id
            )
        )
        .mappings()
        .one()
    )
    _, value_hash = forecast_values_integrity(connection, forecast_id)
    attestation_id = uuid4()
    backup_id = uuid4()
    connection.execute(
        sa.insert(forecast_preservation_attestations).values(
            id=attestation_id,
            forecast_id=forecast_id,
            backup_id=backup_id,
            capture_manifest_sha256=hashlib.sha256(
                evidence["manifest_json"].encode("utf-8")
            ).hexdigest(),
            snapshot_sha256=evidence["snapshot_sha256"],
            forecast_values_sha256=value_hash,
            artifact_sha256=evidence["artifact_sha256"],
            runtime_image_digest=json.loads(evidence["manifest_json"])[
                "runtime_image_digest"
            ],
            backup_manifest_sha256="a" * 64,
            database_dump_sha256="b" * 64,
            image_archive_sha256="c" * 64,
            restored_at=verified_at,
        )
    )
    connection.execute(
        sa.insert(protected_backup_forecast_proofs).values(
            id=uuid4(),
            forecast_id=forecast_id,
            attestation_id=attestation_id,
            backup_id=backup_id,
            verified_at=verified_at,
            publication_decision_id=publication_decision_id,
        )
    )


class TestPgForecastPublicationStore:
    def test_legacy_evidence_requires_fresh_attestation_and_then_publishes(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        evidence = (
            db_connection.execute(
                sa.select(forecast_evidence).where(
                    forecast_evidence.c.forecast_id == forecast_id
                )
            )
            .mappings()
            .one()
        )
        manifest = json.loads(evidence["manifest_json"])
        manifest.pop("forecast_values_count")
        manifest.pop("forecast_values_sha256")
        db_connection.execute(
            sa.text("ALTER TABLE forecast_evidence DISABLE TRIGGER USER")
        )
        db_connection.execute(
            sa.update(forecast_evidence)
            .where(forecast_evidence.c.forecast_id == forecast_id)
            .values(manifest_json=json.dumps(manifest, sort_keys=True))
        )
        db_connection.execute(
            sa.text("ALTER TABLE forecast_evidence ENABLE TRIGGER USER")
        )
        with pytest.raises(PublicationUnavailableError, match="legacy forecast"):
            _publish(store, principal, forecast_id)
        _insert_proof(
            db_connection,
            forecast_id,
            verified_at=ensure_utc(_NOW - timedelta(hours=1)),
        )
        decision = _publish(store, principal, forecast_id)
        assert decision.preservation_at_publish is PreservationAtPublish.VERIFIED

    @pytest.mark.parametrize("age_hours", [1, 40, -1])
    def test_proof_is_verified_only_inside_freshness_window(
        self, db_connection: sa.Connection, age_hours: int
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        _insert_proof(
            db_connection,
            forecast_id,
            verified_at=ensure_utc(_NOW - timedelta(hours=age_hours)),
        )
        decision = _publish(store, principal, forecast_id)
        expected = (
            PreservationAtPublish.VERIFIED
            if age_hours == 1
            else PreservationAtPublish.BACKUP_PENDING
        )
        assert decision.preservation_at_publish is expected

    def test_fresh_assessment_of_existing_attestation_recovers_verification(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        _insert_proof(
            db_connection,
            forecast_id,
            verified_at=ensure_utc(_NOW - timedelta(hours=40)),
        )
        first_proof = (
            db_connection.execute(
                sa.select(protected_backup_forecast_proofs).where(
                    protected_backup_forecast_proofs.c.forecast_id == forecast_id
                )
            )
            .mappings()
            .one()
        )
        db_connection.execute(
            sa.insert(protected_backup_forecast_proofs).values(
                id=uuid4(),
                forecast_id=forecast_id,
                attestation_id=first_proof["attestation_id"],
                backup_id=first_proof["backup_id"],
                verified_at=ensure_utc(_NOW - timedelta(minutes=5)),
            )
        )
        assert (
            _publish(store, principal, forecast_id).preservation_at_publish
            is PreservationAtPublish.VERIFIED
        )

    def test_qc_failed_replacement_leaves_current_decision_intact(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_a, station_id = _seed(db_connection)
        forecast_b = _add_candidate(db_connection, station_id, qc_failed=True)
        first = _publish(store, principal, forecast_a)
        with pytest.raises(PublicationConflictError, match="QC-failed"):
            _publish(
                store,
                principal,
                forecast_b,
                expected_selection_version=1,
                idempotency_key="qc-failed",
            )
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id == forecast_a
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_publication_events)
            )
            == 1
        )

    def test_backup_outage_blocks_replacement_but_allows_reasoned_withdrawal(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_a, station_id = _seed(db_connection)
        forecast_b = _add_candidate(db_connection, station_id)
        first = _publish(store, principal, forecast_a)
        db_connection.execute(
            sa.update(protected_backup_health).values(status="invalid")
        )
        with pytest.raises(PublicationUnavailableError, match="unhealthy"):
            _publish(
                store,
                principal,
                forecast_b,
                expected_selection_version=1,
                idempotency_key="outage",
            )
        withdrawn = store.withdraw(
            WithdrawRequest(
                forecast_id=forecast_a,
                expected_selection_version=1,
                reason_code=WithdrawalReasonCode.DATA_ERROR,
                reason_text="Known source error",
                idempotency_key="withdraw-outage",
            ),
            principal,
            decision_id=PublicationDecisionId(uuid4()),
            now=_NOW,
        )
        assert withdrawn.selection_version == 2
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id is None

    def test_linked_warning_blocks_replacement(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_a, station_id = _seed(db_connection)
        forecast_b = _add_candidate(db_connection, station_id)
        first = _publish(store, principal, forecast_a)
        db_connection.execute(
            sa.update(forecast_publication_selections)
            .where(forecast_publication_selections.c.selected_forecast_id == forecast_a)
            .values(linked_warning_publication_id=uuid4())
        )
        with pytest.raises(PublicationConflictError, match="linked warning"):
            _publish(
                store,
                principal,
                forecast_b,
                expected_selection_version=1,
                idempotency_key="linked",
            )
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id == forecast_a

    def test_overdue_pending_proof_blocks_replacement_and_alerts_operations(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_a, station_id = _seed(db_connection)
        forecast_b = _add_candidate(db_connection, station_id)
        first = _publish(store, principal, forecast_a)
        later = _NOW + timedelta(hours=37)
        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=later - timedelta(minutes=5),
                restored_at=later - timedelta(hours=1),
            )
        )
        try:
            with pytest.raises(PublicationBackupOverdueError):
                _publish(
                    store,
                    principal,
                    forecast_b,
                    expected_selection_version=1,
                    idempotency_key="second",
                    now=later,
                )
            selection = store.fetch_selection(first.key)
            assert selection is not None
            assert selection.selected_forecast_id == forecast_a
            assert (
                db_connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(pipeline_health)
                    .where(pipeline_health.c.check_type == "publication_proof_overdue")
                )
                == 1
            )
        finally:
            with db_connection.engine.begin() as cleanup:
                cleanup.execute(
                    sa.delete(pipeline_health).where(
                        pipeline_health.c.check_type == "publication_proof_overdue"
                    )
                )

    def test_old_proof_satisfies_earlier_decision_after_backup_window(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, first_id, station_id = _seed(db_connection)
        second_id = _add_candidate(db_connection, station_id)
        first = _publish(store, principal, first_id)
        _insert_proof(
            db_connection,
            first_id,
            verified_at=ensure_utc(_NOW + timedelta(hours=1)),
            publication_decision_id=first.id,
        )
        later = ensure_utc(_NOW + timedelta(hours=50))
        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=later - timedelta(minutes=5),
                restored_at=later - timedelta(hours=1),
            )
        )
        decision = _publish(
            store,
            principal,
            second_id,
            expected_selection_version=1,
            idempotency_key="after-old-proof",
            now=later,
        )
        assert decision.forecast_id == second_id

    def test_reselected_forecast_needs_its_own_post_dump_decision_proof(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, first_id, station_id = _seed(db_connection)
        second_id = _add_candidate(db_connection, station_id)
        first = _publish(store, principal, first_id)
        second = _publish(
            store,
            principal,
            second_id,
            expected_selection_version=1,
            idempotency_key="second",
        )
        for forecast_id, decision_id in (
            (first_id, first.id),
            (second_id, second.id),
        ):
            _insert_proof(
                db_connection,
                forecast_id,
                verified_at=ensure_utc(_NOW + timedelta(hours=1)),
                publication_decision_id=decision_id,
            )
        republished_at = ensure_utc(_NOW + timedelta(hours=40))
        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=republished_at - timedelta(minutes=5),
                restored_at=republished_at - timedelta(hours=1),
            )
        )
        republished = _publish(
            store,
            principal,
            first_id,
            expected_selection_version=2,
            idempotency_key="first-again",
            now=republished_at,
        )
        assert (
            republished.preservation_at_publish is PreservationAtPublish.BACKUP_PENDING
        )
        _insert_proof(
            db_connection,
            first_id,
            verified_at=ensure_utc(_NOW + timedelta(hours=41)),
            publication_decision_id=first.id,
        )
        overdue_at = ensure_utc(_NOW + timedelta(hours=80))
        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=overdue_at - timedelta(minutes=5),
                restored_at=overdue_at - timedelta(hours=1),
            )
        )
        existing_health_ids = set(
            db_connection.scalars(
                sa.select(pipeline_health.c.id).where(
                    pipeline_health.c.check_type == "publication_proof_overdue"
                )
            )
        )
        created_health_ids: set[int] = set()
        try:
            with pytest.raises(PublicationBackupOverdueError):
                _publish(
                    store,
                    principal,
                    second_id,
                    expected_selection_version=3,
                    idempotency_key="after-cutoff",
                    now=overdue_at,
                )
        finally:
            with db_connection.engine.begin() as cleanup:
                created_health_ids = (
                    set(
                        cleanup.scalars(
                            sa.select(pipeline_health.c.id).where(
                                pipeline_health.c.check_type
                                == "publication_proof_overdue",
                                pipeline_health.c.checked_at == overdue_at,
                                pipeline_health.c.subject
                                == "forecast_publication_proofs",
                            )
                        )
                    )
                    - existing_health_ids
                )
                cleanup.execute(
                    sa.delete(pipeline_health).where(
                        pipeline_health.c.id.in_(created_health_ids)
                    )
                )
        assert len(created_health_ids) == 1

    def test_replacement_keeps_history_and_historical_withdrawal(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_a, station_id = _seed(db_connection)
        forecast_b = _add_candidate(db_connection, station_id)
        first = _publish(store, principal, forecast_a)
        second = _publish(
            store,
            principal,
            forecast_b,
            expected_selection_version=1,
            idempotency_key="second",
        )
        assert second.replaced_forecast_id == forecast_a
        assert second.replaced_decision_id == first.id
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id == forecast_b
        assert store.fetch_decisions(forecast_a)[0].id == first.id
        assert _publish(store, principal, forecast_a).id == first.id

        third = _publish(
            store,
            principal,
            forecast_a,
            expected_selection_version=2,
            idempotency_key="third",
        )
        assert third.id != first.id
        assert third.replaced_forecast_id == forecast_b
        assert len(store.fetch_decisions(forecast_a)) == 2
        withdrawn_b = store.withdraw(
            WithdrawRequest(
                forecast_id=forecast_b,
                expected_selection_version=3,
                reason_code=WithdrawalReasonCode.INCORRECT_FORECAST,
                reason_text="Wrong forecast",
                idempotency_key="withdraw-b",
            ),
            principal,
            decision_id=PublicationDecisionId(uuid4()),
            now=_NOW,
        )
        assert withdrawn_b.selection_version == 3
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id == forecast_a
        with pytest.raises(PublicationConflictError, match="withdrawn forecast"):
            _publish(
                store,
                principal,
                forecast_b,
                expected_selection_version=3,
                idempotency_key="retry-b",
            )
        assert db_connection.execute(
            sa.select(forecast_publication_events.c.sequence).order_by(
                forecast_publication_events.c.sequence
            )
        ).scalars().all() == [1, 2, 3, 4]

    def test_publish_replay_and_withdraw_are_attributable_and_atomic(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        audit_count_before = db_connection.scalar(
            sa.select(sa.func.count()).select_from(audit_log)
        )
        first = _publish(store, principal, forecast_id)
        assert first.preservation_at_publish is PreservationAtPublish.BACKUP_PENDING
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id == forecast_id
        assert _publish(store, principal, forecast_id).id == first.id
        audit_count = db_connection.scalar(
            sa.select(sa.func.count()).select_from(audit_log)
        )
        assert audit_count == audit_count_before + 1
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_publication_events)
            )
            == 1
        )
        withdrawn = store.withdraw(
            WithdrawRequest(
                forecast_id=forecast_id,
                expected_selection_version=1,
                reason_code=WithdrawalReasonCode.DATA_ERROR,
                reason_text="Incorrect input",
                idempotency_key="withdraw-first",
            ),
            principal,
            decision_id=PublicationDecisionId(uuid4()),
            now=_NOW,
        )
        assert withdrawn.selection_version == 2
        selection = store.fetch_selection(first.key)
        assert selection is not None
        assert selection.selected_forecast_id is None
        assert [row.action.value for row in store.fetch_decisions(forecast_id)] == [
            "publish",
            "withdraw",
        ]
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_publication_events)
            )
            == 2
        )
        with pytest.raises(PublicationConflictError, match="selection version"):
            _publish(store, principal, forecast_id, idempotency_key="again")

    def test_missing_evidence_and_unhealthy_backup_fail_closed(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection, with_evidence=False)
        with pytest.raises(PublicationUnavailableError, match="required inputs"):
            _publish(store, principal, forecast_id)
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_publication_decisions)
            )
            == 0
        )
        db_connection.execute(sa.update(protected_backup_health).values(status="stale"))
        with pytest.raises(PublicationUnavailableError, match="missing or unhealthy"):
            _publish(store, principal, forecast_id)

    def test_grant_revocation_blocks_idempotent_replay(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, station_id = _seed(db_connection)
        _publish(store, principal, forecast_id)
        db_connection.execute(
            sa.delete(human_station_grants).where(
                human_station_grants.c.user_id == principal.user_id,
                human_station_grants.c.station_id == station_id,
                human_station_grants.c.permission == "publish",
            )
        )
        with pytest.raises(PublicationForbiddenError, match="station publication"):
            _publish(store, principal, forecast_id)

    def test_stale_forecast_and_qc_failed_do_not_decide(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection, qc_failed=True)
        with pytest.raises(PublicationConflictError, match="forecast version"):
            _publish(store, principal, forecast_id, expected_forecast_version=2)
        with pytest.raises(PublicationConflictError, match="QC-failed"):
            _publish(store, principal, forecast_id)
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_publication_decisions)
            )
            == 0
        )

    def test_linked_warning_blocks_withdrawal(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        first = _publish(store, principal, forecast_id)
        db_connection.execute(
            sa.update(forecast_publication_selections)
            .where(
                forecast_publication_selections.c.selected_forecast_id == forecast_id
            )
            .values(linked_warning_publication_id=uuid4())
        )
        with pytest.raises(PublicationConflictError, match="linked warning"):
            store.withdraw(
                WithdrawRequest(
                    forecast_id=forecast_id,
                    expected_selection_version=first.selection_version,
                    reason_code=WithdrawalReasonCode.OTHER,
                    reason_text="Correction",
                    idempotency_key="withdraw",
                ),
                principal,
                decision_id=PublicationDecisionId(uuid4()),
                now=_NOW,
            )

    def test_audit_failure_rolls_back_selection_decision_and_event(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        db_connection.execute(
            sa.text("""
            CREATE FUNCTION reject_test_publication_audit() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'test audit failure';
            END;
            $$ LANGUAGE plpgsql;
        """)
        )
        db_connection.execute(
            sa.text("""
            CREATE TRIGGER trg_reject_test_publication_audit
            BEFORE INSERT ON audit_log
            FOR EACH ROW EXECUTE FUNCTION reject_test_publication_audit();
        """)
        )
        with pytest.raises(DBAPIError, match="test audit failure"):
            _publish(store, principal, forecast_id)
        for table in (
            forecast_publication_selections,
            forecast_publication_decisions,
            forecast_publication_events,
        ):
            assert (
                db_connection.scalar(sa.select(sa.func.count()).select_from(table)) == 0
            )

    def test_event_failure_rolls_back_selection_decision_and_audit(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        audit_count_before = db_connection.scalar(
            sa.select(sa.func.count()).select_from(audit_log)
        )
        db_connection.execute(
            sa.text("""
            CREATE FUNCTION reject_test_publication_event() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'test event failure';
            END;
            $$ LANGUAGE plpgsql;
        """)
        )
        db_connection.execute(
            sa.text("""
            CREATE TRIGGER trg_reject_test_publication_event
            BEFORE INSERT ON forecast_publication_events
            FOR EACH ROW EXECUTE FUNCTION reject_test_publication_event();
        """)
        )
        with pytest.raises(DBAPIError, match="test event failure"):
            _publish(store, principal, forecast_id)
        for table in (
            forecast_publication_selections,
            forecast_publication_decisions,
            forecast_publication_events,
        ):
            assert (
                db_connection.scalar(sa.select(sa.func.count()).select_from(table)) == 0
            )
        assert (
            db_connection.scalar(sa.select(sa.func.count()).select_from(audit_log))
            == audit_count_before
        )

    def test_selection_foreign_key_rejects_mismatched_station(
        self, db_connection: sa.Connection
    ) -> None:
        _, _, forecast_id, _ = _seed(db_connection)
        other_station_id = StationId(uuid4())
        PgStationStore(db_connection).store_station(
            make_station_config(
                station_id=other_station_id,
                code=f"OTHER-{other_station_id.hex[:8]}",
            )
        )
        with pytest.raises(IntegrityError), db_connection.begin_nested():
            db_connection.execute(
                sa.insert(forecast_publication_selections).values(
                    tenant_id=DEFAULT_TENANT_ID,
                    station_id=other_station_id,
                    parameter="discharge",
                    issued_at=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
                    selected_forecast_id=forecast_id,
                    version=1,
                )
            )

    def test_database_rejects_null_required_decision_fields(
        self, db_connection: sa.Connection
    ) -> None:
        store, principal, forecast_id, _ = _seed(db_connection)
        first = _publish(store, principal, forecast_id)
        saved = (
            db_connection.execute(
                sa.select(forecast_publication_decisions).where(
                    forecast_publication_decisions.c.id == first.id
                )
            )
            .mappings()
            .one()
        )
        for action, preservation, reason_code, reason_text in (
            ("publish", None, None, None),
            ("withdraw", None, None, None),
        ):
            values = dict(saved)
            values.update(
                id=uuid4(),
                action=action,
                preservation_at_publish=preservation,
                reason_code=reason_code,
                reason_text=reason_text,
                idempotency_key=f"malformed-{action}",
            )
            with pytest.raises(IntegrityError), db_connection.begin_nested():
                db_connection.execute(
                    sa.insert(forecast_publication_decisions).values(**values)
                )
