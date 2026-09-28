# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.db.metadata import (
    forecast_evidence,
    forecast_evidence_blobs,
    forecast_preservation_attestations,
    forecast_publication_decisions,
    forecast_publication_events,
    forecast_publication_selections,
    forecast_publication_sequence,
    forecasts,
    pipeline_health,
    protected_backup_forecast_proofs,
    protected_backup_health,
    stations,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.forecast_values_integrity import forecast_values_integrity
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AuditEventType,
    PipelineCheckType,
    PipelineHealthStatus,
)
from sapphire_flow.types.forecast_publication import (
    PreservationAtPublish,
    PublicationAction,
    PublicationDecision,
    PublicationEventType,
    PublicationKey,
    PublicationSelection,
    PublishRequest,
    WithdrawRequest,
)
from sapphire_flow.types.ids import (
    ForecastId,
    PublicationDecisionId,
    StationId,
    TenantId,
    UserId,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from datetime import datetime

    from sqlalchemy import RowMapping

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.human_auth import HumanPrincipal

log = structlog.get_logger(__name__)
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class PublicationConflictError(ValueError):
    pass


class PublicationForbiddenError(PermissionError):
    pass


class PublicationNotFoundError(LookupError):
    pass


class PublicationUnavailableError(RuntimeError):
    pass


class PublicationBackupOverdueError(PublicationUnavailableError):
    def __init__(self, count: int, oldest_at: datetime) -> None:
        super().__init__("forecast-specific protected backup proof is overdue")
        self.count = count
        self.oldest_at = oldest_at


def _request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key_from_header(row: RowMapping) -> PublicationKey:
    return PublicationKey(
        tenant_id=TenantId(row["tenant_id"]),
        station_id=StationId(row["station_id"]),
        parameter=row["parameter"],
        issued_at=ensure_utc(row["issued_at"]),
    )


def _decision_from_row(row: RowMapping) -> PublicationDecision:
    from sapphire_flow.types.forecast_publication import WithdrawalReasonCode

    return PublicationDecision(
        id=PublicationDecisionId(row["id"]),
        key=PublicationKey(
            tenant_id=TenantId(row["tenant_id"]),
            station_id=StationId(row["station_id"]),
            parameter=row["parameter"],
            issued_at=ensure_utc(row["issued_at"]),
        ),
        forecast_id=ForecastId(row["forecast_id"]),
        actor_user_id=UserId(row["actor_user_id"]),
        action=PublicationAction(row["action"]),
        selection_version=row["selection_version"],
        forecast_version=row["forecast_version"],
        preservation_at_publish=(
            PreservationAtPublish(row["preservation_at_publish"])
            if row["preservation_at_publish"] is not None
            else None
        ),
        replaced_forecast_id=(
            ForecastId(row["replaced_forecast_id"])
            if row["replaced_forecast_id"] is not None
            else None
        ),
        replaced_decision_id=(
            PublicationDecisionId(row["replaced_decision_id"])
            if row["replaced_decision_id"] is not None
            else None
        ),
        reason_code=(
            WithdrawalReasonCode(row["reason_code"])
            if row["reason_code"] is not None
            else None
        ),
        reason_text=row["reason_text"],
        created_at=ensure_utc(row["created_at"]),
    )


class PgForecastPublicationStore:
    def __init__(
        self,
        conn: sa.Connection,
        *,
        transaction_factory: Callable[[], AbstractContextManager[sa.Connection]]
        | None = None,
        backup_max_age: timedelta = timedelta(hours=36),
        proof_window: timedelta = timedelta(hours=36),
    ) -> None:
        self._conn = conn
        self._begin = transaction_factory or conn.engine.begin
        self._backup_max_age = backup_max_age
        self._proof_window = proof_window

    def publish(
        self,
        request: PublishRequest,
        principal: HumanPrincipal,
        *,
        decision_id: PublicationDecisionId,
        now: UtcDatetime,
    ) -> PublicationDecision:
        try:
            with self._begin() as txn:
                return self._publish(txn, request, principal, decision_id, now)
        except PublicationBackupOverdueError as exc:
            self._record_overdue_health(exc, now)
            raise

    def _publish(
        self,
        txn: sa.Connection,
        request: PublishRequest,
        principal: HumanPrincipal,
        decision_id: PublicationDecisionId,
        now: UtcDatetime,
    ) -> PublicationDecision:
        header = self._forecast_header(txn, request.forecast_id)
        key = _key_from_header(header)
        self._authorize(txn, principal, key.station_id, key.tenant_id)
        self._lock_idempotency(
            txn,
            principal.user_id,
            key.tenant_id,
            PublicationAction.PUBLISH,
            request.idempotency_key,
        )
        digest = _request_hash(
            {
                "forecast_id": str(request.forecast_id),
                "expected_forecast_version": request.expected_forecast_version,
                "expected_selection_version": request.expected_selection_version,
            }
        )
        replay = self._replay(
            txn,
            principal.user_id,
            principal.tenant_id,
            PublicationAction.PUBLISH,
            request.idempotency_key,
            digest,
        )
        if replay is not None:
            return replay

        selection, created = self._lock_selection(txn, key, now, create=True)
        replay = self._replay(
            txn,
            principal.user_id,
            principal.tenant_id,
            PublicationAction.PUBLISH,
            request.idempotency_key,
            digest,
        )
        if replay is not None:
            return replay
        if request.expected_selection_version != (
            None if created else selection.version
        ):
            raise PublicationConflictError("selection version is stale")
        locked = self._forecast_header(txn, request.forecast_id, lock=True)
        if locked["version"] != request.expected_forecast_version:
            raise PublicationConflictError("forecast version is stale")
        if locked["status"] == "superseded":
            raise PublicationConflictError(
                "superseded forecast is not a publication candidate"
            )
        if locked["qc_status"] == "qc_failed":
            raise PublicationConflictError("QC-failed forecast cannot be published")
        if self._is_withdrawn(txn, request.forecast_id):
            raise PublicationConflictError("withdrawn forecast cannot be republished")
        prior_id = selection.selected_forecast_id
        if prior_id == request.forecast_id:
            raise PublicationConflictError("forecast is already selected")
        if prior_id is not None and selection.linked_warning_publication_id is not None:
            raise PublicationConflictError("linked warning requires a joint decision")

        self._require_backup_health(txn, now)
        self._require_no_overdue_proofs(txn, now)
        preservation = self._preservation_at_publish(txn, request.forecast_id, now)
        replaced_decision_id = (
            self._current_decision_id(txn, key, selection.version)
            if prior_id is not None
            else None
        )
        new_version = selection.version + 1
        txn.execute(
            sa.update(forecast_publication_selections)
            .where(*self._key_predicates(key))
            .values(
                selected_forecast_id=request.forecast_id,
                version=new_version,
                updated_at=now,
            )
        )
        return self._insert_decision(
            txn,
            decision_id=decision_id,
            key=key,
            forecast_id=request.forecast_id,
            actor_id=principal.user_id,
            action=PublicationAction.PUBLISH,
            forecast_version=locked["version"],
            selection_version=new_version,
            preservation=preservation,
            replaced_forecast_id=prior_id,
            replaced_decision_id=replaced_decision_id,
            reason_code=None,
            reason_text=None,
            idempotency_key=request.idempotency_key,
            request_sha256=digest,
            now=now,
        )

    def withdraw(
        self,
        request: WithdrawRequest,
        principal: HumanPrincipal,
        *,
        decision_id: PublicationDecisionId,
        now: UtcDatetime,
    ) -> PublicationDecision:
        with self._begin() as txn:
            header = self._forecast_header(txn, request.forecast_id)
            key = _key_from_header(header)
            self._authorize(txn, principal, key.station_id, key.tenant_id)
            self._lock_idempotency(
                txn,
                principal.user_id,
                key.tenant_id,
                PublicationAction.WITHDRAW,
                request.idempotency_key,
            )
            digest = _request_hash(
                {
                    "forecast_id": str(request.forecast_id),
                    "expected_selection_version": request.expected_selection_version,
                    "reason_code": request.reason_code.value,
                    "reason_text": request.reason_text,
                }
            )
            replay = self._replay(
                txn,
                principal.user_id,
                principal.tenant_id,
                PublicationAction.WITHDRAW,
                request.idempotency_key,
                digest,
            )
            if replay is not None:
                return replay
            selection, _ = self._lock_selection(txn, key, now, create=False)
            replay = self._replay(
                txn,
                principal.user_id,
                principal.tenant_id,
                PublicationAction.WITHDRAW,
                request.idempotency_key,
                digest,
            )
            if replay is not None:
                return replay
            if request.expected_selection_version != selection.version:
                raise PublicationConflictError("selection version is stale")
            if self._is_withdrawn(txn, request.forecast_id):
                raise PublicationConflictError("forecast is already withdrawn")
            published = txn.execute(
                sa.select(forecast_publication_decisions.c.id).where(
                    forecast_publication_decisions.c.forecast_id == request.forecast_id,
                    forecast_publication_decisions.c.action
                    == PublicationAction.PUBLISH.value,
                )
            ).first()
            if published is None:
                raise PublicationNotFoundError("forecast has no publication decision")
            selected = selection.selected_forecast_id == request.forecast_id
            if selected and selection.linked_warning_publication_id is not None:
                raise PublicationConflictError("linked warning blocks withdrawal")
            new_version = selection.version + 1 if selected else selection.version
            if selected:
                txn.execute(
                    sa.update(forecast_publication_selections)
                    .where(*self._key_predicates(key))
                    .values(
                        selected_forecast_id=None, version=new_version, updated_at=now
                    )
                )
            return self._insert_decision(
                txn,
                decision_id=decision_id,
                key=key,
                forecast_id=request.forecast_id,
                actor_id=principal.user_id,
                action=PublicationAction.WITHDRAW,
                forecast_version=header["version"],
                selection_version=new_version,
                preservation=None,
                replaced_forecast_id=None,
                replaced_decision_id=None,
                reason_code=request.reason_code.value,
                reason_text=request.reason_text.strip(),
                idempotency_key=request.idempotency_key,
                request_sha256=digest,
                now=now,
            )

    def fetch_selection(self, key: PublicationKey) -> PublicationSelection | None:
        row = (
            self._conn.execute(
                sa.select(forecast_publication_selections).where(
                    *self._key_predicates(key)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._selection_from_row(key, row)

    def fetch_decisions(self, forecast_id: ForecastId) -> list[PublicationDecision]:
        rows = (
            self._conn.execute(
                sa.select(forecast_publication_decisions)
                .join(
                    forecast_publication_events,
                    forecast_publication_events.c.decision_id
                    == forecast_publication_decisions.c.id,
                )
                .where(forecast_publication_decisions.c.forecast_id == forecast_id)
                .order_by(forecast_publication_events.c.sequence)
            )
            .mappings()
            .all()
        )
        return [_decision_from_row(row) for row in rows]

    def _forecast_header(
        self, txn: sa.Connection, forecast_id: ForecastId, *, lock: bool = False
    ) -> RowMapping:
        query = (
            sa.select(
                forecasts.c.id,
                forecasts.c.station_id,
                forecasts.c.parameter,
                forecasts.c.issued_at,
                forecasts.c.version,
                forecasts.c.status,
                forecasts.c.qc_status,
                stations.c.tenant_id,
            )
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(forecasts.c.id == forecast_id)
        )
        if lock:
            txn.execute(
                sa.select(sa.func.lock_publication_candidate(forecast_id))
            ).scalar_one()
        row = txn.execute(query).mappings().one_or_none()
        if row is None:
            raise PublicationNotFoundError("forecast does not exist")
        return row

    @staticmethod
    def _authorize(
        txn: sa.Connection,
        principal: HumanPrincipal,
        station_id: StationId,
        tenant_id: TenantId,
    ) -> None:
        if principal.tenant_id != tenant_id:
            raise PublicationForbiddenError("human has no station publication grant")
        allowed = txn.execute(
            sa.select(
                sa.func.lock_publication_grants(
                    principal.user_id, tenant_id, station_id
                )
            )
        ).scalar_one()
        if not allowed:
            raise PublicationForbiddenError("human has no station publication grant")

    @staticmethod
    def _key_predicates(key: PublicationKey) -> tuple[sa.ColumnElement[bool], ...]:
        return (
            forecast_publication_selections.c.tenant_id == key.tenant_id,
            forecast_publication_selections.c.station_id == key.station_id,
            forecast_publication_selections.c.parameter == key.parameter,
            forecast_publication_selections.c.issued_at == key.issued_at,
        )

    @staticmethod
    def _selection_from_row(
        key: PublicationKey, row: RowMapping
    ) -> PublicationSelection:
        return PublicationSelection(
            key=key,
            selected_forecast_id=(
                ForecastId(row["selected_forecast_id"])
                if row["selected_forecast_id"] is not None
                else None
            ),
            version=row["version"],
            linked_warning_publication_id=row["linked_warning_publication_id"],
        )

    def _lock_selection(
        self, txn: sa.Connection, key: PublicationKey, now: UtcDatetime, *, create: bool
    ) -> tuple[PublicationSelection, bool]:
        created = False
        if create:
            inserted = txn.execute(
                pg_insert(forecast_publication_selections)
                .values(
                    tenant_id=key.tenant_id,
                    station_id=key.station_id,
                    parameter=key.parameter,
                    issued_at=key.issued_at,
                    selected_forecast_id=None,
                    version=0,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing()
                .returning(forecast_publication_selections.c.tenant_id)
            ).scalar_one_or_none()
            created = inserted is not None
        row = (
            txn.execute(
                sa.select(forecast_publication_selections)
                .where(*self._key_predicates(key))
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PublicationNotFoundError("publication selection does not exist")
        return self._selection_from_row(key, row), created

    @staticmethod
    def _lock_idempotency(
        txn: sa.Connection,
        actor_id: UserId,
        tenant_id: TenantId,
        action: PublicationAction,
        idempotency_key: str,
    ) -> None:
        scope = f"{actor_id}:{tenant_id}:{action.value}:{idempotency_key}"
        txn.execute(
            sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(scope, 0)))
        )

    @staticmethod
    def _replay(
        txn: sa.Connection,
        actor_id: UserId,
        tenant_id: TenantId,
        action: PublicationAction,
        idempotency_key: str,
        request_sha256: str,
    ) -> PublicationDecision | None:
        row = (
            txn.execute(
                sa.select(forecast_publication_decisions).where(
                    forecast_publication_decisions.c.actor_user_id == actor_id,
                    forecast_publication_decisions.c.tenant_id == tenant_id,
                    forecast_publication_decisions.c.action == action.value,
                    forecast_publication_decisions.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["request_sha256"] != request_sha256:
            raise PublicationConflictError("idempotency key has different input")
        return _decision_from_row(row)

    @staticmethod
    def _is_withdrawn(txn: sa.Connection, forecast_id: ForecastId) -> bool:
        return (
            txn.execute(
                sa.select(forecast_publication_decisions.c.id).where(
                    forecast_publication_decisions.c.forecast_id == forecast_id,
                    forecast_publication_decisions.c.action
                    == PublicationAction.WITHDRAW.value,
                )
            ).first()
            is not None
        )

    @staticmethod
    def _current_decision_id(
        txn: sa.Connection, key: PublicationKey, selection_version: int
    ) -> PublicationDecisionId:
        value = txn.execute(
            sa.select(forecast_publication_decisions.c.id).where(
                forecast_publication_decisions.c.tenant_id == key.tenant_id,
                forecast_publication_decisions.c.station_id == key.station_id,
                forecast_publication_decisions.c.parameter == key.parameter,
                forecast_publication_decisions.c.issued_at == key.issued_at,
                forecast_publication_decisions.c.selection_version == selection_version,
                forecast_publication_decisions.c.action
                == PublicationAction.PUBLISH.value,
            )
        ).scalar_one_or_none()
        if value is None:
            raise PublicationConflictError("selected forecast has no decision")
        return PublicationDecisionId(value)

    def _require_backup_health(self, txn: sa.Connection, now: UtcDatetime) -> None:
        row = (
            txn.execute(
                sa.select(protected_backup_health).where(
                    protected_backup_health.c.id == 1
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["status"] != "verified":
            raise PublicationUnavailableError(
                "protected backup health is missing or unhealthy"
            )
        if not row["target_separate"] or not row["retention_ready"]:
            raise PublicationUnavailableError("protected backup target is not ready")
        if row["backup_id"] is None or row["manifest_sha256"] is None:
            raise PublicationUnavailableError("protected backup proof is incomplete")
        if row["checked_at"] > now or row["restored_at"] > now:
            raise PublicationUnavailableError("backup health timestamp is invalid")
        if (
            now - row["checked_at"] > self._backup_max_age
            or now - row["restored_at"] > self._backup_max_age
        ):
            raise PublicationUnavailableError("protected backup health is stale")

    def _require_no_overdue_proofs(self, txn: sa.Connection, now: UtcDatetime) -> None:
        pending = forecast_publication_decisions.alias("pending")
        proof_exists = sa.exists(
            sa.select(protected_backup_forecast_proofs.c.id).where(
                protected_backup_forecast_proofs.c.publication_decision_id
                == pending.c.id,
                protected_backup_forecast_proofs.c.verified_at <= now,
            )
        )
        row = txn.execute(
            sa.select(sa.func.count(), sa.func.min(pending.c.created_at)).where(
                pending.c.preservation_at_publish
                == PreservationAtPublish.BACKUP_PENDING.value,
                pending.c.created_at < now - self._proof_window,
                ~proof_exists,
            )
        ).one()
        if row[0]:
            raise PublicationBackupOverdueError(row[0], row[1])

    def _preservation_at_publish(
        self, txn: sa.Connection, forecast_id: ForecastId, now: UtcDatetime
    ) -> PreservationAtPublish:
        evidence = (
            txn.execute(
                sa.select(forecast_evidence).where(
                    forecast_evidence.c.forecast_id == forecast_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if evidence is None:
            raise PublicationUnavailableError("forecast has no captured evidence")
        if evidence["snapshot_sha256"] is None or evidence["thresholds_json"] is None:
            raise PublicationUnavailableError("forecast evidence lacks required inputs")
        retained_snapshot = txn.execute(
            sa.select(forecast_evidence_blobs.c.payload).where(
                forecast_evidence_blobs.c.sha256 == evidence["snapshot_sha256"]
            )
        ).scalar_one_or_none()
        if (
            retained_snapshot is None
            or hashlib.sha256(retained_snapshot).hexdigest()
            != evidence["snapshot_sha256"]
        ):
            raise PublicationUnavailableError("input snapshot is not retained intact")
        if evidence["artifact_sha256"] is not None:
            retained_artifact = txn.execute(
                sa.select(forecast_evidence_blobs.c.payload).where(
                    forecast_evidence_blobs.c.sha256 == evidence["artifact_sha256"]
                )
            ).scalar_one_or_none()
            if (
                retained_artifact is None
                or hashlib.sha256(retained_artifact).hexdigest()
                != evidence["artifact_sha256"]
            ):
                raise PublicationUnavailableError(
                    "model artifact is not retained intact"
                )
        if (
            evidence["status"] == "evidence_incomplete"
            and evidence["reason"] != "runtime_image_bytes_unpinned"
        ):
            raise PublicationUnavailableError(
                "forecast evidence has an unresolved capture gap"
            )
        if evidence["status"] not in ("complete", "evidence_incomplete"):
            raise PublicationUnavailableError("forecast evidence status is invalid")
        try:
            manifest = json.loads(evidence["manifest_json"])
        except (TypeError, ValueError) as exc:
            raise PublicationUnavailableError(
                "forecast evidence manifest is unreadable"
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("forecast_id") != str(
            forecast_id
        ):
            raise PublicationUnavailableError(
                "forecast evidence manifest does not match forecast"
            )
        if (
            manifest.get("thresholds_sha256")
            != hashlib.sha256(evidence["thresholds_json"].encode("utf-8")).hexdigest()
        ):
            raise PublicationUnavailableError(
                "threshold snapshot does not match manifest"
            )
        if (
            manifest.get("model_artifact_id") is not None
            and evidence["artifact_sha256"] is None
        ):
            raise PublicationUnavailableError("model artifact bytes are not retained")
        if (
            not isinstance(manifest.get("runtime_image_digest"), str)
            or _IMAGE_DIGEST.fullmatch(manifest["runtime_image_digest"]) is None
        ):
            raise PublicationUnavailableError("runtime image identity is missing")
        value_count, values_hash = forecast_values_integrity(txn, forecast_id)
        legacy_output = (
            "forecast_values_count" not in manifest
            and "forecast_values_sha256" not in manifest
        )
        if value_count < 1 or (
            not legacy_output
            and (
                manifest.get("forecast_values_count") != value_count
                or manifest.get("forecast_values_sha256") != values_hash
            )
        ):
            raise PublicationUnavailableError("forecast output differs from capture")
        attestation = forecast_preservation_attestations
        proof = txn.execute(
            sa.select(
                attestation.c.id,
                attestation.c.forecast_id,
                attestation.c.backup_id,
                attestation.c.capture_manifest_sha256,
                attestation.c.snapshot_sha256,
                attestation.c.artifact_sha256,
                attestation.c.runtime_image_digest,
                attestation.c.forecast_values_sha256,
            )
            .join(
                protected_backup_forecast_proofs,
                protected_backup_forecast_proofs.c.attestation_id == attestation.c.id,
            )
            .where(
                protected_backup_forecast_proofs.c.forecast_id == forecast_id,
                protected_backup_forecast_proofs.c.verified_at
                >= now - self._backup_max_age,
                protected_backup_forecast_proofs.c.verified_at <= now,
            )
            .order_by(protected_backup_forecast_proofs.c.verified_at.desc())
            .limit(1)
        ).one_or_none()
        if proof is None:
            if legacy_output:
                raise PublicationUnavailableError(
                    "legacy forecast output requires a fresh protected proof"
                )
            return PreservationAtPublish.BACKUP_PENDING
        manifest_hash = hashlib.sha256(
            evidence["manifest_json"].encode("utf-8")
        ).hexdigest()
        if (
            proof.forecast_id != forecast_id
            or proof.capture_manifest_sha256 != manifest_hash
            or proof.snapshot_sha256 != evidence["snapshot_sha256"]
            or proof.artifact_sha256 != evidence["artifact_sha256"]
            or proof.runtime_image_digest != manifest["runtime_image_digest"]
            or proof.forecast_values_sha256 != values_hash
        ):
            raise PublicationUnavailableError(
                "protected backup proof does not match evidence"
            )
        return PreservationAtPublish.VERIFIED

    @staticmethod
    def _insert_decision(
        txn: sa.Connection,
        *,
        decision_id: PublicationDecisionId,
        key: PublicationKey,
        forecast_id: ForecastId,
        actor_id: UserId,
        action: PublicationAction,
        forecast_version: int,
        selection_version: int,
        preservation: PreservationAtPublish | None,
        replaced_forecast_id: ForecastId | None,
        replaced_decision_id: PublicationDecisionId | None,
        reason_code: str | None,
        reason_text: str | None,
        idempotency_key: str,
        request_sha256: str,
        now: UtcDatetime,
    ) -> PublicationDecision:
        values = {
            "id": decision_id,
            "tenant_id": key.tenant_id,
            "station_id": key.station_id,
            "parameter": key.parameter,
            "issued_at": key.issued_at,
            "forecast_id": forecast_id,
            "actor_user_id": actor_id,
            "action": action.value,
            "selection_version": selection_version,
            "forecast_version": forecast_version,
            "preservation_at_publish": preservation.value if preservation else None,
            "replaced_forecast_id": replaced_forecast_id,
            "replaced_decision_id": replaced_decision_id,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "idempotency_key": idempotency_key,
            "request_sha256": request_sha256,
            "created_at": now,
        }
        txn.execute(sa.insert(forecast_publication_decisions).values(**values))
        PgAuditLogStore(txn).append_entry(
            AuditEntry.user(
                actor_id=actor_id,
                event_type=AuditEventType.FORECAST_PUBLICATION_DECIDED,
                target_type="forecast_publication_decision",
                target_id=str(decision_id),
                detail={
                    "action": action.value,
                    "forecast_id": str(forecast_id),
                    "tenant_id": str(key.tenant_id),
                    "station_id": str(key.station_id),
                    "selection_version": selection_version,
                    "replaced_forecast_id": str(replaced_forecast_id)
                    if replaced_forecast_id
                    else None,
                    "reason_code": reason_code,
                    "reason_text": reason_text,
                },
                ip_address=None,
                created_at=now,
            )
        )
        sequence = txn.execute(
            sa.update(forecast_publication_sequence)
            .where(forecast_publication_sequence.c.id == 1)
            .values(next_value=forecast_publication_sequence.c.next_value + 1)
            .returning(forecast_publication_sequence.c.next_value - 1)
        ).scalar_one()
        event_type = (
            PublicationEventType.WITHDRAWN
            if action is PublicationAction.WITHDRAW
            else PublicationEventType.REPLACED
            if replaced_forecast_id
            else PublicationEventType.PUBLISHED
        )
        txn.execute(
            sa.insert(forecast_publication_events).values(
                sequence=sequence,
                decision_id=decision_id,
                event_type=event_type.value,
                forecast_id=forecast_id,
                replaced_forecast_id=replaced_forecast_id,
                actor_user_id=actor_id,
                created_at=now,
            )
        )
        saved = (
            txn.execute(
                sa.select(forecast_publication_decisions).where(
                    forecast_publication_decisions.c.id == decision_id
                )
            )
            .mappings()
            .one()
        )
        return _decision_from_row(saved)

    def _record_overdue_health(
        self, overdue: PublicationBackupOverdueError, now: UtcDatetime
    ) -> None:
        try:
            with self._conn.engine.begin() as txn:
                txn.execute(
                    sa.insert(pipeline_health).values(
                        check_type=PipelineCheckType.PUBLICATION_PROOF_OVERDUE.value,
                        checked_at=now,
                        status=PipelineHealthStatus.CRITICAL.value,
                        subject="forecast_publication_proofs",
                        detail={
                            "count": overdue.count,
                            "oldest_pending_at": overdue.oldest_at.isoformat(),
                        },
                        cycle_time=None,
                    )
                )
        except SQLAlchemyError:
            log.exception("forecast_publication.overdue_health_write_failed")
