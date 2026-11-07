from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from sapphire_flow.exceptions import WeatherEvidenceCaptureError
from sapphire_flow.services.operational_weather_projection import future_receipt_scope
from sapphire_flow.services.operational_weather_proof import prove_future_receipt
from sapphire_flow.services.selected_weather_evidence import (
    SelectedWeatherIssuanceRefusal,
    selected_weather_issuance_refusal,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherRequest,
    FutureWeatherRequirement,
    FutureWeatherUse,
    WeatherCacheAttempt,
    WeatherCacheKey,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherUnused,
    WeatherUse,
)
from tests.fakes.operational_weather import FINGERPRINT, ISSUE
from tests.unit.services.test_recap_daily_weather import qualified_rows


def source_evidence() -> StationSourceEvidence:
    rows = qualified_rows()
    request = FutureWeatherRequest(
        station_id=rows[0].station_id,
        binding_fingerprint=FINGERPRINT,
        source="ifs_ecmwf",
        issue_time=ISSUE,
        requirements=(
            FutureWeatherRequirement(
                parameter="precipitation", valid_times=(ISSUE,), member_ids=(0,)
            ),
        ),
    )
    scope = future_receipt_scope(request, cycle_time=ISSUE)
    assert scope is not None
    receipt = prove_future_receipt(
        scope=scope, cycle_time=ISSUE, validated_at=ISSUE, fetched=rows, readback=rows
    )
    attempt = WeatherCacheAttempt(
        key=WeatherCacheKey(
            station_id=request.station_id, binding_fingerprint=FINGERPRINT
        ),
        generation=1,
        id=uuid4(),
        issue_time=ISSUE,
        started_at=ISSUE,
    )
    return StationSourceEvidence(
        weather_use=WeatherUse(
            station_id=request.station_id,
            issue_time=ISSUE,
            used_at=ISSUE,
            attempt=attempt,
            refresh_finished_at=ISSUE,
            past=WeatherUnused(),
            future=FutureWeatherUse(
                freshness=WeatherFreshness.CURRENT,
                refresh_attempt=FutureWeatherAttempt(
                    scope=request,
                    outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
                    resolved_cycle=ISSUE,
                ),
                requirement=request,
                receipt=receipt,
                consumed_record_ids=(rows[0].id,),
            ),
        )
    )


class TestSelectedWeatherIssuanceRefusal:
    @pytest.mark.parametrize(
        "offset, expected",
        [
            (timedelta(hours=36) - timedelta(microseconds=1), None),
            (timedelta(hours=36), None),
            (
                timedelta(hours=36, microseconds=1),
                SelectedWeatherIssuanceRefusal.WEATHER_EXPIRED,
            ),
            (
                -timedelta(microseconds=1),
                SelectedWeatherIssuanceRefusal.WEATHER_EXPIRED,
            ),
        ],
    )
    def test_inclusive_actual_age_without_rounding_or_evidence_rewrite(
        self,
        offset: timedelta,
        expected: SelectedWeatherIssuanceRefusal | None,
    ) -> None:
        source = source_evidence()
        before = source.weather_use
        assert (
            selected_weather_issuance_refusal(
                source,
                issue_time=ISSUE,
                at=ensure_utc(ISSUE + offset),
                max_age_hours=36,
            )
            is expected
        )
        assert source.weather_use is before

    @pytest.mark.parametrize(
        "at, expected",
        [
            (
                ensure_utc(
                    ISSUE + timedelta(hours=18, minutes=15) - timedelta(microseconds=1)
                ),
                None,
            ),
            (
                ensure_utc(ISSUE + timedelta(hours=18, minutes=15)),
                SelectedWeatherIssuanceRefusal.ISSUE_DAY_MISMATCH,
            ),
        ],
    )
    def test_nepal_midnight_is_exact(
        self,
        at: UtcDatetime,
        expected: SelectedWeatherIssuanceRefusal | None,
    ) -> None:
        source = source_evidence()
        assert (
            selected_weather_issuance_refusal(
                source,
                issue_time=ISSUE,
                at=at,
                max_age_hours=36,
                automatic_issue_timezone=ZoneInfo("Asia/Kathmandu"),
            )
            is expected
        )
        assert (
            selected_weather_issuance_refusal(
                source,
                issue_time=ISSUE,
                at=at,
                max_age_hours=36,
            )
            is None
        )

    def test_no_weather_model_is_not_falsely_expired(self) -> None:
        source = source_evidence()
        assert source.weather_use is not None
        unused = replace(
            source,
            weather_use=replace(
                source.weather_use,
                attempt=None,
                refresh_finished_at=None,
                future=WeatherUnused(),
            ),
        )
        assert (
            selected_weather_issuance_refusal(
                unused,
                issue_time=ISSUE,
                at=ensure_utc(ISSUE + timedelta(days=3)),
                max_age_hours=36,
            )
            is None
        )

    def test_missing_evidence_stays_an_integrity_failure(self) -> None:
        with pytest.raises(WeatherEvidenceCaptureError, match="assembly facts missing"):
            selected_weather_issuance_refusal(
                None, issue_time=ISSUE, at=ISSUE, max_age_hours=36
            )

    def test_frozen_claim_issue_cannot_be_replaced_by_flow_issue(self) -> None:
        with pytest.raises(WeatherEvidenceCaptureError, match="issue mismatch"):
            selected_weather_issuance_refusal(
                source_evidence(),
                issue_time=ensure_utc(ISSUE + timedelta(days=1)),
                at=ISSUE,
                max_age_hours=36,
            )
