from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId, TenantId
from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.provisional_evaluation import EvaluationScope
from sapphire_flow.types.provisional_evaluation_reads import (
    EvaluationSampleResult,
    LegacyEvaluationEvidence,
    SafeDerivedLineage,
    SourceRevision,
    StoredEvaluationSample,
    StoredOrderBasis,
)
from sapphire_flow.types.rating_reference import RatingReferenceBasis


@pytest.fixture
def accepted_sample() -> StoredEvaluationSample:
    digest = Sha256Digest(value="a" * 64)
    return StoredEvaluationSample(
        scope=EvaluationScope(
            tenant_id=TenantId(UUID(int=1)), station_id=StationId(UUID(int=2))
        ),
        stored_order=1,
        stored_order_basis=StoredOrderBasis.DATABASE_INSERTION_SEQUENCE,
        timestamp=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        value=1.0,
        rejection_code=None,
        source_revision=SourceRevision.CURRENT,
        qc_status=QcStatus.QC_PASSED,
        qc_rule_version=None,
        warnings=(),
        lineage=SafeDerivedLineage(
            derived_fingerprint=digest,
            observation_id=ObservationId(UUID(int=3)),
            source_snapshot_sha256=digest,
            rating_curve_id=RatingCurveId(UUID(int=4)),
            curve_version=1,
            file_sha256=digest,
            reference_basis=RatingReferenceBasis.VERIFIED,
            reference_id=UUID(int=5),
            level_unit="m",
            discharge_unit="m3/s",
            evidence=LegacyEvaluationEvidence(legacy_custody_sha256=digest),
        ),
    )


class TestStoredEvaluationSample:
    def test_registration_does_not_claim_original_insertion_order(
        self, accepted_sample: StoredEvaluationSample
    ) -> None:
        registered = replace(
            accepted_sample, stored_order_basis=StoredOrderBasis.MIGRATION_REGISTRATION
        )
        assert registered.selection_notes == ("legacy_insertion_order_unknown",)
        assert accepted_sample.selection_notes == ()

    def test_rejected_result_keeps_reason_without_number(
        self, accepted_sample: StoredEvaluationSample
    ) -> None:
        rejected = replace(accepted_sample, value=None, rejection_code="qc_failed")
        assert rejected.result is EvaluationSampleResult.REJECTED
        assert rejected.rejection_code == "qc_failed"

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
    def test_invalid_accepted_value_is_refused(
        self, accepted_sample: StoredEvaluationSample, value: float
    ) -> None:
        with pytest.raises(ValueError, match="finite and nonnegative"):
            replace(accepted_sample, value=value)

    def test_result_cannot_be_both_accepted_and_rejected(
        self, accepted_sample: StoredEvaluationSample
    ) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            replace(accepted_sample, rejection_code="qc_failed")

    def test_source_revision_does_not_change_stored_value(
        self, accepted_sample: StoredEvaluationSample
    ) -> None:
        superseded = replace(accepted_sample, source_revision=SourceRevision.SUPERSEDED)
        assert superseded.value == accepted_sample.value
        assert superseded.lineage == accepted_sample.lineage
