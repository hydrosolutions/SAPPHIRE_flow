from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.services.operational_weather_proof import verify_future_receipt
from sapphire_flow.services.recap_daily_weather import recap_daily_support
from sapphire_flow.store.operational_weather_codec import verify_past_snapshot
from sapphire_flow.types.operational_weather import (
    FutureWeatherRequest,
    FutureWeatherRequirement,
    FutureWeatherScope,
    FutureWeatherUse,
    PastWeatherUse,
    WeatherAttemptState,
    WeatherBlocked,
    WeatherFreshness,
    WeatherLegState,
    WeatherUnused,
    WeatherUseBlockReason,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import WeatherForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.operational_weather import (
        PastWeatherRequirement,
        WeatherCacheAttempt,
        WeatherCacheEntry,
    )
    from sapphire_flow.types.weather import WeatherForecastRecord


def project_past_weather(
    entry: WeatherCacheEntry | None,
    *,
    expected_attempt: WeatherCacheAttempt,
    requirement: PastWeatherRequirement | None,
    max_past_bytes: int,
) -> PastWeatherUse | WeatherBlocked | WeatherUnused:
    if requirement is None:
        return WeatherUnused()
    if requirement.end != expected_attempt.issue_time:
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
    if (
        snapshot.window.start > requirement.start
        or snapshot.window.end < requirement.end
    ):
        return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
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
        days=tuple(
            day
            for day in snapshot.window.days
            if requirement.start <= day.day < requirement.end
        ),
    )


def future_receipt_scope(
    request: FutureWeatherRequest,
    *,
    cycle_time: UtcDatetime,
) -> FutureWeatherScope | None:
    requirements: list[FutureWeatherRequirement] = []
    for requirement in request.requirements:
        days = tuple(
            recap_daily_support(cycle_time, day) for day in requirement.valid_times
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
    return FutureWeatherScope(
        station_id=request.station_id,
        binding_fingerprint=request.binding_fingerprint,
        source=request.source,
        issue_time=request.issue_time,
        requirements=tuple(requirements),
    )


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
):
    if requirement is None:
        return WeatherUnused()
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
        return FutureWeatherUse(
            refresh_attempt=outcome,
            freshness=WeatherFreshness.CURRENT if current else WeatherFreshness.STALE,
            requirement=requirement,
            receipt=receipt,
            consumed_record_ids=tuple(row.id for row in consumed),
        ), consumed
    return WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)
