from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sapphire_flow.types.gateway_preparation import (
    ForecastLeadBand,
    HistoryScope,
    RegistrationAttempt,
    RegistrationStatus,
    SourceRunContract,
    SourceRunSemantics,
)
from tests.unit.services.test_gateway_preparation import proposal


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


def test_declared_piecewise_schedule_has_single_join_and_half_open_end() -> None:
    bands = (
        ForecastLeadBand(
            start=timedelta(0), end=timedelta(hours=144), cadence=timedelta(hours=3)
        ),
        ForecastLeadBand(
            start=timedelta(hours=144),
            end=timedelta(hours=240),
            cadence=timedelta(hours=6),
        ),
    )
    contract = replace(proposal().contracts[1], forecast_lead_bands=bands)
    leads = contract.forecast_leads(timedelta(0), timedelta(hours=240))
    assert leads == tuple(
        timedelta(hours=h) for h in (*range(0, 144, 3), *range(144, 240, 6))
    )
    assert len(leads) == 64
    assert contract.forecast_leads(timedelta(hours=141), timedelta(hours=150)) == (
        timedelta(hours=141),
        timedelta(hours=144),
    )
    with pytest.raises(ValueError, match="boundary"):
        contract.forecast_leads(timedelta(hours=145), timedelta(hours=240))
    with pytest.raises(ValueError, match="exceeds"):
        contract.forecast_leads(timedelta(0), timedelta(hours=246))


@pytest.mark.parametrize("start", [138, 150])
def test_declared_lead_bands_reject_overlap_or_gap(start: int) -> None:
    bands = (
        ForecastLeadBand(
            start=timedelta(0), end=timedelta(hours=144), cadence=timedelta(hours=3)
        ),
        ForecastLeadBand(
            start=timedelta(hours=start),
            end=timedelta(hours=240),
            cadence=timedelta(hours=6),
        ),
    )
    with pytest.raises(ValueError, match="contiguous"):
        replace(proposal().contracts[1], forecast_lead_bands=bands)


def test_lead_band_rejects_unaligned_end() -> None:
    with pytest.raises(ValueError, match="align"):
        ForecastLeadBand(
            start=timedelta(0), end=timedelta(hours=5), cadence=timedelta(hours=3)
        )


def test_fixed_run_identity_requires_aware_datetime_and_normalizes_utc() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        SourceRunContract(
            semantics=SourceRunSemantics.UTC_INSTANT, timestamp=datetime(2024, 1, 1)
        )
    run = SourceRunContract(
        semantics=SourceRunSemantics.UTC_INSTANT,
        timestamp=datetime(2024, 1, 1, 3, tzinfo=timezone(timedelta(hours=3))),
    )
    assert run.timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert run.timestamp.tzinfo == UTC
    forecast = proposal().forecast
    assert forecast is not None
    with pytest.raises(ValueError, match="match its cycle"):
        replace(forecast, source_runs=((forecast.cycles[0].isoformat(), run),))
