from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sapphire_flow.types.ids import ForecastId


class PreservationStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "evidence_incomplete"


class BackupProofStatus(Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True, kw_only=True, slots=True)
class BackupProof:
    status: BackupProofStatus
    backup_id: UUID | None = None
    manifest_sha256: str | None = None
    database_dump_sha256: str | None = None
    image_archives: tuple[tuple[str, str], ...] = ()
    sample_forecast_id: ForecastId | None = None
    capture_manifest_sha256: str | None = None
    snapshot_sha256: str | None = None
    forecast_values_sha256: str | None = None
    artifact_sha256: str | None = None
    runtime_image_digest: str | None = None
    restored_at: datetime | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class PreservationAttestation:
    id: UUID
    forecast_id: ForecastId
    backup_id: UUID
    capture_manifest_sha256: str
    snapshot_sha256: str
    forecast_values_sha256: str
    artifact_sha256: str | None
    runtime_image_digest: str
    backup_manifest_sha256: str
    database_dump_sha256: str
    image_archive_sha256: str
    restored_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class PreservationAssessment:
    capture_status: PreservationStatus
    effective_status: PreservationStatus
    attestation_id: UUID | None
    remaining_reasons: tuple[str, ...]
