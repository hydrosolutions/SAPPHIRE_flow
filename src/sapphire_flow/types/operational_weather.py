from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ForecastId, StationId

OPERATIONAL_WEATHER_POLICY = "recap-start-utc-daily-v1"
QUALIFIED_SOURCE_REVISION = "ceff18d099b277d6e237c2cb81fe2068a85d06e5"


class OperationalWeatherCoverageError(ValueError):
    """Required source support is incomplete, without inventing missing samples."""


class OperationalWeatherSource(StrEnum):
    ERA5_LAND = "era5_land"
    IFS = "ifs"


class WeatherProductIdentity(StrEnum):
    REANALYSIS = "deterministic_reanalysis"
    CONTROL = "deterministic_control"


class WeatherIdentityOrigin(StrEnum):
    QUALIFIED_ENDPOINT_CONTRACT = "qualified_endpoint_contract"


WeatherParameter = Literal["precipitation", "temperature"]


def require_utc(value: UtcDatetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("weather timestamps must be timezone-aware UTC")


def require_midnight(value: UtcDatetime) -> None:
    require_utc(value)
    if any((value.hour, value.minute, value.second, value.microsecond)):
        raise ValueError("operational daily weather requires UTC midnight")


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalWeatherSample:
    parameter: WeatherParameter
    source: OperationalWeatherSource
    valid_time: UtcDatetime
    source_run: UtcDatetime
    value: float
    product: WeatherProductIdentity
    interval_end: UtcDatetime | None = None
    identity_origin: WeatherIdentityOrigin = (
        WeatherIdentityOrigin.QUALIFIED_ENDPOINT_CONTRACT
    )
    contract_revision: str = QUALIFIED_SOURCE_REVISION

    def __post_init__(self) -> None:
        require_utc(self.valid_time)
        require_utc(self.source_run)
        if self.parameter not in ("precipitation", "temperature"):
            raise ValueError("unsupported operational weather parameter")
        if not isinstance(self.source, OperationalWeatherSource):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("unsupported operational weather source")
        if not math.isfinite(self.value):
            raise ValueError("weather sample must be finite")
        if self.parameter == "precipitation" and self.value < 0:
            raise ValueError("precipitation must be nonnegative")
        expected_product = (
            WeatherProductIdentity.CONTROL
            if self.source is OperationalWeatherSource.IFS
            else WeatherProductIdentity.REANALYSIS
        )
        if self.product is not expected_product:
            raise ValueError("weather product conflicts with qualified source")
        if (
            self.identity_origin
            is not WeatherIdentityOrigin.QUALIFIED_ENDPOINT_CONTRACT
            or self.contract_revision != QUALIFIED_SOURCE_REVISION
        ):
            raise ValueError("unsupported weather identity contract")
        step = self.sample_step()
        if self.parameter == "precipitation":
            if self.interval_end is None:
                raise ValueError("precipitation requires a START-labelled interval")
            require_utc(self.interval_end)
            if self.interval_end != self.valid_time + step:
                raise ValueError(
                    "precipitation interval conflicts with source schedule"
                )
        elif self.interval_end is not None:
            raise ValueError("temperature is a snapshot, not an interval")

    def sample_step(self) -> timedelta:
        if any(
            (
                self.valid_time.minute,
                self.valid_time.second,
                self.valid_time.microsecond,
            )
        ):
            raise ValueError("weather sample is not aligned to source grid")
        if self.source is OperationalWeatherSource.ERA5_LAND:
            require_midnight(self.source_run)
            if self.valid_time.date() != self.source_run.date():
                raise ValueError("ERA5 source run must identify its UTC day")
            return timedelta(hours=1)
        if self.source_run.hour % 6 or any(
            (
                self.source_run.minute,
                self.source_run.second,
                self.source_run.microsecond,
            )
        ):
            raise ValueError("IFS source run must use a real six-hour cycle")
        lead = (self.valid_time - self.source_run).total_seconds() / 3600
        step = 3 if lead < 144 else 6
        max_lead = 360 if self.source_run.hour == 0 else 72
        if lead < 0 or lead > max_lead or lead % step:
            raise ValueError("unsupported IFS lead grid")
        if self.parameter == "precipitation" and lead + step > max_lead:
            raise ValueError("precipitation interval exceeds qualified IFS horizon")
        return timedelta(hours=step)


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalWeatherDay:
    day: UtcDatetime
    precipitation_mm: float
    temperature_c: float
    samples: tuple[OperationalWeatherSample, ...]

    def __post_init__(self) -> None:
        require_midnight(self.day)
        if not self.samples:
            raise ValueError("daily weather requires contributing samples")
        end = self.day + timedelta(days=1)
        for parameter in ("precipitation", "temperature"):
            rows = sorted(
                (row for row in self.samples if row.parameter == parameter),
                key=lambda row: row.valid_time,
            )
            cursor = self.day
            for row in rows:
                if row.valid_time > cursor:
                    raise OperationalWeatherCoverageError("daily weather has a gap")
                if row.valid_time < cursor:
                    raise ValueError("daily weather has an overlap or duplicate")
                cursor = row.valid_time + row.sample_step()
            if cursor < end:
                raise OperationalWeatherCoverageError(
                    "daily weather does not cover the complete UTC grid"
                )
            if cursor > end:
                raise ValueError("daily weather sample crosses a UTC day boundary")
            reduced = math.fsum(row.value for row in rows)
            if parameter == "temperature":
                reduced /= len(rows)
            actual = (
                self.precipitation_mm
                if parameter == "precipitation"
                else self.temperature_c
            )
            if not math.isfinite(actual) or actual != reduced:
                raise ValueError(
                    "daily weather value differs from contributing samples"
                )


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalWeatherWindow:
    station_id: StationId
    binding_fingerprint: str
    issue_time: UtcDatetime
    start: UtcDatetime
    end: UtcDatetime
    days: tuple[OperationalWeatherDay, ...]
    policy_version: str = OPERATIONAL_WEATHER_POLICY

    def __post_init__(self) -> None:
        for bound in (self.issue_time, self.start, self.end):
            require_midnight(bound)
        if self.policy_version != OPERATIONAL_WEATHER_POLICY:
            raise ValueError("unsupported operational weather policy")
        if len(self.binding_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.binding_fingerprint
        ):
            raise ValueError("binding fingerprint must be a canonical SHA256")
        if not self.days or self.start >= self.end:
            raise ValueError("operational weather window must be nonempty")
        expected = tuple(
            self.start + timedelta(days=offset)
            for offset in range((self.end - self.start).days)
        )
        if tuple(day.day for day in self.days) != expected:
            raise ValueError("operational weather window must be contiguous")
        if any(
            sample.source_run > self.issue_time
            for day in self.days
            for sample in day.samples
        ):
            raise ValueError("weather source run is after forecast issue")


class WeatherAttemptState(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class WeatherLegState(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    FATAL = "fatal"
    NOT_ATTEMPTED = "not_attempted"
    UNUSED = "unused"


class WeatherFailureReason(StrEnum):
    TRANSPORT = "transport"
    MISSING_SUPPORT = "missing_support"
    NO_ELIGIBLE_CYCLE = "no_eligible_cycle"
    AUTHORIZATION = "authorization"
    CONFIGURATION = "configuration"
    INTEGRITY = "integrity"
    STORAGE = "storage"
    PARENT_FAILURE = "parent_failure"
    RECEIPT_CAPACITY = "receipt_capacity"


class WeatherFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherLegOutcome:
    state: WeatherLegState
    reason: WeatherFailureReason | None = None

    def __post_init__(self) -> None:
        failed = self.state in (
            WeatherLegState.UNAVAILABLE,
            WeatherLegState.FATAL,
            WeatherLegState.NOT_ATTEMPTED,
        )
        if failed != (self.reason is not None):
            raise ValueError("weather failure reason must match leg outcome")
        if self.state is WeatherLegState.UNAVAILABLE and self.reason not in (
            WeatherFailureReason.TRANSPORT,
            WeatherFailureReason.MISSING_SUPPORT,
            WeatherFailureReason.NO_ELIGIBLE_CYCLE,
            WeatherFailureReason.RECEIPT_CAPACITY,
        ):
            raise ValueError("fatal weather reason cannot authorize stale fallback")


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherRequirement:
    parameter: str
    valid_times: tuple[UtcDatetime, ...]
    member_ids: tuple[int | None, ...]

    def __post_init__(self) -> None:
        if not self.parameter or not self.valid_times or not self.member_ids:
            raise ValueError("future weather requirement must be nonempty")
        for value in self.valid_times:
            require_utc(value)
        if tuple(sorted(set(self.valid_times))) != self.valid_times:
            raise ValueError("future weather times must be unique and ordered")
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("future weather members must be unique")
        if any(
            member is not None and (type(member) is not int or member < 0)
            for member in self.member_ids
        ):
            raise ValueError("future weather member identifier is invalid")


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherScope:
    station_id: StationId
    binding_fingerprint: str
    source: str
    issue_time: UtcDatetime
    requirements: tuple[FutureWeatherRequirement, ...]
    policy_version: str = OPERATIONAL_WEATHER_POLICY

    def __post_init__(self) -> None:
        require_midnight(self.issue_time)
        if not self.source or not self.requirements:
            raise ValueError(
                "future weather scope must identify source and requirements"
            )
        if self.policy_version != OPERATIONAL_WEATHER_POLICY:
            raise ValueError("future weather scope has unsupported policy")
        if len(self.binding_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.binding_fingerprint
        ):
            raise ValueError("future binding fingerprint must be canonical SHA256")
        parameters = tuple(requirement.parameter for requirement in self.requirements)
        if len(set(parameters)) != len(parameters):
            raise ValueError("future weather parameters must be unique")
        if any(
            value < self.issue_time
            for requirement in self.requirements
            for value in requirement.valid_times
        ):
            raise ValueError("future weather requirement precedes issue time")

    @property
    def request(self) -> FutureWeatherRequest:
        return FutureWeatherRequest(
            station_id=self.station_id,
            binding_fingerprint=self.binding_fingerprint,
            source=self.source,
            issue_time=self.issue_time,
            policy_version=self.policy_version,
            requirements=tuple(
                FutureWeatherRequirement(
                    parameter=requirement.parameter,
                    member_ids=requirement.member_ids,
                    valid_times=tuple(
                        sorted(
                            {
                                ensure_utc(
                                    stamp.replace(
                                        hour=0, minute=0, second=0, microsecond=0
                                    )
                                )
                                for stamp in requirement.valid_times
                            }
                        )
                    ),
                )
                for requirement in self.requirements
            ),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherRequest:
    station_id: StationId
    binding_fingerprint: str
    source: str
    issue_time: UtcDatetime
    requirements: tuple[FutureWeatherRequirement, ...]
    policy_version: str = OPERATIONAL_WEATHER_POLICY

    def __post_init__(self) -> None:
        require_midnight(self.issue_time)
        if not self.source or not self.requirements:
            raise ValueError("future weather request must be nonempty")
        if self.policy_version != OPERATIONAL_WEATHER_POLICY:
            raise ValueError("future weather request has unsupported policy")
        if len(self.binding_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.binding_fingerprint
        ):
            raise ValueError("future request fingerprint must be canonical SHA256")
        if len({requirement.parameter for requirement in self.requirements}) != len(
            self.requirements
        ):
            raise ValueError("future weather request repeats a parameter")
        for requirement in self.requirements:
            if any(
                stamp != self.issue_time + timedelta(days=index)
                for index, stamp in enumerate(requirement.valid_times)
            ):
                raise ValueError(
                    "future weather request requires contiguous day-zero support"
                )


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherReceipt:
    scope: FutureWeatherScope
    cycle_time: UtcDatetime
    validated_at: UtcDatetime
    row_sha256: str
    row_count: int

    def __post_init__(self) -> None:
        require_utc(self.cycle_time)
        require_utc(self.validated_at)
        if self.cycle_time > self.scope.issue_time:
            raise ValueError("future weather receipt cycle follows issue time")
        if type(self.row_count) is not int or self.row_count <= 0:
            raise ValueError("future weather receipt requires positive row count")
        expected_count = sum(
            len(requirement.valid_times) * len(requirement.member_ids)
            for requirement in self.scope.requirements
        )
        if self.row_count != expected_count:
            raise ValueError("future receipt row count does not match required scope")
        if len(self.row_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.row_sha256
        ):
            raise ValueError("future weather receipt requires canonical row SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherAttempt:
    scope: FutureWeatherRequest
    outcome: WeatherLegOutcome
    requested_cycle: UtcDatetime | None = None
    resolved_cycle: UtcDatetime | None = None

    def __post_init__(self) -> None:
        for cycle in (self.requested_cycle, self.resolved_cycle):
            if cycle is not None:
                require_utc(cycle)
                if cycle > self.scope.issue_time:
                    raise ValueError("weather attempt cycle follows issue time")
        if (
            self.outcome.state is WeatherLegState.SUCCESS
            and self.resolved_cycle is None
        ):
            raise ValueError("successful future attempt requires a real cycle")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherCacheKey:
    station_id: StationId
    binding_fingerprint: str

    def __post_init__(self) -> None:
        if len(self.binding_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.binding_fingerprint
        ):
            raise ValueError("cache key requires canonical binding SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherPastSnapshot:
    id: UUID
    retrieved_at: UtcDatetime
    window: OperationalWeatherWindow
    payload_sha256: str

    def __post_init__(self) -> None:
        require_utc(self.retrieved_at)
        if len(self.payload_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.payload_sha256
        ):
            raise ValueError("past snapshot requires canonical payload SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherCacheAttempt:
    key: WeatherCacheKey
    generation: int
    id: UUID
    issue_time: UtcDatetime
    started_at: UtcDatetime

    def __post_init__(self) -> None:
        require_midnight(self.issue_time)
        require_utc(self.started_at)
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("weather attempt generation must be positive")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherCacheCompletion:
    finished_at: UtcDatetime
    past_outcome: WeatherLegOutcome
    future_attempts: tuple[FutureWeatherAttempt, ...]
    past_snapshot: WeatherPastSnapshot | None = None
    future_receipts: tuple[FutureWeatherReceipt, ...] = ()

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
class WeatherCacheEntry:
    attempt: WeatherCacheAttempt
    state: WeatherAttemptState
    past_outcome: WeatherLegOutcome
    future_attempts: tuple[FutureWeatherAttempt, ...]
    past_snapshot: WeatherPastSnapshot | None = None
    future_receipts: tuple[FutureWeatherReceipt, ...] = ()
    finished_at: UtcDatetime | None = None
    completion_sha256: str | None = None

    def __post_init__(self) -> None:
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


class WeatherUseBlockReason(StrEnum):
    UNKNOWN_ATTEMPT = "unknown_attempt"
    FATAL_REFRESH = "fatal_refresh"
    MISSING_SUPPORT = "missing_support"


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherUnused:
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherBlocked:
    reason: WeatherUseBlockReason


@dataclass(frozen=True, kw_only=True, slots=True)
class PastWeatherRequirement:
    start: UtcDatetime
    end: UtcDatetime
    parameters: tuple[WeatherParameter, ...]

    def __post_init__(self) -> None:
        require_midnight(self.start)
        require_midnight(self.end)
        if self.start >= self.end or not self.parameters:
            raise ValueError("past weather requirement must be nonempty")
        if len(set(self.parameters)) != len(self.parameters) or any(
            parameter not in ("precipitation", "temperature")
            for parameter in self.parameters
        ):
            raise ValueError("past weather requirement has invalid parameters")


@dataclass(frozen=True, kw_only=True, slots=True)
class PastWeatherUse:
    freshness: WeatherFreshness
    refresh_outcome: WeatherLegOutcome
    retrieved_at: UtcDatetime
    requirement: PastWeatherRequirement
    snapshot_id: UUID
    snapshot_sha256: str
    snapshot_issue_time: UtcDatetime
    binding_fingerprint: str
    days: tuple[OperationalWeatherDay, ...]

    def __post_init__(self) -> None:
        require_midnight(self.snapshot_issue_time)
        require_utc(self.retrieved_at)
        if self.refresh_outcome.state not in (
            WeatherLegState.SUCCESS,
            WeatherLegState.UNAVAILABLE,
        ) or (self.freshness is WeatherFreshness.CURRENT) != (
            self.refresh_outcome.state is WeatherLegState.SUCCESS
        ):
            raise ValueError("past weather freshness disagrees with refresh outcome")
        if self.snapshot_issue_time > self.requirement.end:
            raise ValueError("past weather snapshot follows issue time")
        expected_days = int((self.requirement.end - self.requirement.start).days)
        if len(self.days) != expected_days or any(
            day.day != self.requirement.start + timedelta(days=index)
            for index, day in enumerate(self.days)
        ):
            raise ValueError("past weather use must cover exact consumed days")
        for digest in (self.snapshot_sha256, self.binding_fingerprint):
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError(
                    "past weather use requires canonical SHA256 identities"
                )


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureWeatherUse:
    freshness: WeatherFreshness
    refresh_attempt: FutureWeatherAttempt
    requirement: FutureWeatherRequest
    receipt: FutureWeatherReceipt
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
            or self.receipt.cycle_time > self.requirement.issue_time
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
class WeatherUse:
    station_id: StationId
    issue_time: UtcDatetime
    used_at: UtcDatetime
    attempt: WeatherCacheAttempt | None
    refresh_finished_at: UtcDatetime | None
    past: PastWeatherUse | WeatherUnused
    future: FutureWeatherUse | WeatherUnused
    policy_version: str = OPERATIONAL_WEATHER_POLICY

    def __post_init__(self) -> None:
        require_midnight(self.issue_time)
        if self.policy_version != OPERATIONAL_WEATHER_POLICY:
            raise ValueError("unsupported as-used weather policy")
        require_utc(self.used_at)
        if (
            self.refresh_finished_at is not None
            and self.used_at < self.refresh_finished_at
        ):
            raise ValueError("weather use precedes refresh completion")
        if (
            isinstance(self.past, PastWeatherUse)
            and self.used_at < self.past.retrieved_at
        ):
            raise ValueError("weather use precedes past retrieval")
        if (
            isinstance(self.future, FutureWeatherUse)
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
            self.attempt.key.station_id != self.station_id
            or self.attempt.issue_time != self.issue_time
        ):
            raise ValueError("as-used weather attempt identity mismatch")
        if isinstance(self.past, PastWeatherUse) and (
            self.past.requirement.end != self.issue_time
            or self.attempt is None
            or self.past.binding_fingerprint != self.attempt.key.binding_fingerprint
        ):
            raise ValueError("as-used past weather identity mismatch")
        if isinstance(self.future, FutureWeatherUse) and (
            self.future.requirement.station_id != self.station_id
            or self.future.requirement.issue_time != self.issue_time
        ):
            raise ValueError("as-used future weather identity mismatch")


@dataclass(frozen=True, kw_only=True, slots=True)
class ContributingWeatherUse:
    forecast_id: ForecastId
    evidence_sha256: str | None
    weather_use: WeatherUse | None

    def __post_init__(self) -> None:
        digest = self.evidence_sha256
        if self.weather_use is not None and digest is None:
            raise ValueError("selected contributor requires evidence hash")
        if digest is not None and (
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("contributor evidence hash must be SHA256")


@dataclass(frozen=True, kw_only=True, slots=True)
class DerivedWeatherUse:
    station_id: StationId
    issue_time: UtcDatetime
    contributors: tuple[ContributingWeatherUse, ...]

    def __post_init__(self) -> None:
        require_midnight(self.issue_time)
        if not self.contributors or not any(
            item.weather_use is not None for item in self.contributors
        ):
            raise ValueError("derived weather requires a selected contributor")
        if len({item.forecast_id for item in self.contributors}) != len(
            self.contributors
        ):
            raise ValueError("derived weather contributors must be unique")
        if any(
            item.weather_use is not None
            and (
                item.weather_use.station_id != self.station_id
                or item.weather_use.issue_time != self.issue_time
            )
            for item in self.contributors
        ):
            raise ValueError("derived weather contributor scope mismatch")
