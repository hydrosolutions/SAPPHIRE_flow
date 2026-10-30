from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from sapphire_flow.store.bafu_unit_documents import (
    CountsDocument,
    ReceiptDocument,
    document_digest,
)
from sapphire_flow.types.bafu_capture_batch import BafuCaptureBatchId
from sapphire_flow.types.bafu_write_result import (
    BafuApplicability,
    BafuJournalQuota,
    BafuOutcomeCounts,
    BafuWriteResult,
    BafuWriteStatus,
)


class TestReceiptDocument:
    def test_rejects_counts_not_matching_declared_inputs(self) -> None:
        with pytest.raises(ValidationError, match="counts differ"):
            ReceiptDocument(
                input_digest="a" * 64,
                expected_captures=2,
                counts=CountsDocument(
                    inserted=1,
                    restated=0,
                    evidence_only=0,
                    unchanged=0,
                    quarantined=0,
                    rejected=0,
                ),
                event_ids=[1, 2],
                observations=[],
            )

    def test_rejects_missing_event_references(self) -> None:
        with pytest.raises(ValidationError, match="event references differ"):
            ReceiptDocument(
                input_digest="a" * 64,
                expected_captures=1,
                counts=CountsDocument(
                    inserted=1,
                    restated=0,
                    evidence_only=0,
                    unchanged=0,
                    quarantined=0,
                    rejected=0,
                ),
                event_ids=[],
                observations=[],
            )

    def test_rejects_coerced_count(self) -> None:
        with pytest.raises(ValidationError, match="valid integer"):
            CountsDocument.model_validate(
                dict(
                    inserted="1",
                    restated=0,
                    evidence_only=0,
                    unchanged=0,
                    quarantined=0,
                    rejected=0,
                )
            )

    def test_unknown_document_fields_fail_closed(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ReceiptDocument.model_validate(
                dict(
                    input_digest="a" * 64,
                    expected_captures=1,
                    counts=dict(
                        inserted=1,
                        restated=0,
                        evidence_only=0,
                        unchanged=0,
                        quarantined=0,
                        rejected=0,
                    ),
                    event_ids=[1],
                    observations=[],
                    surprise=True,
                )
            )


class TestDocumentDigest:
    def test_key_order_is_not_identity(self) -> None:
        assert document_digest({"b": 2, "a": 1}) == document_digest({"a": 1, "b": 2})

    def test_nonfinite_value_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Out of range float"):
            document_digest({"value": float("nan")})


class TestBafuJournalQuota:
    def test_nonpositive_quota_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="quotas must be positive"):
            BafuJournalQuota(
                max_station_events=0,
                max_station_receipts=1,
                max_station_evidence_bytes=1,
                max_batch_bytes=1,
            )


class TestBafuOutcomeCounts:
    def test_negative_count_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="nonnegative"):
            BafuOutcomeCounts(unchanged=-1)


class TestBafuWriteResult:
    def test_admission_cannot_claim_committed_without_receipt(self) -> None:
        with pytest.raises(ValueError, match="receipt digest"):
            BafuWriteResult(
                batch_id=BafuCaptureBatchId(uuid4()),
                status=BafuWriteStatus.COMMITTED,
                applicability=BafuApplicability.CURRENT,
            )

    def test_unknown_cannot_carry_success_counts(self) -> None:
        with pytest.raises(ValueError, match="outcome counts"):
            BafuWriteResult(
                batch_id=BafuCaptureBatchId(uuid4()),
                status=BafuWriteStatus.UNKNOWN,
                applicability=BafuApplicability.UNKNOWN,
                counts=BafuOutcomeCounts(),
            )


class TestCanonicalBoundaries:
    @pytest.mark.parametrize(
        "number,expanded", [(1e22, 10**22), (1e23, 10**23), (-0.0, 0), (4.2, 4.20)]
    )
    def test_json_number_representations_hash_equally(
        self, number: float, expanded: float | int
    ) -> None:
        assert document_digest({"n": number}) == document_digest({"n": expanded})
        assert document_digest({"n": number}) != document_digest({"n": str(number)})

    def test_large_integers_are_not_rounded(self) -> None:
        value = 1234567890123456789012345678901234567890
        assert document_digest({"event_ids": [value]}) != document_digest(
            {"event_ids": [value + 1]}
        )
        assert document_digest({"counts": value}) != document_digest(
            {"counts": value + 1}
        )

    def test_observation_state_rejects_naive_timestamp(self) -> None:
        from sapphire_flow.store.bafu_unit_documents import ObservationStateDocument

        with pytest.raises(ValidationError, match="Naive datetime"):
            ObservationStateDocument.model_validate(
                dict(
                    id=uuid4(),
                    station_id=uuid4(),
                    timestamp="2026-10-21T00:00:00",
                    parameter="water_level",
                    source="measured",
                    value=4.2,
                    rating_curve_id=None,
                    rating_curve_correction_version=None,
                    delivery_id=None,
                    qc_status="raw",
                    qc_flags=None,
                    qc_rule_version=None,
                )
            )
