from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sapphire_flow.types.gateway_preparation import (
    ChunkEvidence,
    DataStream,
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


def valid_chunk() -> ChunkEvidence:
    return ChunkEvidence(
        scope_revision="sha256:" + "0" * 64,
        station_code="001.0",
        stream=DataStream.ERA5_HISTORY,
        variable="total_precipitation",
        start="2024-01-01T00:00:00+00:00",
        end="2024-01-04T00:00:00+00:00",
        member=None,
        cycle=None,
        fetched_at="2026-11-06T00:00:00+00:00",
        expected_count=3,
        usable_count=3,
        observed_count=3,
        missing_intervals=(),
        holds=(),
        response_sha256="sha256:" + "a" * 64,
        validated_source="synthetic_history",
        validated_source_run=SourceRunContract(semantics=SourceRunSemantics.NULL),
        validated_run_identities=(),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_count": -1},
        {"usable_count": 4},
        {"observed_count": 2},
        {"start": "bad"},
        {"end": "2024-01-01T00:00:00+00:00"},
        {"fetched_at": "2026-11-06"},
        {"response_sha256": "bad"},
        {"validated_source": None},
        {"validated_source_run": None},
        {"validated_run_identities": ("unexpected",)},
        {"usable_count": 2},
        {"member": 0},
    ],
)
def test_chunk_evidence_rejects_incoherent_values(changes: dict[str, object]) -> None:
    with pytest.raises(
        ValueError, match="evidence|counts|provenance|identities|Invalid isoformat"
    ):
        replace(valid_chunk(), **changes)


def test_failed_fetch_can_preserve_empty_unvalidated_evidence() -> None:
    evidence = valid_chunk()
    failed = replace(
        evidence,
        usable_count=0,
        observed_count=0,
        missing_intervals=((evidence.start, evidence.end),),
        holds=("missing_requested_support", "source_unavailable"),
        validated_source=None,
        validated_source_run=None,
    )
    assert failed.usable_count == 0 and failed.validated_source is None
