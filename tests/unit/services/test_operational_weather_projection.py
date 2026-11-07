from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.services.operational_weather_projection import (
    future_receipt_scope,
    project_future_weather,
    project_past_weather,
)
from sapphire_flow.services.operational_weather_proof import (
    WeatherProofError,
    prove_future_receipt,
)
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherRequest,
    FutureWeatherRequirement,
    FutureWeatherScope,
    PastWeatherRequirement,
    PastWeatherUse,
    WeatherAttemptState,
    WeatherBlocked,
    WeatherCacheAttempt,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherFailureReason,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherUnused,
    WeatherUseBlockReason,
)
from tests.fakes.fake_stores import FakeWeatherForecastStore
from tests.fakes.operational_weather import FINGERPRINT, ISSUE, make_operational_window
from tests.unit.services.test_recap_daily_weather import qualified_rows

LIMIT = 2 * 1024 * 1024


def successful_entry() -> WeatherCacheEntry:
    sid = StationId(uuid4())
    return WeatherCacheEntry(
        attempt=WeatherCacheAttempt(
            key=WeatherCacheKey(station_id=sid, binding_fingerprint=FINGERPRINT),
            generation=1,
            id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        ),
        state=WeatherAttemptState.COMPLETE,
        past_outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
        future_attempts=(),
        past_snapshot=make_past_snapshot(
            window=make_operational_window(sid),
            retrieved_at=ISSUE,
            snapshot_id=uuid4(),
            max_bytes=LIMIT,
        ),
        finished_at=ISSUE,
        completion_sha256="b" * 64,
    )


def requirement() -> PastWeatherRequirement:
    return PastWeatherRequirement(
        start=ensure_utc(ISSUE - timedelta(days=1)),
        end=ISSUE,
        parameters=("precipitation",),
    )


class TestProjectPastWeather:
    def test_unused_ignores_unknown_cache(self) -> None:
        attempt = successful_entry().attempt
        assert isinstance(
            project_past_weather(
                None, expected_attempt=attempt, requirement=None, max_past_bytes=LIMIT
            ),
            WeatherUnused,
        )

    def test_successful_past_is_current_despite_unrelated_future_failure(self) -> None:
        entry = successful_entry()
        scope = FutureWeatherScope(
            station_id=entry.attempt.key.station_id,
            binding_fingerprint="c" * 64,
            source="ifs",
            issue_time=ISSUE,
            requirements=(
                FutureWeatherRequirement(
                    parameter="precipitation", valid_times=(ISSUE,), member_ids=(0,)
                ),
            ),
        )
        entry = replace(
            entry,
            state=WeatherAttemptState.FAILED,
            future_attempts=(
                FutureWeatherAttempt(
                    scope=scope.request,
                    outcome=WeatherLegOutcome(
                        state=WeatherLegState.UNAVAILABLE,
                        reason=WeatherFailureReason.TRANSPORT,
                    ),
                ),
            ),
        )
        result = project_past_weather(
            entry,
            expected_attempt=entry.attempt,
            requirement=requirement(),
            max_past_bytes=LIMIT,
        )
        assert isinstance(result, PastWeatherUse)
        assert result.freshness is WeatherFreshness.CURRENT
        assert result.requirement.parameters == ("precipitation",)

    def test_recoverable_failure_uses_complete_last_good_as_stale(self) -> None:
        entry = replace(
            successful_entry(),
            state=WeatherAttemptState.FAILED,
            past_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
            ),
        )
        result = project_past_weather(
            entry,
            expected_attempt=entry.attempt,
            requirement=requirement(),
            max_past_bytes=LIMIT,
        )
        assert isinstance(result, PastWeatherUse)
        assert result.freshness is WeatherFreshness.STALE

    @pytest.mark.parametrize(
        "reason",
        [
            WeatherFailureReason.AUTHORIZATION,
            WeatherFailureReason.CONFIGURATION,
            WeatherFailureReason.INTEGRITY,
            WeatherFailureReason.STORAGE,
        ],
    )
    def test_fatal_reason_cannot_bypass_through_good_cache(
        self, reason: WeatherFailureReason
    ) -> None:
        entry = replace(
            successful_entry(),
            state=WeatherAttemptState.FAILED,
            past_outcome=WeatherLegOutcome(state=WeatherLegState.FATAL, reason=reason),
        )
        assert project_past_weather(
            entry,
            expected_attempt=entry.attempt,
            requirement=requirement(),
            max_past_bytes=LIMIT,
        ) == WeatherBlocked(reason=WeatherUseBlockReason.FATAL_REFRESH)

    def test_superseded_attempt_cannot_project_current_data(self) -> None:
        entry = successful_entry()
        other = replace(entry.attempt, generation=2, id=uuid4())
        assert project_past_weather(
            entry,
            expected_attempt=other,
            requirement=requirement(),
            max_past_bytes=LIMIT,
        ) == WeatherBlocked(reason=WeatherUseBlockReason.UNKNOWN_ATTEMPT)

    def test_next_day_requires_complete_rollover_buffer(self) -> None:
        entry = successful_entry()
        later = ensure_utc(ISSUE + timedelta(days=1))
        attempt = replace(
            entry.attempt, generation=2, id=uuid4(), issue_time=later, started_at=later
        )
        entry = replace(
            entry,
            attempt=attempt,
            finished_at=later,
            state=WeatherAttemptState.FAILED,
            past_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
            ),
        )
        req = PastWeatherRequirement(
            start=ISSUE, end=later, parameters=("temperature",)
        )
        assert project_past_weather(
            entry, expected_attempt=attempt, requirement=req, max_past_bytes=LIMIT
        ) == WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
        assert entry.past_snapshot is not None
        window = entry.past_snapshot.window
        buffer = make_operational_window(attempt.key.station_id, issue_time=later)
        buffered = replace(window, end=later, days=window.days + buffer.days)
        entry = replace(
            entry,
            past_snapshot=make_past_snapshot(
                window=buffered,
                retrieved_at=ISSUE,
                snapshot_id=uuid4(),
                max_bytes=LIMIT,
            ),
        )
        result = project_past_weather(
            entry, expected_attempt=attempt, requirement=req, max_past_bytes=LIMIT
        )
        assert isinstance(result, PastWeatherUse)
        assert result.freshness is WeatherFreshness.STALE
        assert result.snapshot_issue_time == ISSUE
        assert tuple(day.day for day in result.days) == (ISSUE,)


class TestProjectFutureWeather:
    @pytest.mark.parametrize("shift", [0, 1])
    def test_uses_current_proof_or_supported_retained_proof(self, shift: int) -> None:
        rows = qualified_rows()
        issue = ensure_utc(ISSUE + timedelta(days=shift))
        request = FutureWeatherRequest(
            station_id=rows[0].station_id,
            binding_fingerprint="c" * 64,
            source="ifs_ecmwf",
            issue_time=ISSUE,
            requirements=tuple(
                FutureWeatherRequirement(
                    parameter=parameter,
                    member_ids=(0,),
                    valid_times=(ISSUE, ensure_utc(ISSUE + timedelta(days=1))),
                )
                for parameter in ("precipitation", "temperature")
            ),
        )
        scope = future_receipt_scope(request, cycle_time=ISSUE)
        assert scope is not None
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            fetched=rows,
            readback=rows,
        )
        required = replace(
            request,
            issue_time=issue,
            requirements=tuple(
                replace(item, valid_times=item.valid_times[shift:])
                for item in request.requirements
            ),
        )
        outcome = (
            WeatherLegOutcome(state=WeatherLegState.SUCCESS)
            if shift == 0
            else WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE,
                reason=WeatherFailureReason.TRANSPORT,
            )
        )
        attempt = WeatherCacheAttempt(
            key=WeatherCacheKey(
                station_id=rows[0].station_id, binding_fingerprint=FINGERPRINT
            ),
            generation=1,
            id=uuid4(),
            issue_time=issue,
            started_at=issue,
        )
        entry = WeatherCacheEntry(
            attempt=attempt,
            state=WeatherAttemptState.COMPLETE
            if shift == 0
            else WeatherAttemptState.FAILED,
            past_outcome=WeatherLegOutcome(state=WeatherLegState.UNUSED),
            future_attempts=(
                FutureWeatherAttempt(
                    scope=required,
                    outcome=outcome,
                    resolved_cycle=ISSUE if shift == 0 else None,
                ),
            ),
            future_receipts=(receipt,),
            finished_at=issue,
            completion_sha256="b" * 64,
        )
        store = FakeWeatherForecastStore()
        store.store_weather_forecasts(rows)
        result = project_future_weather(
            entry,
            expected_attempt=attempt,
            requirement=required,
            store=store,
            earliest_cycle=ISSUE,
        )
        assert isinstance(result, tuple)
        assert result[0].freshness is (
            WeatherFreshness.CURRENT if shift == 0 else WeatherFreshness.STALE
        )
        assert len(result[1]) == 16 * (2 - shift)
        if shift:
            corrupted = FakeWeatherForecastStore()
            corrupted.store_weather_forecasts(
                [
                    replace(row, value=row.value + 1) if index == 0 else row
                    for index, row in enumerate(rows)
                ]
            )
            with pytest.raises(WeatherProofError, match="complete receipt"):
                project_future_weather(
                    entry,
                    expected_attempt=attempt,
                    requirement=required,
                    store=corrupted,
                    earliest_cycle=ISSUE,
                )
