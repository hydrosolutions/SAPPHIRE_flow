from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.operational_weather import (
    WEATHER_PARAMETERS,
    FutureWeatherRequirement,
    OperationalWeatherPeriod,
    WeatherAttemptState,
    WeatherCacheKey,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherParameter,
    WeatherUnused,
    qualified_ifs_daily_support,
    require_daily_grid_boundary,
    require_utc,
)
from sapphire_flow.types.time_grid import TimeGrid  # noqa: TC001 - evidence schema
from sapphire_flow.types.weather_gap import WeatherGapMethod
from sapphire_flow.types.weather_gap_v2 import (
    WeatherGapFillV2,  # noqa: TC001 - evidence schema
)

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ForecastId, StationId

OPERATIONAL_WEATHER_POLICY_V2 = "recap-start-declared-daily-v2"


def require_context(
    grid: TimeGrid, issue_time: UtcDatetime, cutoff: UtcDatetime
) -> None:
    require_utc(issue_time)
    require_utc(cutoff)
    if grid.step != timedelta(days=1) or cutoff > issue_time:
        raise ValueError(
            "weather context requires a daily grid and cutoff at or before issue"
        )


def require_digest(value: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("weather identity requires canonical SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalWeatherWindowV2:
    station_id: StationId
    binding_fingerprint: str
    grid: TimeGrid
    issue_time: UtcDatetime
    eligibility_cutoff: UtcDatetime
    start: UtcDatetime
    end: UtcDatetime
    periods: tuple[OperationalWeatherPeriod, ...]
    policy_version: str = OPERATIONAL_WEATHER_POLICY_V2

    def __post_init__(self) -> None:
        require_context(self.grid, self.issue_time, self.eligibility_cutoff)
        require_digest(self.binding_fingerprint)
        for bound in (self.start, self.end):
            require_daily_grid_boundary(bound, self.grid)
        if self.policy_version != OPERATIONAL_WEATHER_POLICY_V2:
            raise ValueError("unsupported period weather policy")
        if self.start >= self.end or self.end > self.eligibility_cutoff:
            raise ValueError(
                "past weather bounds exceed eligibility cutoff or are empty"
            )
        expected = tuple(
            self.start + self.grid.step * i for i in range((self.end - self.start).days)
        )
        if tuple(p.start for p in self.periods) != expected or any(
            p.grid != self.grid for p in self.periods
        ):
            raise ValueError("weather periods must tile the declared grid/window")
        if any(
            row.source_run > self.eligibility_cutoff
            for p in self.periods
            for row in p.samples
        ):
            raise ValueError("weather source run follows eligibility cutoff")


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherRequestV2:
    station_id: StationId
    binding_fingerprint: str
    source: str
    grid: TimeGrid
    issue_time: UtcDatetime
    eligibility_cutoff: UtcDatetime
    first_period_start: UtcDatetime
    requirements: tuple[FutureWeatherRequirement, ...]
    policy_version: str = OPERATIONAL_WEATHER_POLICY_V2

    def __post_init__(self) -> None:
        require_context(self.grid, self.issue_time, self.eligibility_cutoff)
        require_daily_grid_boundary(self.first_period_start, self.grid)
        require_digest(self.binding_fingerprint)
        if (
            self.policy_version != OPERATIONAL_WEATHER_POLICY_V2
            or not self.source
            or not self.requirements
        ):
            raise ValueError(
                "future period request requires qualified policy/source/scope"
            )
        if len({r.parameter for r in self.requirements}) != len(self.requirements):
            raise ValueError("future period request repeats a parameter")
        for req in self.requirements:
            if req.parameter not in WEATHER_PARAMETERS or any(
                m is None for m in req.member_ids
            ):
                raise ValueError(
                    "future period request requires supported parameters "
                    "and explicit members"
                )
            if any(
                t != self.first_period_start + self.grid.step * i
                for i, t in enumerate(req.valid_times)
            ):
                raise ValueError(
                    "future period request requires contiguous explicit starts"
                )


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherScopeV2:
    request: FutureWeatherRequestV2
    requirements: tuple[FutureWeatherRequirement, ...]

    def __post_init__(self) -> None:
        expected = {r.parameter: r for r in self.request.requirements}
        if (
            len(self.requirements) != len(expected)
            or {r.parameter for r in self.requirements} != expected.keys()
        ):
            raise ValueError("native scope parameters differ from period request")
        for req in self.requirements:
            target = expected[req.parameter]
            if (
                req.member_ids != target.member_ids
                or tuple(sorted({self.request.grid.floor(t) for t in req.valid_times}))
                != target.valid_times
            ):
                raise ValueError("native scope differs from requested periods/members")

    @property
    def station_id(self) -> StationId:
        return self.request.station_id

    @property
    def binding_fingerprint(self) -> str:
        return self.request.binding_fingerprint

    @property
    def source(self) -> str:
        return self.request.source

    @property
    def issue_time(self) -> UtcDatetime:
        return self.request.issue_time


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherReceiptV2:
    scope: FutureWeatherScopeV2
    cycle_time: UtcDatetime
    validated_at: UtcDatetime
    row_sha256: str
    row_count: int

    def __post_init__(self) -> None:
        require_utc(self.cycle_time)
        require_utc(self.validated_at)
        if (
            self.cycle_time > self.scope.request.eligibility_cutoff
            or self.cycle_time > self.validated_at
        ):
            raise ValueError(
                "future receipt cycle follows eligibility cutoff/validation"
            )
        expected_count = sum(
            len(r.valid_times) * len(r.member_ids) for r in self.scope.requirements
        )
        if (
            type(self.row_count) is not int
            or self.row_count <= 0
            or self.row_count != expected_count
        ):
            raise ValueError("future receipt row count differs from native scope")
        require_digest(self.row_sha256)
        targets = {req.parameter: req for req in self.scope.request.requirements}
        for native in self.scope.requirements:
            target = targets[native.parameter]
            supports = tuple(
                qualified_ifs_daily_support(
                    self.cycle_time, start, grid=self.scope.request.grid
                )
                for start in target.valid_times
            )
            if (
                native.parameter != target.parameter
                or any(not support for support in supports)
                or native.valid_times
                != tuple(t for support in supports for t in support)
            ):
                raise ValueError("future receipt lacks complete native period support")


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherAttemptV2:
    scope: FutureWeatherRequestV2
    outcome: WeatherLegOutcome
    requested_cycle: UtcDatetime | None = None
    resolved_cycle: UtcDatetime | None = None

    def __post_init__(self) -> None:
        for cycle in (self.requested_cycle, self.resolved_cycle):
            if cycle is not None:
                require_utc(cycle)
                if cycle > self.scope.eligibility_cutoff:
                    raise ValueError("weather attempt cycle follows issue time")
        if (
            self.outcome.state is WeatherLegState.SUCCESS
            and self.resolved_cycle is None
        ):
            raise ValueError("successful future attempt requires a real cycle")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherPastSnapshotV2:
    id: UUID
    retrieved_at: UtcDatetime
    window: OperationalWeatherWindowV2
    payload_sha256: str

    def __post_init__(self) -> None:
        require_utc(self.retrieved_at)
        if len(self.payload_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.payload_sha256
        ):
            raise ValueError("past snapshot requires canonical payload SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherCacheAttemptV2:
    grid: TimeGrid
    eligibility_cutoff: UtcDatetime
    key: WeatherCacheKey
    generation: int
    id: UUID
    issue_time: UtcDatetime
    started_at: UtcDatetime

    def __post_init__(self) -> None:
        require_utc(self.issue_time)
        require_utc(self.started_at)
        require_context(self.grid, self.issue_time, self.eligibility_cutoff)
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("weather attempt generation must be positive")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherCacheCompletionV2:
    finished_at: UtcDatetime
    past_outcome: WeatherLegOutcome
    future_attempts: tuple[FutureWeatherAttemptV2, ...]
    past_snapshot: WeatherPastSnapshotV2 | None = None
    future_receipts: tuple[FutureWeatherReceiptV2, ...] = ()

    def __post_init__(self) -> None:
        require_utc(self.finished_at)
        if self.past_outcome.state is WeatherLegState.PENDING or any(
            attempt.outcome.state is WeatherLegState.PENDING
            for attempt in self.future_attempts
        ):
            raise ValueError("terminal weather completion cannot contain pending legs")
        if (self.past_outcome.state is WeatherLegState.SUCCESS) != (
            self.past_snapshot is not None
        ):
            raise ValueError(
                "successful past outcome requires exactly one paired snapshot"
            )
        scopes = tuple(attempt.scope for attempt in self.future_attempts)
        if len(set(scopes)) != len(scopes):
            raise ValueError("weather completion repeats a future scope")
        receipt_keys = tuple(
            (receipt.scope.request, receipt.cycle_time)
            for receipt in self.future_receipts
        )
        successful_keys = {
            (attempt.scope, attempt.resolved_cycle)
            for attempt in self.future_attempts
            if attempt.outcome.state is WeatherLegState.SUCCESS
        }
        if (
            len(set(receipt_keys)) != len(receipt_keys)
            or set(receipt_keys) != successful_keys
        ):
            raise ValueError("future receipts must match successful scopes exactly")
        for attempt in self.future_attempts:
            if attempt.outcome.state is WeatherLegState.SUCCESS and not any(
                receipt.scope.request == attempt.scope
                and receipt.cycle_time == attempt.resolved_cycle
                for receipt in self.future_receipts
            ):
                raise ValueError(
                    "successful future outcome requires a matching receipt"
                )

    @property
    def state(self) -> WeatherAttemptState:
        states = (self.past_outcome.state,) + tuple(
            attempt.outcome.state for attempt in self.future_attempts
        )
        if all(
            state in (WeatherLegState.SUCCESS, WeatherLegState.UNUSED)
            for state in states
        ):
            return WeatherAttemptState.COMPLETE
        return WeatherAttemptState.FAILED


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherCacheEntryV2:
    attempt: WeatherCacheAttemptV2
    state: WeatherAttemptState
    past_outcome: WeatherLegOutcome
    future_attempts: tuple[FutureWeatherAttemptV2, ...]
    past_snapshot: WeatherPastSnapshotV2 | None = None
    future_receipts: tuple[FutureWeatherReceiptV2, ...] = ()
    finished_at: UtcDatetime | None = None
    completion_sha256: str | None = None

    def __post_init__(self) -> None:
        if any(
            scope.grid != self.attempt.grid
            or scope.eligibility_cutoff != self.attempt.eligibility_cutoff
            for scope in (a.scope for a in self.future_attempts)
        ):
            raise ValueError("cache request temporal context differs from attempt")
        if (
            self.past_snapshot is not None
            and self.past_snapshot.window.grid != self.attempt.grid
        ):
            raise ValueError("cache snapshot uses a different grid")
        if any(r.scope.request.grid != self.attempt.grid for r in self.future_receipts):
            raise ValueError("cache receipt uses a different grid")
        if self.state is WeatherAttemptState.PENDING:
            if self.finished_at is not None or self.completion_sha256 is not None:
                raise ValueError("pending weather entry cannot claim completion")
            if self.past_outcome.state is not WeatherLegState.PENDING:
                raise ValueError("pending weather entry requires unknown past outcome")
        elif self.finished_at is None or self.completion_sha256 is None:
            raise ValueError("terminal weather entry requires completion identity")
        if self.finished_at is not None:
            require_utc(self.finished_at)
            if self.finished_at < self.attempt.started_at:
                raise ValueError("weather completion precedes attempt start")
        if self.past_snapshot is not None and (
            self.past_snapshot.window.station_id != self.attempt.key.station_id
            or self.past_snapshot.window.binding_fingerprint
            != self.attempt.key.binding_fingerprint
        ):
            raise ValueError("past snapshot does not match cache identity")
        if any(
            receipt.scope.station_id != self.attempt.key.station_id
            for receipt in self.future_receipts
        ):
            raise ValueError("future receipt does not match cache station")

        scopes = tuple(future.scope for future in self.future_attempts)
        if len(set(scopes)) != len(scopes) or any(
            scope.station_id != self.attempt.key.station_id
            or scope.issue_time != self.attempt.issue_time
            for scope in scopes
        ):
            raise ValueError("cache future scopes do not match attempt identity")
        receipt_keys = tuple(
            (receipt.scope.request, receipt.cycle_time)
            for receipt in self.future_receipts
        )
        if len(receipt_keys) > 32 or len(set(receipt_keys)) != len(receipt_keys):
            raise ValueError("cache receipts exceed bound or repeat a scope/cycle")
        if self.state is WeatherAttemptState.PENDING:
            if any(
                future.outcome.state is not WeatherLegState.PENDING
                for future in self.future_attempts
            ):
                raise ValueError("pending cache entry contains a terminal future leg")
        else:
            states = (self.past_outcome.state,) + tuple(
                future.outcome.state for future in self.future_attempts
            )
            if WeatherLegState.PENDING in states:
                raise ValueError("terminal cache entry contains a pending leg")
            complete = all(
                state in (WeatherLegState.SUCCESS, WeatherLegState.UNUSED)
                for state in states
            )
            if complete != (self.state is WeatherAttemptState.COMPLETE):
                raise ValueError("cache terminal state contradicts leg outcomes")
            if self.past_outcome.state is WeatherLegState.SUCCESS and (
                self.past_snapshot is None
                or self.past_snapshot.window.issue_time != self.attempt.issue_time
                or self.past_snapshot.window.eligibility_cutoff
                != self.attempt.eligibility_cutoff
            ):
                raise ValueError("current past success lacks its paired snapshot")
            if any(
                future.outcome.state is WeatherLegState.SUCCESS
                and (future.scope, future.resolved_cycle) not in receipt_keys
                for future in self.future_attempts
            ):
                raise ValueError("current future success lacks its receipt")
            if self.completion_sha256 is not None and (
                len(self.completion_sha256) != 64
                or any(
                    char not in "0123456789abcdef" for char in self.completion_sha256
                )
            ):
                raise ValueError("cache completion digest must be canonical SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class PastWeatherRequirementV2:
    grid: TimeGrid
    start: UtcDatetime
    end: UtcDatetime
    parameters: tuple[WeatherParameter, ...]

    def __post_init__(self) -> None:
        require_daily_grid_boundary(self.start, self.grid)
        require_daily_grid_boundary(self.end, self.grid)
        if self.start >= self.end or not self.parameters:
            raise ValueError("past weather requirement must be nonempty")
        if len(set(self.parameters)) != len(self.parameters) or any(
            parameter not in WEATHER_PARAMETERS for parameter in self.parameters
        ):
            raise ValueError("past weather requirement has invalid parameters")


@dataclass(frozen=True, kw_only=True, slots=True)
class PastWeatherUseV2:
    freshness: WeatherFreshness
    refresh_outcome: WeatherLegOutcome
    retrieved_at: UtcDatetime
    requirement: PastWeatherRequirementV2
    snapshot_id: UUID
    snapshot_sha256: str
    snapshot_issue_time: UtcDatetime
    binding_fingerprint: str
    periods: tuple[OperationalWeatherPeriod, ...]
    snapshot_cutoff: UtcDatetime
    gap_fills: tuple[WeatherGapFillV2, ...] = ()

    def __post_init__(self) -> None:
        require_utc(self.snapshot_issue_time)
        require_utc(self.retrieved_at)
        if self.refresh_outcome.state not in (
            WeatherLegState.SUCCESS,
            WeatherLegState.UNAVAILABLE,
        ) or (self.freshness is WeatherFreshness.CURRENT) != (
            self.refresh_outcome.state is WeatherLegState.SUCCESS
        ):
            raise ValueError("past weather freshness disagrees with refresh outcome")
        require_utc(self.snapshot_cutoff)
        if self.snapshot_cutoff > self.snapshot_issue_time:
            raise ValueError("snapshot cutoff follows its issue identity")
        if any(
            row.source_run > self.snapshot_cutoff
            for period in self.periods
            for row in period.samples
        ):
            raise ValueError("past weather source run follows snapshot cutoff")
        expected_days = int((self.requirement.end - self.requirement.start).days)
        if len(self.periods) != expected_days or any(
            day.grid != self.requirement.grid
            or day.start != self.requirement.start + timedelta(days=index)
            for index, day in enumerate(self.periods)
        ):
            raise ValueError("past weather use must cover exact consumed days")
        for digest in (self.snapshot_sha256, self.binding_fingerprint):
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError(
                    "past weather use requires canonical SHA256 identities"
                )
        by_day = {day.start: day for day in self.periods}
        filled: set[tuple[str, UtcDatetime]] = set()
        for fill in self.gap_fills:
            key = (fill.parameter, fill.start)
            if (
                fill.grid != self.requirement.grid
                or key in filled
                or fill.start not in by_day
                or fill.parameter not in self.requirement.parameters
            ):
                raise ValueError("past weather fill scope mismatch or duplicate")
            filled.add(key)
            day = by_day[fill.start]
            original = (
                day.precipitation_mm
                if fill.parameter == "precipitation"
                else day.temperature_c
            )
            if original is not None:
                raise ValueError("weather fill cannot replace complete source values")
            if fill.parameter == "precipitation" and fill.value < math.fsum(
                row.value for row in day.samples if row.parameter == "precipitation"
            ):
                raise ValueError("weather fill is below observed partial rainfall")
            if fill.method is WeatherGapMethod.LINEAR:
                for endpoint in fill.endpoints:
                    support = by_day.get(endpoint.start)
                    value = (
                        None
                        if support is None
                        else support.precipitation_mm
                        if fill.parameter == "precipitation"
                        else support.temperature_c
                    )
                    if value != endpoint.value:
                        raise ValueError(
                            "weather fill endpoint lacks original daily support"
                        )


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherUseV2:
    freshness: WeatherFreshness
    refresh_attempt: FutureWeatherAttemptV2
    requirement: FutureWeatherRequestV2
    receipt: FutureWeatherReceiptV2
    consumed_record_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        scope = self.receipt.scope
        refresh = self.refresh_attempt
        if refresh.scope != self.requirement or refresh.outcome.state not in (
            WeatherLegState.SUCCESS,
            WeatherLegState.UNAVAILABLE,
        ):
            raise ValueError("future weather refresh identity mismatch")
        current = (
            refresh.outcome.state is WeatherLegState.SUCCESS
            and refresh.resolved_cycle == self.receipt.cycle_time
        )
        if (self.freshness is WeatherFreshness.CURRENT) != current:
            raise ValueError("future weather freshness disagrees with refresh outcome")
        if (
            scope.station_id != self.requirement.station_id
            or scope.binding_fingerprint != self.requirement.binding_fingerprint
            or scope.source != self.requirement.source
            or self.receipt.cycle_time > self.requirement.eligibility_cutoff
            or scope.request.grid != self.requirement.grid
            or scope.request.eligibility_cutoff > self.requirement.eligibility_cutoff
            or scope.issue_time > self.requirement.issue_time
        ):
            raise ValueError("future weather use identity mismatch")
        if not self.consumed_record_ids or len(set(self.consumed_record_ids)) != len(
            self.consumed_record_ids
        ):
            raise ValueError(
                "future weather use requires unique archive record identities"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherUseV2:
    grid: TimeGrid
    eligibility_cutoff: UtcDatetime
    station_id: StationId
    issue_time: UtcDatetime
    used_at: UtcDatetime
    attempt: WeatherCacheAttemptV2 | None
    refresh_finished_at: UtcDatetime | None
    past: PastWeatherUseV2 | WeatherUnused
    future: FutureWeatherUseV2 | WeatherUnused
    policy_version: str = OPERATIONAL_WEATHER_POLICY_V2

    def __post_init__(self) -> None:
        require_utc(self.issue_time)
        require_context(self.grid, self.issue_time, self.eligibility_cutoff)
        if self.policy_version != OPERATIONAL_WEATHER_POLICY_V2:
            raise ValueError("unsupported as-used weather policy")
        require_utc(self.used_at)
        if (
            self.refresh_finished_at is not None
            and self.used_at < self.refresh_finished_at
        ):
            raise ValueError("weather use precedes refresh completion")
        if (
            isinstance(self.past, PastWeatherUseV2)
            and self.used_at < self.past.retrieved_at
        ):
            raise ValueError("weather use precedes past retrieval")
        if (
            isinstance(self.future, FutureWeatherUseV2)
            and self.used_at < self.future.receipt.validated_at
        ):
            raise ValueError("weather use precedes future verification")
        if self.refresh_finished_at is not None:
            require_utc(self.refresh_finished_at)
        if self.attempt is not None and (
            self.refresh_finished_at is None
            or self.refresh_finished_at < self.attempt.started_at
        ):
            raise ValueError("as-used weather requires terminal refresh time")
        if self.attempt is None:
            if self.refresh_finished_at is not None:
                raise ValueError("unused weather cannot invent refresh completion")
            if not isinstance(self.past, WeatherUnused) or not isinstance(
                self.future, WeatherUnused
            ):
                raise ValueError("consumed weather requires a refresh attempt")
        elif (
            self.attempt.grid != self.grid
            or self.attempt.eligibility_cutoff != self.eligibility_cutoff
            or self.attempt.key.station_id != self.station_id
            or self.attempt.issue_time != self.issue_time
        ):
            raise ValueError("as-used weather attempt identity mismatch")
        if isinstance(self.past, PastWeatherUseV2) and (
            self.past.requirement.end > self.eligibility_cutoff
            or self.past.requirement.grid != self.grid
            or self.past.snapshot_issue_time > self.issue_time
            or self.past.snapshot_cutoff > self.eligibility_cutoff
            or self.attempt is None
            or self.past.binding_fingerprint != self.attempt.key.binding_fingerprint
        ):
            raise ValueError("as-used past weather identity mismatch")
        if isinstance(self.past, PastWeatherUseV2) and any(
            row.station_id != self.station_id
            or row.cycle_time > self.eligibility_cutoff
            for fill in self.past.gap_fills
            for row in fill.donor_records
        ):
            raise ValueError("as-used weather donor identity mismatch")
        if isinstance(self.future, FutureWeatherUseV2) and (
            self.future.requirement.station_id != self.station_id
            or self.future.requirement.issue_time != self.issue_time
            or self.future.requirement.grid != self.grid
            or self.future.requirement.eligibility_cutoff != self.eligibility_cutoff
        ):
            raise ValueError("as-used future weather identity mismatch")

        if (
            isinstance(self.past, PastWeatherUseV2)
            and isinstance(self.future, FutureWeatherUseV2)
            and self.past.requirement.end != self.future.requirement.first_period_start
        ):
            raise ValueError("past and future weather periods are not contiguous")
        if isinstance(
            self.future, FutureWeatherUseV2
        ) and self.future.receipt.cycle_time > min(
            self.eligibility_cutoff, self.used_at
        ):
            raise ValueError("used weather cycle follows cutoff/use time")


@dataclass(frozen=True, kw_only=True, slots=True)
class ContributingWeatherUseV2:
    forecast_id: ForecastId
    evidence_sha256: str | None
    weather_use: WeatherUseV2

    def __post_init__(self) -> None:
        digest = self.evidence_sha256
        if digest is None:
            raise ValueError("selected contributor requires evidence hash")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("contributor evidence hash must be SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class DerivedWeatherUseV2:
    grid: TimeGrid
    station_id: StationId
    issue_time: UtcDatetime
    contributors: tuple[ContributingWeatherUseV2, ...]

    def __post_init__(self) -> None:
        require_utc(self.issue_time)
        if not self.contributors:
            raise ValueError("derived weather requires a selected contributor")
        if len({item.forecast_id for item in self.contributors}) != len(
            self.contributors
        ):
            raise ValueError("derived weather contributors must be unique")
        if any(
            (
                item.weather_use.station_id != self.station_id
                or item.weather_use.issue_time != self.issue_time
                or item.weather_use.grid != self.grid
            )
            for item in self.contributors
        ):
            raise ValueError("derived weather contributor scope mismatch")
