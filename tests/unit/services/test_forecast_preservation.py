from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from sapphire_flow.services.forecast_preservation import assess_effective_preservation
from sapphire_flow.types.forecast_evidence import (
    EvidenceStatus,
    PersistedForecastEvidence,
)
from sapphire_flow.types.forecast_preservation import (
    BackupProof,
    BackupProofStatus,
    PreservationAttestation,
    PreservationStatus,
)
from sapphire_flow.types.ids import ForecastId


def _case() -> tuple[PersistedForecastEvidence, PreservationAttestation]:
    image = "sha256:" + "a" * 64
    manifest_json = json.dumps({"runtime_image_digest": image})
    evidence = PersistedForecastEvidence(
        status=EvidenceStatus.INCOMPLETE,
        manifest_json=manifest_json,
        snapshot=b"snapshot",
        snapshot_sha256=hashlib.sha256(b"snapshot").hexdigest(),
        artifact=b"artifact",
        artifact_sha256=hashlib.sha256(b"artifact").hexdigest(),
        thresholds_json="[]",
        reason="runtime_image_bytes_unpinned",
    )
    attestation = PreservationAttestation(
        id=uuid4(),
        forecast_id=ForecastId(uuid4()),
        backup_id=uuid4(),
        capture_manifest_sha256=hashlib.sha256(manifest_json.encode()).hexdigest(),
        snapshot_sha256=evidence.snapshot_sha256 or "",
        forecast_values_sha256="f" * 64,
        artifact_sha256=evidence.artifact_sha256,
        runtime_image_digest=image,
        backup_manifest_sha256="b" * 64,
        database_dump_sha256="c" * 64,
        image_archive_sha256="d" * 64,
        restored_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    return evidence, attestation


def _proof(attestation: PreservationAttestation) -> BackupProof:
    return BackupProof(
        status=BackupProofStatus.VERIFIED,
        backup_id=attestation.backup_id,
        manifest_sha256=attestation.backup_manifest_sha256,
        database_dump_sha256=attestation.database_dump_sha256,
        image_archives=(
            (attestation.runtime_image_digest, attestation.image_archive_sha256),
        ),
        sample_forecast_id=attestation.forecast_id,
        capture_manifest_sha256=attestation.capture_manifest_sha256,
        snapshot_sha256=attestation.snapshot_sha256,
        forecast_values_sha256=attestation.forecast_values_sha256,
        artifact_sha256=attestation.artifact_sha256,
        runtime_image_digest=attestation.runtime_image_digest,
        restored_at=attestation.restored_at,
    )


class TestAssessEffectivePreservation:
    def test_verified_restored_image_covers_only_capture_gap(self) -> None:
        evidence, attestation = _case()

        result = assess_effective_preservation(
            attestation.forecast_id, evidence, attestation, _proof(attestation)
        )

        assert result.capture_status is PreservationStatus.INCOMPLETE
        assert result.effective_status is PreservationStatus.COMPLETE
        assert result.attestation_id == attestation.id
        assert result.remaining_reasons == ()

    def test_missing_or_stale_proof_keeps_capture_incomplete(self) -> None:
        evidence, attestation = _case()

        missing = assess_effective_preservation(
            attestation.forecast_id,
            evidence,
            None,
            BackupProof(status=BackupProofStatus.MISSING),
        )
        stale = assess_effective_preservation(
            attestation.forecast_id,
            evidence,
            attestation,
            BackupProof(status=BackupProofStatus.STALE),
        )

        assert missing.effective_status is PreservationStatus.INCOMPLETE
        assert stale.effective_status is PreservationStatus.INCOMPLETE
        assert stale.remaining_reasons == ("backup_proof_stale",)

    def test_other_gap_cannot_be_covered_by_image_attestation(self) -> None:
        evidence, attestation = _case()
        evidence = replace(
            evidence,
            reason="runtime_image_bytes_unpinned;observation_provenance_unavailable",
        )

        result = assess_effective_preservation(
            attestation.forecast_id, evidence, attestation, _proof(attestation)
        )

        assert result.effective_status is PreservationStatus.INCOMPLETE
        assert "observation_provenance_unavailable" in result.remaining_reasons

    def test_mismatched_capture_hash_remains_incomplete(self) -> None:
        evidence, attestation = _case()

        result = assess_effective_preservation(
            attestation.forecast_id,
            evidence,
            replace(attestation, capture_manifest_sha256="f" * 64),
            _proof(attestation),
        )

        assert result.effective_status is PreservationStatus.INCOMPLETE
        assert result.remaining_reasons == ("preservation_attestation_mismatch",)

    def test_unrelated_verified_backup_cannot_complete_evidence(self) -> None:
        evidence, attestation = _case()
        proof = replace(_proof(attestation), backup_id=uuid4())

        result = assess_effective_preservation(
            attestation.forecast_id, evidence, attestation, proof
        )

        assert result.effective_status is PreservationStatus.INCOMPLETE
        assert result.remaining_reasons == ("preservation_attestation_mismatch",)

    def test_wrong_restored_sample_cannot_complete_evidence(self) -> None:
        evidence, attestation = _case()
        proof = replace(_proof(attestation), sample_forecast_id=ForecastId(uuid4()))

        result = assess_effective_preservation(
            attestation.forecast_id, evidence, attestation, proof
        )

        assert result.effective_status is PreservationStatus.INCOMPLETE

    def test_wrong_restored_snapshot_cannot_complete_evidence(self) -> None:
        evidence, attestation = _case()
        proof = replace(_proof(attestation), snapshot_sha256="e" * 64)

        result = assess_effective_preservation(
            attestation.forecast_id, evidence, attestation, proof
        )

        assert result.effective_status is PreservationStatus.INCOMPLETE
