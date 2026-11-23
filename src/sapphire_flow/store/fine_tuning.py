# pyright: reportUnknownMemberType=false
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 — Pydantic resolves at runtime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from sapphire_flow.config.fine_tuning import FineTuningConfig  # noqa: TC001
from sapphire_flow.db.metadata import audit_log
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.fine_tuning_evidence import CandidateReceipt, canonical_json
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.ids import ArtifactId, StationId, TenantId

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.fine_tuning import FineTuningRequest


class _ReceiptBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: FineTuningConfig
    configuration_sha256: str
    tenant_id: UUID
    artifact_id: UUID
    artifact_sha256: str
    donor_sha256: str
    incumbent_ids: list[UUID]
    requested_stations: list[UUID]
    fitted_stations: list[UUID]
    fitting_sources: dict[str, object]
    evaluation: dict[str, object] | None
    donor_pretraining_exposure: str


def parse_receipt(value: object) -> CandidateReceipt:
    parsed = _ReceiptBoundary.model_validate(value)
    request = parsed.request.to_domain()
    if parsed.configuration_sha256 != request.sha256:
        raise ConfigurationError("fine-tuning receipt configuration digest mismatch")
    if parsed.donor_pretraining_exposure != "not_verified":
        raise ConfigurationError("unsupported donor exposure evidence")
    return CandidateReceipt(
        request=request,
        tenant_id=TenantId(parsed.tenant_id),
        artifact_id=ArtifactId(parsed.artifact_id),
        artifact_sha256=parsed.artifact_sha256,
        donor_sha256=parsed.donor_sha256,
        incumbent_ids=tuple(ArtifactId(aid) for aid in parsed.incumbent_ids),
        requested_stations=tuple(StationId(sid) for sid in parsed.requested_stations),
        fitted_stations=tuple(StationId(sid) for sid in parsed.fitted_stations),
        fitting_sources_json=canonical_json(parsed.fitting_sources),
        evaluation_json=canonical_json(parsed.evaluation)
        if parsed.evaluation is not None
        else None,
    )


class PgFineTuningReceipts:
    def __init__(self, conn: sa.Connection) -> None:
        self.conn = conn

    def append(self, receipt: CandidateReceipt, now: UtcDatetime) -> None:
        PgAuditLogStore(self.conn).append_entry(
            AuditEntry.system(
                event_type=AuditEventType.FINE_TUNING_RECEIPT,
                target_type="model_artifact",
                target_id=str(receipt.artifact_id),
                detail={"receipt": receipt.payload(), "sha256": receipt.sha256},
                ip_address=None,
                created_at=now,
            )
        )

    def latest(self, artifact_id: ArtifactId) -> CandidateReceipt:
        raw = self.conn.execute(
            sa.select(audit_log.c.detail)
            .where(
                audit_log.c.event_type == AuditEventType.FINE_TUNING_RECEIPT.value,
                audit_log.c.target_type == "model_artifact",
                audit_log.c.target_id == str(artifact_id),
            )
            .order_by(audit_log.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if raw is None:
            raise ConfigurationError("no fine-tuning receipt for this artifact")
        receipt = parse_receipt(raw["receipt"])
        if receipt.sha256 != raw["sha256"] or receipt.artifact_id != artifact_id:
            raise ConfigurationError("fine-tuning receipt integrity check failed")
        return receipt

    def decision(self, artifact_id: ArtifactId) -> dict[str, object] | None:
        return self.conn.execute(
            sa.select(audit_log.c.detail)
            .where(
                audit_log.c.event_type == AuditEventType.FINE_TUNING_DECISION.value,
                audit_log.c.target_type == "model_artifact",
                audit_log.c.target_id == str(artifact_id),
            )
            .order_by(audit_log.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def refuse_retained_candidate(self, request: FineTuningRequest) -> None:
        retained = self.conn.execute(
            sa.select(audit_log.c.id)
            .where(
                audit_log.c.event_type == AuditEventType.FINE_TUNING_RECEIPT.value,
                audit_log.c.detail["receipt"]["request"]["request_id"].astext
                == str(request.request_id),
            )
            .limit(1)
        ).scalar_one_or_none()
        if retained is not None:
            raise ConfigurationError(
                "request already has a retained candidate; resume its evaluation, "
                "or use a new request UUID for a deliberate new fit"
            )


def scope_lock_key(request: FineTuningRequest, tenant_id: TenantId) -> int:
    identity = canonical_json(
        [str(tenant_id), str(request.model_id), str(request.group_id)]
    )
    return int.from_bytes(
        hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True
    )


@contextmanager
def fitting_lock(
    engine: sa.Engine, request: FineTuningRequest, tenant_id: TenantId
) -> Iterator[None]:
    # Dedicated connection: never leave a transaction open during model fitting.
    key = scope_lock_key(request, tenant_id)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        acquired = conn.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar_one()
        if not acquired:
            raise ConfigurationError(
                "another fine-tuning operation owns this model/group"
            )
        try:
            yield
        finally:
            conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def lock_decision(conn: sa.Connection, receipt: CandidateReceipt) -> None:
    acquired = conn.execute(
        sa.text("SELECT pg_try_advisory_xact_lock(:key)"),
        {
            "key": scope_lock_key(receipt.request, receipt.tenant_id),
        },
    ).scalar_one()
    if not acquired:
        raise ConfigurationError("another fine-tuning operation owns this model/group")
