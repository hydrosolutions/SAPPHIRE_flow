from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, cast

from sapphire_flow.types.forecast_evidence import EvidenceStatus
from sapphire_flow.types.forecast_preservation import (
    BackupProofStatus,
    PreservationAssessment,
    PreservationStatus,
)

if TYPE_CHECKING:
    from sapphire_flow.types.forecast_evidence import PersistedForecastEvidence
    from sapphire_flow.types.forecast_preservation import (
        BackupProof,
        PreservationAttestation,
    )
    from sapphire_flow.types.ids import ForecastId

_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def assess_effective_preservation(
    forecast_id: ForecastId,
    evidence: PersistedForecastEvidence,
    attestation: PreservationAttestation | None,
    backup_proof: BackupProof,
) -> PreservationAssessment:
    reasons = tuple(filter(None, (evidence.reason or "").split(";")))
    capture_status = PreservationStatus(evidence.status.value)
    if evidence.status is EvidenceStatus.COMPLETE:
        return PreservationAssessment(
            capture_status=capture_status,
            effective_status=PreservationStatus.COMPLETE,
            attestation_id=None,
            remaining_reasons=(),
        )
    if reasons != ("runtime_image_bytes_unpinned",):
        return PreservationAssessment(
            capture_status=capture_status,
            effective_status=PreservationStatus.INCOMPLETE,
            attestation_id=None,
            remaining_reasons=reasons,
        )
    if attestation is None:
        return PreservationAssessment(
            capture_status=capture_status,
            effective_status=PreservationStatus.INCOMPLETE,
            attestation_id=None,
            remaining_reasons=reasons,
        )
    if backup_proof.status is not BackupProofStatus.VERIFIED:
        return PreservationAssessment(
            capture_status=capture_status,
            effective_status=PreservationStatus.INCOMPLETE,
            attestation_id=attestation.id,
            remaining_reasons=(f"backup_proof_{backup_proof.status.value}",),
        )
    try:
        manifest_obj: object = json.loads(evidence.manifest_json)
    except (TypeError, ValueError):
        manifest_obj = {}
    manifest = (
        cast("dict[str, object]", manifest_obj)
        if isinstance(manifest_obj, dict)
        else {}
    )
    image_digest = manifest.get("runtime_image_digest")
    bindings_match = (
        isinstance(image_digest, str)
        and _IMAGE_DIGEST.fullmatch(image_digest) is not None
        and forecast_id == attestation.forecast_id
        and evidence.snapshot_sha256 is not None
        and evidence.snapshot_sha256 == attestation.snapshot_sha256
        and evidence.artifact_sha256 == attestation.artifact_sha256
        and hashlib.sha256(evidence.manifest_json.encode("utf-8")).hexdigest()
        == attestation.capture_manifest_sha256
        and image_digest == attestation.runtime_image_digest
        and backup_proof.backup_id == attestation.backup_id
        and backup_proof.manifest_sha256 == attestation.backup_manifest_sha256
        and backup_proof.database_dump_sha256 == attestation.database_dump_sha256
        and backup_proof.sample_forecast_id == forecast_id
        and backup_proof.capture_manifest_sha256 == attestation.capture_manifest_sha256
        and backup_proof.snapshot_sha256 == attestation.snapshot_sha256
        and backup_proof.forecast_values_sha256 == attestation.forecast_values_sha256
        and backup_proof.artifact_sha256 == attestation.artifact_sha256
        and backup_proof.runtime_image_digest == image_digest
        and backup_proof.restored_at == attestation.restored_at
        and (
            image_digest,
            attestation.image_archive_sha256,
        )
        in backup_proof.image_archives
    )
    if not bindings_match:
        return PreservationAssessment(
            capture_status=capture_status,
            effective_status=PreservationStatus.INCOMPLETE,
            attestation_id=attestation.id,
            remaining_reasons=("preservation_attestation_mismatch",),
        )
    return PreservationAssessment(
        capture_status=capture_status,
        effective_status=PreservationStatus.COMPLETE,
        attestation_id=attestation.id,
        remaining_reasons=(),
    )
