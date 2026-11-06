from datetime import UTC, datetime

import pytest

from sapphire_flow.types.gateway_preparation import (
    HistoryScope,
    RegistrationAttempt,
    RegistrationStatus,
)


def test_whole_year_job_expansion_uses_exclusive_end() -> None:
    scope = HistoryScope(
        start=datetime(2023, 5, 1, tzinfo=UTC),
        end=datetime(2025, 1, 1, tzinfo=UTC),
        purpose="explicit training choice",
    )
    assert scope.job_years == (2023, 2024)


def test_reconciliation_requires_attributed_evidence() -> None:
    with pytest.raises(ValueError, match="attributed"):
        RegistrationAttempt(
            attempt_id="synthetic",
            recorded_at="2026-11-06",
            requested_hru="synthetic",
            upload_sha256="synthetic",
            status=RegistrationStatus.CONFIRMED,
        )


def test_history_scope_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="UTC"):
        HistoryScope(
            start=datetime(2024, 1, 1), end=datetime(2024, 1, 2), purpose="synthetic"
        )
