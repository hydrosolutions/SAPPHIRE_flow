# pyright: reportUnknownMemberType=false
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.training import promote_artifact
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType, ModelArtifactStatus

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ModelArtifactStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.services.fine_tuning_evidence import CandidateReceipt
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.write_principal import WritePrincipal


class ReviewDecision(Enum):
    APPROVE = "approve"
    REJECT = "reject"


def decide_candidate(
    receipt: CandidateReceipt,
    *,
    reviewed_sha256: str,
    principal: WritePrincipal,
    artifacts: ModelArtifactStore,
    groups: StationGroupStore,
    stations: StationStore,
    audit: AuditLogStore,
    decision: ReviewDecision,
    reason: str,
    now: UtcDatetime,
) -> None:
    """Caller holds the scope lock and one real transaction for all writes."""
    if not reason.strip():
        raise ConfigurationError("admin decision requires a reason")
    if principal.tenant_id != receipt.tenant_id:
        raise ConfigurationError("candidate is outside the authorized tenant")
    if reviewed_sha256 != receipt.sha256:
        raise ConfigurationError("review is stale; read the current report again")
    request = receipt.request
    group = groups.fetch_group(request.group_id)
    if group is None or group.tenant_id != receipt.tenant_id:
        raise ConfigurationError("candidate group is missing or changed tenant")
    if group.station_ids != frozenset(receipt.requested_stations):
        raise ConfigurationError(
            "group membership changed since fitting; review refused"
        )
    for sid in group.station_ids:
        station = stations.fetch_station(sid)
        if station is None or station.tenant_id != receipt.tenant_id:
            raise ConfigurationError(
                "candidate group contains foreign or missing stations"
            )
    candidate = artifacts.fetch_artifact_record(receipt.artifact_id)
    if (
        candidate is None
        or candidate.model_id != request.model_id
        or candidate.group_id != group.id
    ):
        raise ConfigurationError("candidate artifact identity mismatch")
    if candidate.sha256_hash != receipt.artifact_sha256:
        raise ConfigurationError("candidate artifact digest changed")
    allowed = {ModelArtifactStatus.TRAINING, ModelArtifactStatus.PENDING_APPROVAL}
    if candidate.status not in allowed:
        raise ConfigurationError(
            "candidate already has a decision or is not reviewable"
        )
    if decision is ReviewDecision.APPROVE:
        donor = artifacts.fetch_artifact_record(request.base_artifact_id)
        if donor is None or donor.sha256_hash != receipt.donor_sha256:
            raise ConfigurationError("donor identity changed since fitting")
        report: dict[str, object] = (
            json.loads(receipt.evaluation_json) if receipt.evaluation_json else {}
        )
        if (
            candidate.status is not ModelArtifactStatus.PENDING_APPROVAL
            or report.get("state") != "complete"
        ):
            raise ConfigurationError("candidate evaluation is incomplete")
        retained = artifacts.fetch_artifact(receipt.artifact_id)
        if (
            retained is None
            or hashlib.sha256(retained[1]).hexdigest() != receipt.artifact_sha256
        ):
            raise ConfigurationError("candidate bytes are missing or changed")
        promote_artifact(
            artifacts,
            request.model_id,
            receipt.artifact_id,
            group_id=group.id,
            principal=principal,
            target_tenant_id=receipt.tenant_id,
            audit_log_store=audit,
            now=now,
            audit_rejection=False,
            expected_active_ids=frozenset(receipt.incumbent_ids),
        )
    else:
        artifacts.transition_artifact_status(
            receipt.artifact_id, ModelArtifactStatus.REJECTED
        )
    audit.append_entry(
        AuditEntry.system(
            event_type=AuditEventType.FINE_TUNING_DECISION,
            target_type="model_artifact",
            target_id=str(receipt.artifact_id),
            detail={
                "decision": decision.value,
                "reason": reason,
                "operator": str(principal.id),
                "tenant_id": str(receipt.tenant_id),
                "reviewed_sha256": reviewed_sha256,
            },
            ip_address=None,
            created_at=now,
        )
    )
