from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import QcStatus
    from sapphire_flow.types.ids import ObservationId, RatingCurveId
    from sapphire_flow.types.native_capture import CaptureId, Sha256Digest
    from sapphire_flow.types.provisional_evaluation import EvaluationScope
    from sapphire_flow.types.rating_reference import RatingReferenceBasis


class StoredOrderBasis(Enum):
    DATABASE_INSERTION_SEQUENCE = "database_insertion_sequence"
    MIGRATION_REGISTRATION = "migration_registration"


class SourceRevision(Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    MISSING = "missing"


class EvaluationSampleResult(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeEvaluationEvidence:
    capture_id: CaptureId
    capture_sha256: Sha256Digest


@dataclass(frozen=True, kw_only=True, slots=True)
class LegacyEvaluationEvidence:
    legacy_custody_sha256: Sha256Digest


@dataclass(frozen=True, kw_only=True, slots=True)
class SafeDerivedLineage:
    derived_fingerprint: Sha256Digest
    observation_id: ObservationId
    source_snapshot_sha256: Sha256Digest
    rating_curve_id: RatingCurveId
    curve_version: int
    file_sha256: Sha256Digest
    reference_basis: RatingReferenceBasis
    reference_id: UUID
    level_unit: Literal["m"]
    discharge_unit: Literal["m3/s"]
    evidence: NativeEvaluationEvidence | LegacyEvaluationEvidence

    def __post_init__(self) -> None:
        if self.curve_version < 1:
            raise ValueError("curve version must be positive")


@dataclass(frozen=True, kw_only=True, slots=True)
class StoredEvaluationSample:
    scope: EvaluationScope
    stored_order: int
    stored_order_basis: StoredOrderBasis
    timestamp: UtcDatetime
    value: float | None
    rejection_code: str | None
    source_revision: SourceRevision
    qc_status: QcStatus
    qc_rule_version: str | None
    warnings: tuple[str, ...]
    lineage: SafeDerivedLineage

    def __post_init__(self) -> None:
        if self.stored_order < 1:
            raise ValueError("stored order must be positive")
        if (self.value is None) == (self.rejection_code is None):
            raise ValueError("sample requires exactly one accepted value or rejection")
        if self.value is not None and (not math.isfinite(self.value) or self.value < 0):
            raise ValueError("accepted sample must be finite and nonnegative")
        if self.rejection_code is not None and not self.rejection_code.strip():
            raise ValueError("rejected sample requires a reason")

    @property
    def result(self) -> EvaluationSampleResult:
        return (
            EvaluationSampleResult.ACCEPTED
            if self.value is not None
            else EvaluationSampleResult.REJECTED
        )

    @property
    def selection_notes(self) -> tuple[str, ...]:
        return (
            ("legacy_insertion_order_unknown",)
            if self.stored_order_basis is StoredOrderBasis.MIGRATION_REGISTRATION
            else ()
        )
