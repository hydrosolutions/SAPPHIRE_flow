from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast, overload

from sapphire_flow.services.operational_weather_proof import verify_future_receipt
from sapphire_flow.services.recap_daily_weather import recap_daily_support
from sapphire_flow.store.operational_weather_codec import verify_past_snapshot
from sapphire_flow.types.operational_weather import (
    FutureWeatherRequest,
    FutureWeatherRequirement,
    FutureWeatherScope,
    FutureWeatherUse,
    OperationalWeatherDay,
    OperationalWeatherPeriod,
    PastWeatherUse,
    WeatherAttemptState,
    WeatherBlocked,
    WeatherCacheAttempt,
    WeatherCacheEntry,
    WeatherCoverage,
    WeatherFreshness,
    WeatherLegState,
    WeatherUnused,
    WeatherUseBlockReason,
)
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherRequestV2,
    FutureWeatherScopeV2,
    FutureWeatherUseV2,
    PastWeatherRequirementV2,
    PastWeatherUseV2,
    WeatherCacheAttemptV2,
    WeatherCacheEntryV2,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import WeatherForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.operational_weather import (
        PastWeatherRequirement,
    )
    from sapphire_flow.types.weather import WeatherForecastRecord


@overload
def project_past_weather(
    entry: WeatherCacheEntry | None,
    *,
    expected_attempt: WeatherCacheAttempt,
    requirement: PastWeatherRequirement | None,
    max_past_bytes: int,
    coverage: WeatherCoverage = WeatherCoverage.COMPLETE,
) -> PastWeatherUse | WeatherBlocked | WeatherUnused: ...


@overload
def project_past_weather(
    entry: WeatherCacheEntryV2 | None,
    *,
    expected_attempt: WeatherCacheAttemptV2,
    requirement: PastWeatherRequirementV2 | None,
    max_past_bytes: int,
    coverage: WeatherCoverage = WeatherCoverage.COMPLETE,
) -> PastWeatherUseV2 | WeatherBlocked | WeatherUnused: ...


@overload
def project_past_weather(
    entry: WeatherCacheEntry | WeatherCacheEntryV2 | None,
    *,
    expected_attempt: WeatherCacheAttempt | WeatherCacheAttemptV2,
    requirement: PastWeatherRequirement | PastWeatherRequirementV2 | None,
    max_past_bytes: int,
    coverage: WeatherCoverage = WeatherCoverage.COMPLETE,
) -> PastWeatherUse | PastWeatherUseV2 | WeatherBlocked | WeatherUnused: ...


def project_past_weather(
    entry: WeatherCacheEntry | WeatherCacheEntryV2 | None,
    *,
    expected_attempt: WeatherCacheAttempt | WeatherCacheAttemptV2,
    requirement: PastWeatherRequirement | PastWeatherRequirementV2 | None,
    max_past_bytes: int,
    coverage: WeatherCoverage = WeatherCoverage.COMPLETE,
) -> PastWeatherUse | PastWeatherUseV2 | WeatherBlocked | WeatherUnused:
    if requirement is None:
        return WeatherUnused()
    if isinstance(requirement, PastWeatherRequirementV2):
        if (
            not isinstance(expected_attempt, WeatherCacheAttemptV2)
            or requirement.grid != expected_attempt.grid
            or requirement.end > expected_attempt.eligibility_cutoff
        ):
            raise ValueError("past period requirement differs from attempt context")
    elif (
        not isinstance(expected_attempt, WeatherCacheAttempt)
        or requirement.end != expected_attempt.issue_time
    ):
        raise ValueError("past weather requirement must end at the issue boundary")
    if (
        entry is None
        or entry.attempt != expected_attempt
        or entry.state is WeatherAttemptState.PENDING
    ):
        return WeatherBlocked(reason=WeatherUseBlockReason.UNKNOWN_ATTEMPT)
    state = entry.past_outcome.state
    if state in (WeatherLegState.FATAL, WeatherLegState.NOT_ATTEMPTED):
        return WeatherBlocked(reason=WeatherUseBlockReason.FATAL_REFRESH)
    if state not in (WeatherLegState.SUCCESS, WeatherLegState.UNAVAILABLE):
        return WeatherBlocked(reason=WeatherUseBlockReason.UNKNOWN_ATTEMPT)
    snapshot = entry.past_snapshot
    if snapshot is None:
        return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
    verify_past_snapshot(snapshot, max_bytes=max_past_bytes)
    if coverage is WeatherCoverage.COMPLETE and (
        snapshot.window.start > requirement.start
        or snapshot.window.end < requirement.end
    ):
        return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
    if isinstance(requirement, PastWeatherRequirementV2):
        if not isinstance(entry, WeatherCacheEntryV2):
            raise ValueError("past weather versions differ")
        period_snapshot = entry.past_snapshot
        assert period_snapshot is not None
        if period_snapshot.window.grid != requirement.grid:
            raise ValueError("past snapshot grid differs from requested periods")
        if (
            period_snapshot.window.issue_time > expected_attempt.issue_time
            or period_snapshot.window.eligibility_cutoff
            > entry.attempt.eligibility_cutoff
        ):
            return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
        available_periods = {p.start: p for p in period_snapshot.window.periods}
        days: tuple[OperationalWeatherDay | OperationalWeatherPeriod, ...] = tuple(
            available_periods.get(
                requirement.start + timedelta(days=i),
                OperationalWeatherPeriod(
                    grid=requirement.grid,
                    start=requirement.start + timedelta(days=i),
                    precipitation_mm=None,
                    temperature_c=None,
                    samples=(),
                ),
            )
            for i in range((requirement.end - requirement.start).days)
        )
    else:
        if not isinstance(entry, WeatherCacheEntry):
            raise ValueError("past weather versions differ")
        old_snapshot = entry.past_snapshot
        assert old_snapshot is not None
        available = {day.day: day for day in old_snapshot.window.days}
        days = tuple(
            available.get(
                requirement.start + timedelta(days=i),
                OperationalWeatherDay(
                    day=requirement.start + timedelta(days=i),
                    precipitation_mm=None,
                    temperature_c=None,
                    samples=(),
                ),
            )
            for i in range((requirement.end - requirement.start).days)
        )
    if not any(day.samples for day in days):
        return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
    if coverage is WeatherCoverage.COMPLETE and any(
        (day.precipitation_mm if parameter == "precipitation" else day.temperature_c)
        is None
        for day in days
        for parameter in requirement.parameters
    ):
        return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
    if isinstance(requirement, PastWeatherRequirementV2):
        assert (
            isinstance(entry, WeatherCacheEntryV2) and entry.past_snapshot is not None
        )
        return PastWeatherUseV2(
            refresh_outcome=entry.past_outcome,
            retrieved_at=snapshot.retrieved_at,
            freshness=WeatherFreshness.CURRENT
            if state is WeatherLegState.SUCCESS
            else WeatherFreshness.STALE,
            requirement=requirement,
            snapshot_id=snapshot.id,
            snapshot_sha256=snapshot.payload_sha256,
            snapshot_issue_time=snapshot.window.issue_time,
            snapshot_cutoff=entry.past_snapshot.window.eligibility_cutoff,
            binding_fingerprint=snapshot.window.binding_fingerprint,
            periods=cast("tuple[OperationalWeatherPeriod, ...]", days),
        )
    return PastWeatherUse(
        refresh_outcome=entry.past_outcome,
        retrieved_at=snapshot.retrieved_at,
        freshness=WeatherFreshness.CURRENT
        if state is WeatherLegState.SUCCESS
        else WeatherFreshness.STALE,
        requirement=requirement,
        snapshot_id=snapshot.id,
        snapshot_sha256=snapshot.payload_sha256,
        snapshot_issue_time=snapshot.window.issue_time,
        binding_fingerprint=snapshot.window.binding_fingerprint,
        days=cast("tuple[OperationalWeatherDay, ...]", days),
    )


@overload
def future_receipt_scope(
    request: FutureWeatherRequest, *, cycle_time: UtcDatetime
) -> FutureWeatherScope | None: ...


@overload
def future_receipt_scope(
    request: FutureWeatherRequestV2, *, cycle_time: UtcDatetime
) -> FutureWeatherScopeV2 | None: ...


@overload
def future_receipt_scope(
    request: FutureWeatherRequest | FutureWeatherRequestV2, *, cycle_time: UtcDatetime
) -> FutureWeatherScope | FutureWeatherScopeV2 | None: ...


def future_receipt_scope(
    request: FutureWeatherRequest | FutureWeatherRequestV2,
    *,
    cycle_time: UtcDatetime,
) -> FutureWeatherScope | FutureWeatherScopeV2 | None:
    requirements: list[FutureWeatherRequirement] = []
    for requirement in request.requirements:
        days = tuple(
            recap_daily_support(cycle_time, day, grid=request.grid)
            if isinstance(request, FutureWeatherRequestV2)
            else recap_daily_support(cycle_time, day)
            for day in requirement.valid_times
        )
        if any(not day for day in days):
            return None
        requirements.append(
            FutureWeatherRequirement(
                parameter=requirement.parameter,
                member_ids=requirement.member_ids,
                valid_times=tuple(stamp for day in days for stamp in day),
            )
        )
    if isinstance(request, FutureWeatherRequestV2):
        return FutureWeatherScopeV2(request=request, requirements=tuple(requirements))
    return FutureWeatherScope(
        station_id=request.station_id,
        binding_fingerprint=request.binding_fingerprint,
        source=request.source,
        issue_time=request.issue_time,
        requirements=tuple(requirements),
    )


@overload
def project_future_weather(
    entry: WeatherCacheEntry | None,
    *,
    expected_attempt: WeatherCacheAttempt,
    requirement: FutureWeatherRequest | None,
    store: WeatherForecastStore,
    earliest_cycle: UtcDatetime,
) -> (
    tuple[FutureWeatherUse, tuple[WeatherForecastRecord, ...]]
    | WeatherBlocked
    | WeatherUnused
): ...


@overload
def project_future_weather(
    entry: WeatherCacheEntryV2 | None,
    *,
    expected_attempt: WeatherCacheAttemptV2,
    requirement: FutureWeatherRequestV2 | None,
    store: WeatherForecastStore,
    earliest_cycle: UtcDatetime,
) -> (
    tuple[FutureWeatherUseV2, tuple[WeatherForecastRecord, ...]]
    | WeatherBlocked
    | WeatherUnused
): ...


@overload
def project_future_weather(
    entry: WeatherCacheEntry | WeatherCacheEntryV2 | None,
    *,
    expected_attempt: WeatherCacheAttempt | WeatherCacheAttemptV2,
    requirement: FutureWeatherRequest | FutureWeatherRequestV2 | None,
    store: WeatherForecastStore,
    earliest_cycle: UtcDatetime,
) -> (
    tuple[FutureWeatherUse | FutureWeatherUseV2, tuple[WeatherForecastRecord, ...]]
    | WeatherBlocked
    | WeatherUnused
): ...


def project_future_weather(
    entry: WeatherCacheEntry | WeatherCacheEntryV2 | None,
    *,
    expected_attempt: WeatherCacheAttempt | WeatherCacheAttemptV2,
    requirement: FutureWeatherRequest | FutureWeatherRequestV2 | None,
    store: WeatherForecastStore,
    earliest_cycle: UtcDatetime,
) -> (
    tuple[FutureWeatherUse | FutureWeatherUseV2, tuple[WeatherForecastRecord, ...]]
    | WeatherBlocked
    | WeatherUnused
):
    if requirement is None:
        return WeatherUnused()
    if isinstance(requirement, FutureWeatherRequestV2):
        if (
            not isinstance(expected_attempt, WeatherCacheAttemptV2)
            or requirement.grid != expected_attempt.grid
            or requirement.eligibility_cutoff != expected_attempt.eligibility_cutoff
        ):
            raise ValueError("future period request differs from attempt context")
    elif not isinstance(expected_attempt, WeatherCacheAttempt):
        raise ValueError("future weather versions differ")
    if requirement.issue_time != expected_attempt.issue_time:
        raise ValueError("future weather request issue mismatch")
    if (
        entry is None
        or entry.attempt != expected_attempt
        or entry.state is WeatherAttemptState.PENDING
    ):
        return WeatherBlocked(reason=WeatherUseBlockReason.UNKNOWN_ATTEMPT)
    outcome = next(
        (item for item in entry.future_attempts if item.scope == requirement), None
    )
    if outcome is None:
        return WeatherBlocked(reason=WeatherUseBlockReason.UNKNOWN_ATTEMPT)
    if outcome.outcome.state in (WeatherLegState.FATAL, WeatherLegState.NOT_ATTEMPTED):
        return WeatherBlocked(reason=WeatherUseBlockReason.FATAL_REFRESH)
    if outcome.outcome.state not in (
        WeatherLegState.SUCCESS,
        WeatherLegState.UNAVAILABLE,
    ):
        return WeatherBlocked(reason=WeatherUseBlockReason.UNKNOWN_ATTEMPT)
    for receipt in sorted(
        entry.future_receipts,
        key=lambda item: (
            item.cycle_time == outcome.resolved_cycle
            and item.scope.request == requirement,
            item.cycle_time,
            item.validated_at,
        ),
        reverse=True,
    ):
        if (
            receipt.scope.station_id != requirement.station_id
            or receipt.scope.binding_fingerprint != requirement.binding_fingerprint
            or receipt.scope.source != requirement.source
            or not earliest_cycle <= receipt.cycle_time <= requirement.issue_time
            or receipt.scope.issue_time > requirement.issue_time
        ):
            continue
        if isinstance(requirement, FutureWeatherRequestV2):
            if (
                not isinstance(receipt.scope, FutureWeatherScopeV2)
                or receipt.scope.request.grid != requirement.grid
                or receipt.scope.request.eligibility_cutoff
                > requirement.eligibility_cutoff
                or receipt.cycle_time > requirement.eligibility_cutoff
            ):
                continue
        elif not isinstance(receipt.scope, FutureWeatherScope):
            raise ValueError("future receipt versions differ")
        scope = future_receipt_scope(requirement, cycle_time=receipt.cycle_time)
        if scope is None:
            continue
        wanted = {
            (item.parameter, stamp, member)
            for item in scope.requirements
            for stamp in item.valid_times
            for member in item.member_ids
        }
        covered = {
            (item.parameter, stamp, member)
            for item in receipt.scope.requirements
            for stamp in item.valid_times
            for member in item.member_ids
        }
        if not wanted <= covered:
            continue
        records = store.fetch_weather_forecasts(
            station_id=requirement.station_id,
            nwp_source=requirement.source,
            cycle_time=receipt.cycle_time,
            parameters=sorted({item.parameter for item in receipt.scope.requirements}),
        )
        verify_future_receipt(receipt, records)
        consumed = tuple(
            row
            for row in records
            if (row.parameter, row.valid_time, row.member_id) in wanted
        )
        current = (
            outcome.outcome.state is WeatherLegState.SUCCESS
            and outcome.resolved_cycle == receipt.cycle_time
        )
        if isinstance(requirement, FutureWeatherRequestV2):
            from sapphire_flow.types.operational_weather_v2 import (
                FutureWeatherAttemptV2,
                FutureWeatherReceiptV2,
            )

            if not isinstance(outcome, FutureWeatherAttemptV2) or not isinstance(
                receipt, FutureWeatherReceiptV2
            ):
                raise ValueError("future receipt versions differ")
            return FutureWeatherUseV2(
                refresh_attempt=outcome,
                freshness=WeatherFreshness.CURRENT
                if current
                else WeatherFreshness.STALE,
                requirement=requirement,
                receipt=receipt,
                consumed_record_ids=tuple(row.id for row in consumed),
            ), consumed
        from sapphire_flow.types.operational_weather import (
            FutureWeatherAttempt,
            FutureWeatherReceipt,
        )

        if not isinstance(outcome, FutureWeatherAttempt) or not isinstance(
            receipt, FutureWeatherReceipt
        ):
            raise ValueError("future receipt versions differ")
        return FutureWeatherUse(
            refresh_attempt=outcome,
            freshness=WeatherFreshness.CURRENT if current else WeatherFreshness.STALE,
            requirement=requirement,
            receipt=receipt,
            consumed_record_ids=tuple(row.id for row in consumed),
        ), consumed
    return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)


def weather_use_periods(
    past: PastWeatherUse | PastWeatherUseV2,
) -> tuple[OperationalWeatherPeriod, ...]:
    """Read-only common view; v1 identities and hashed payloads stay unchanged."""
    if isinstance(past, PastWeatherUseV2):
        return past.periods
    from sapphire_flow.types.operational_weather import UTC_DAILY_WEATHER_GRID

    return tuple(
        OperationalWeatherPeriod(
            grid=UTC_DAILY_WEATHER_GRID,
            start=day.day,
            precipitation_mm=day.precipitation_mm,
            temperature_c=day.temperature_c,
            samples=day.samples,
        )
        for day in past.days
    )
