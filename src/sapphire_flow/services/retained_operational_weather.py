from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.services.operational_weather_projection import future_receipt_scope
from sapphire_flow.services.weather_gap_fill import fill_daily_weather
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.operational_weather import (
    FutureWeatherRequest,
    OperationalWeatherDay,
    OperationalWeatherSample,
    OperationalWeatherSource,
    PastWeatherRequirement,
    WeatherPastSnapshot,
    period_weather_value,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.types.weather_gap import WeatherGapFill


class SnapshotSupport(IntEnum):
    UNUSABLE = 0
    FILLED = 1
    NATIVE = 2


@dataclass(frozen=True, kw_only=True, slots=True)
class RetainedPastRequirement:
    past: PastWeatherRequirement
    fill_parameters: frozenset[str]
    max_gap_days: int


def past_sample_lead(sample: OperationalWeatherSample) -> timedelta:
    return sample.valid_time + sample.sample_step() - sample.source_run


def past_leads_allowed(
    days: tuple[OperationalWeatherDay, ...], *, max_lead: timedelta
) -> bool:
    return all(
        past_sample_lead(sample) <= max_lead
        for day in days
        for sample in day.samples
        if sample.source is OperationalWeatherSource.IFS
    )


def retained_future_samples(
    snapshot: WeatherPastSnapshot,
    request: FutureWeatherRequest,
    *,
    cycle: UtcDatetime,
) -> tuple[OperationalWeatherSample, ...] | None:
    if snapshot.window.station_id != request.station_id or cycle > request.issue_time:
        return None
    if request.source != "ifs_ecmwf" or any(
        req.member_ids != (0,) for req in request.requirements
    ):
        return None
    scope = future_receipt_scope(request, cycle_time=cycle)
    if scope is None:
        return None
    wanted = {
        (req.parameter, stamp)
        for req in scope.requirements
        for stamp in req.valid_times
    }
    samples = tuple(
        sample
        for day in snapshot.window.days
        for sample in day.samples
        if sample.source is OperationalWeatherSource.IFS
        and sample.source_run == cycle
        and (sample.parameter, sample.valid_time) in wanted
    )
    if {(sample.parameter, sample.valid_time) for sample in samples} != wanted:
        return None
    return samples


def retained_tail_cycle(
    snapshot: WeatherPastSnapshot,
    requests: tuple[FutureWeatherRequest, ...],
) -> UtcDatetime | None:
    cycles = {
        sample.source_run
        for day in snapshot.window.days
        for sample in day.samples
        if sample.source is OperationalWeatherSource.IFS
    }
    return next(
        (
            cycle
            for cycle in sorted(cycles, reverse=True)
            if requests
            and all(
                retained_future_samples(snapshot, request, cycle=cycle) is not None
                for request in requests
            )
        ),
        None,
    )


def snapshot_support(
    snapshot: WeatherPastSnapshot,
    *,
    binding_fingerprint: str,
    requirements: tuple[RetainedPastRequirement, ...],
    future_requests: tuple[FutureWeatherRequest, ...],
    issue_time: UtcDatetime,
    max_past_lead: timedelta,
    donors: Mapping[tuple[str, UtcDatetime], WeatherGapFill],
) -> SnapshotSupport:
    window = snapshot.window
    if (
        window.binding_fingerprint != binding_fingerprint
        or window.issue_time > issue_time
    ):
        return SnapshotSupport.UNUSABLE
    cycle = retained_tail_cycle(snapshot, future_requests)
    if cycle is None:
        return SnapshotSupport.UNUSABLE
    by_day = {day.day: day for day in window.days}
    rollover = by_day.get(issue_time)
    if rollover is None or any(
        period_weather_value(rollover, parameter) is None
        for requirement in requirements
        for parameter in requirement.past.parameters
    ):
        return SnapshotSupport.UNUSABLE
    support = SnapshotSupport.NATIVE
    for policy in requirements:
        past = policy.past
        days = tuple(
            by_day.get(
                stamp,
                OperationalWeatherDay(
                    day=stamp, precipitation_mm=None, temperature_c=None, samples=()
                ),
            )
            for stamp in (
                ensure_utc(past.start + timedelta(days=index))
                for index in range((past.end - past.start).days)
            )
        )
        # Policy exclusions are a refusal, never an interpolatable missing value.
        if not past_leads_allowed(days, max_lead=max_past_lead):
            return SnapshotSupport.UNUSABLE
        frame = pl.DataFrame(
            [
                {
                    "timestamp": day.day,
                    **{
                        name: period_weather_value(day, name)
                        for name in past.parameters
                    },
                }
                for day in days
            ],
            schema={
                "timestamp": pl.Datetime("us", "UTC"),
                **{name: pl.Float64 for name in past.parameters},
            },
        )
        if all(frame.get_column(name).null_count() == 0 for name in past.parameters):
            continue
        filled = fill_daily_weather(
            frame,
            issue_time=issue_time,
            lookbacks={name: len(days) for name in policy.fill_parameters},
            max_gap_days=policy.max_gap_days,
            donors=donors,
            precipitation_lower_bounds={
                day.day: sum(
                    sample.value
                    for sample in day.samples
                    if sample.parameter == "precipitation"
                )
                for day in days
            },
        )
        if any(filled.frame.get_column(name).null_count() for name in past.parameters):
            return SnapshotSupport.UNUSABLE
        support = SnapshotSupport.FILLED
    return support
