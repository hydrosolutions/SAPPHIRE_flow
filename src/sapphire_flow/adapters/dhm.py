from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource, StationKind
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CaptureBatchId,
    CaptureRequestInterval,
    DhmRequestEvidence,
    NativeFetchOutcome,
)
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sapphire_flow.config.dhm import DhmBinding, DhmConfig
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)
_RESPONSE_BYTES = 2 * 1024 * 1024


class DhmBoundsMode(Enum):
    POLLING = "polling"
    HISTORICAL = "historical"


def validate_dhm_station_binding(
    config: DhmConfig, station: StationConfig
) -> DhmBinding:
    binding = next(
        (
            item
            for item in config.bindings
            if (item.network, item.station_code) == (station.network, station.code)
        ),
        None,
    )
    if binding is None or "water_level" not in station.measured_parameters:
        raise ConfigurationError("DHM station requires binding and measured level")
    unit = station.water_level_unit
    compatible = (
        unit in ("m", "m a.s.l.")
        if binding.level_reference == "masl"
        else unit == "m" and station.water_level_datum_masl is None
    )
    if not compatible:
        raise ConfigurationError("Incompatible DHM station unit or datum")
    return binding


class _Reading(BaseModel):
    model_config = ConfigDict(strict=True)
    station: int = Field(gt=0)
    water_level: float | None = Field(alias="waterLevel", allow_inf_nan=False)
    measured_at: datetime = Field(alias="waterLevelOn")

    @field_validator("measured_at", mode="before")
    @classmethod
    def parse_measurement_time(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("Measurement timestamp must be an offset-aware string")
        return ensure_utc(datetime.fromisoformat(value))


class _Page(BaseModel):
    model_config = ConfigDict(strict=True)
    results: list[_Reading]
    next: str | None = None


class _FetchError(AdapterError):
    def __init__(self, cause: FetchOutcomeCause, detail: str) -> None:
        super().__init__(detail)
        self.cause = cause


@dataclass(slots=True, kw_only=True)
class _BatchBudget:
    deadline: float
    requests: int = 0
    received_bytes: int = 0
    accepted_bytes: int = 0
    last_request_at: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _StationWork:
    station: StationConfig
    binding: DhmBinding
    start: UtcDatetime
    windows: int
    bounds_mode: DhmBoundsMode = DhmBoundsMode.POLLING


def _failure(
    station: StationConfig, cause: FetchOutcomeCause, detail: str
) -> StationFetchOutcome:
    return StationFetchOutcome(
        station_id=station.id,
        observations=(),
        failure_cause=cause,
        failure_detail=detail,
    )


@dataclass(slots=True)
class _RequestTrace:
    intervals: list[CaptureRequestInterval] = field(
        default_factory=list[CaptureRequestInterval]
    )
    started_at: UtcDatetime | None = None


class DhmAdapter:
    def __init__(
        self,
        *,
        config: DhmConfig,
        http_client: httpx.Client,
        clock: Callable[[], UtcDatetime],
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        id_gen: Callable[[], UUID] = uuid4,
        request_clock: Callable[[], UtcDatetime] | None = None,
    ) -> None:
        self._config = config
        self._http_client = http_client
        self._clock = clock
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._id_gen = id_gen
        self._request_clock = request_clock or clock
        self._window_span = timedelta(hours=config.window_hours)
        self._history_url = httpx.URL(config.endpoint.rstrip("/") + "/").join("river/")
        self._bindings = {(b.network, b.station_code): b for b in config.bindings}

    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        return self.fetch_observations_batch(station_configs, since).observations

    def fetch_observations_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> HydroScraperBatchResult:
        return self._fetch_batch(station_configs, since, None, DhmBoundsMode.POLLING)

    def fetch_observations_window(
        self,
        station_configs: list[StationConfig],
        *,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> HydroScraperBatchResult:
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ConfigurationError("DHM historical bounds must be timezone aware")
        start, end = ensure_utc(start), ensure_utc(end)
        if not start < end or end - start > timedelta(days=31):
            raise ConfigurationError("DHM historical interval must be at most 31 days")
        return self._fetch_batch(
            station_configs,
            {station.id: start for station in station_configs},
            end,
            DhmBoundsMode.HISTORICAL,
        )

    def _fetch_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
        end: UtcDatetime | None,
        bounds_mode: DhmBoundsMode,
    ) -> HydroScraperBatchResult:
        budget = _BatchBudget(
            deadline=self._monotonic() + self._config.max_batch_seconds
        )
        if end is None:
            end = ensure_utc(self._clock())
        supported = [
            s
            for s in station_configs
            if s.network == "dhm" and s.station_kind == StationKind.RIVER
        ]
        work, failures = self._preflight(supported, since, end, budget)
        work = [replace(item, bounds_mode=bounds_mode) for item in work]
        if failures:
            return HydroScraperBatchResult(
                outcomes=tuple(
                    failures.get(s.id)
                    or _failure(
                        s,
                        FetchOutcomeCause.BATCH_ABORTED,
                        "DHM preflight stopped batch",
                    )
                    for s in supported
                )
            )
        outcomes: list[StationFetchOutcome] = []
        stop: FetchOutcomeCause | None = None
        for item in work:
            trace = _RequestTrace()
            batch_id = CaptureBatchId(self._id_gen())
            requests_before = budget.requests
            bytes_before = budget.accepted_bytes
            if stop is not None:
                outcome = _failure(
                    item.station,
                    FetchOutcomeCause.BATCH_ABORTED,
                    f"DHM batch stopped: {stop.value}",
                )
            else:
                outcome = self._fetch_station(item, end, budget, trace)
                stop = outcome.failure_cause
            request_count = budget.requests - requests_before
            outcome = replace(
                outcome,
                dhm_request_evidence=DhmRequestEvidence(
                    batch_id=batch_id,
                    station_id=item.station.id,
                    provider=CanonicalProviderIdentity.parse(
                        self._config.endpoint, item.binding.api_station_id
                    ),
                    requested_intervals=tuple(trace.intervals),
                    request_started_at=trace.started_at,
                    retrieved_at=ensure_utc(self._request_clock()),
                    outcome=(
                        NativeFetchOutcome.FAILED
                        if outcome.failure_cause is not None
                        else NativeFetchOutcome.COMPLETE
                        if request_count
                        else NativeFetchOutcome.NO_REQUEST
                    ),
                    request_count=request_count,
                    accepted_bytes=budget.accepted_bytes - bytes_before,
                ),
            )
            outcomes.append(outcome)
        log.info(
            "observation.dhm_batch_finished",
            requests=budget.requests,
            received_bytes=budget.received_bytes,
            accepted_bytes=budget.accepted_bytes,
            deadline_overrun=self._monotonic() >= budget.deadline,
        )
        return HydroScraperBatchResult(outcomes=tuple(outcomes))

    def _time_fits(self, requests: int, budget: _BatchBudget) -> bool:
        remaining = budget.deadline - self._monotonic()
        return (
            remaining > 0
            and max(0, requests - 1) * self._config.min_request_interval_s < remaining
        )

    def _preflight(
        self,
        stations: list[StationConfig],
        since: dict[StationId, UtcDatetime],
        end: UtcDatetime,
        budget: _BatchBudget,
    ) -> tuple[list[_StationWork], dict[StationId, StationFetchOutcome]]:
        work: list[_StationWork] = []
        failures: dict[StationId, StationFetchOutcome] = {}
        for station in stations:
            try:
                binding = self._binding(station)
                start = since.get(station.id)
                if start is None:
                    raise ConfigurationError("DHM station requires a fetch watermark")
                start = ensure_utc(start)
            except (ConfigurationError, ValueError, OverflowError):
                failures[station.id] = _failure(
                    station,
                    FetchOutcomeCause.CONFIGURATION_ERROR,
                    "Invalid DHM binding, metadata or watermark",
                )
                continue
            quotient, remainder = divmod(
                max(timedelta(0), end - start), self._window_span
            )
            windows = quotient + int(remainder > timedelta(0))
            item = _StationWork(
                station=station, binding=binding, start=start, windows=windows
            )
            work.append(item)
            log.info(
                "observation.dhm_preflight_station",
                station_id=str(station.id),
                minimum_requests=windows,
            )
            if windows and (
                windows
                > min(
                    self._config.max_pages_per_station,
                    self._config.max_requests_per_batch,
                )
                or not self._time_fits(windows, budget)
            ):
                failures[station.id] = _failure(
                    station,
                    FetchOutcomeCause.RESOURCE_LIMIT,
                    "DHM station minimum work exceeds budget",
                )
        minimum = sum(item.windows for item in work)
        log.info(
            "observation.dhm_preflight_batch",
            minimum_requests=minimum,
            minimum_pacing_s=max(0, minimum - 1) * self._config.min_request_interval_s,
        )
        if (
            not failures
            and minimum
            and (
                minimum > self._config.max_requests_per_batch
                or not self._time_fits(minimum, budget)
            )
        ):
            failures = {
                item.station.id: _failure(
                    item.station,
                    FetchOutcomeCause.RESOURCE_LIMIT,
                    "DHM aggregate minimum work exceeds budget",
                )
                for item in work
                if item.windows
            }
        return work, failures

    def _binding(self, station: StationConfig) -> DhmBinding:
        return validate_dhm_station_binding(self._config, station)

    def _fetch_station(
        self,
        work: _StationWork,
        end: UtcDatetime,
        budget: _BatchBudget,
        trace: _RequestTrace | None = None,
    ) -> StationFetchOutcome:
        try:
            observations = self._fetch_history(
                work.station,
                work.binding,
                work.start,
                end,
                budget,
                trace,
                bounds_mode=work.bounds_mode,
            )
            if work.windows:
                self._check_deadline(budget)
            return StationFetchOutcome(
                station_id=work.station.id,
                observations=tuple(observations),
                failure_cause=None,
                failure_detail=None,
            )
        except _FetchError as exc:
            cause, detail = exc.cause, str(exc)
        except httpx.TimeoutException:
            if self._monotonic() >= budget.deadline:
                cause, detail = FetchOutcomeCause.RESOURCE_LIMIT, "DHM deadline"
            else:
                cause, detail = (
                    FetchOutcomeCause.TRANSPORT_ERROR,
                    "DHM request timed out",
                )
        except httpx.RequestError:
            cause, detail = FetchOutcomeCause.TRANSPORT_ERROR, "DHM request failed"
        except (ValueError, OverflowError):
            cause, detail = (
                FetchOutcomeCause.MALFORMED_RESPONSE,
                "Invalid DHM response or continuation",
            )
        log.warning(
            "observation.fetch_failed",
            station_id=str(work.station.id),
            failure_cause=cause.value,
            reason=detail,
        )
        return _failure(work.station, cause, detail)

    def _check_deadline(self, budget: _BatchBudget) -> None:
        if self._monotonic() >= budget.deadline:
            raise _FetchError(FetchOutcomeCause.RESOURCE_LIMIT, "DHM deadline")

    def _before_request(self, budget: _BatchBudget) -> float:
        self._check_deadline(budget)
        if budget.requests >= self._config.max_requests_per_batch:
            raise _FetchError(FetchOutcomeCause.RESOURCE_LIMIT, "DHM request budget")
        if budget.accepted_bytes >= self._config.max_bytes_per_batch:
            raise _FetchError(FetchOutcomeCause.RESOURCE_LIMIT, "DHM batch_bytes")
        now = self._monotonic()
        earliest = (
            now
            if budget.last_request_at is None
            else max(now, budget.last_request_at + self._config.min_request_interval_s)
        )
        if earliest >= budget.deadline:
            raise _FetchError(
                FetchOutcomeCause.RESOURCE_LIMIT, "DHM pacing cannot fit deadline"
            )
        if earliest > now:
            self._sleeper(earliest - now)
        started = self._monotonic()
        if started >= budget.deadline:
            raise _FetchError(FetchOutcomeCause.RESOURCE_LIMIT, "DHM deadline")
        budget.requests += 1
        budget.last_request_at = started
        return min(self._config.timeout_s, budget.deadline - started)

    @staticmethod
    def _content_length(response: httpx.Response) -> int | None:
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding != "identity":
            raise ValueError("DHM requires identity encoding")
        lengths = response.headers.get_list("content-length")
        if not lengths:
            return None
        value = lengths[0]
        if len(lengths) != 1 or not value.isascii() or not value.isdigit():
            raise ValueError("Invalid DHM Content-Length")
        return int(value)

    def _get_page(
        self,
        url: httpx.URL,
        budget: _BatchBudget,
        trace: _RequestTrace | None = None,
    ) -> _Page:
        timeout = self._before_request(budget)
        if trace is not None:
            interval = CaptureRequestInterval(
                lower_exclusive=ensure_utc(
                    datetime.fromisoformat(url.params["water_level_on__gt"])
                ),
                upper_exclusive=ensure_utc(
                    datetime.fromisoformat(url.params["water_level_on__lt"])
                ),
            )
            if interval not in trace.intervals:
                trace.intervals.append(interval)
            if trace.started_at is None:
                trace.started_at = ensure_utc(self._request_clock())
        with self._http_client.stream(
            "GET",
            url,
            timeout=timeout,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code == 429:
                raise _FetchError(FetchOutcomeCause.RATE_LIMITED, "DHM HTTP 429")
            if not response.is_success:
                raise _FetchError(
                    FetchOutcomeCause.HTTP_STATUS_ERROR,
                    f"DHM HTTP {response.status_code}",
                )
            length = self._content_length(response)
            self._check_deadline(budget)
            if length is not None:
                self._check_bytes(length, 0, budget)
            body = bytearray()
            # No chunk_size: HTTPX must not coalesce small raw yields before checks.
            raw = response.iter_raw()
            while True:
                self._check_deadline(budget)
                try:
                    chunk = next(raw)
                except StopIteration:
                    break
                budget.received_bytes += len(chunk)
                self._check_deadline(budget)
                self._check_bytes(len(chunk), len(body), budget)
                body.extend(chunk)
                budget.accepted_bytes += len(chunk)
            return _Page.model_validate_json(body)

    def _check_bytes(
        self, incoming: int, response_bytes: int, budget: _BatchBudget
    ) -> None:
        if response_bytes + incoming > _RESPONSE_BYTES:
            raise _FetchError(FetchOutcomeCause.RESOURCE_LIMIT, "DHM response_bytes")
        if budget.accepted_bytes + incoming > self._config.max_bytes_per_batch:
            raise _FetchError(FetchOutcomeCause.RESOURCE_LIMIT, "DHM batch_bytes")

    def _fetch_history(
        self,
        station: StationConfig,
        binding: DhmBinding,
        since: UtcDatetime,
        end: UtcDatetime,
        budget: _BatchBudget,
        trace: _RequestTrace | None = None,
        *,
        bounds_mode: DhmBoundsMode = DhmBoundsMode.POLLING,
    ) -> list[RawObservation]:
        observations: dict[UtcDatetime, RawObservation] = {}
        window_start = since
        pages_used = 0
        while window_start < end:
            window_end = min(window_start + self._window_span, end)
            window, count = self._fetch_window(
                station,
                binding,
                window_start,
                window_end,
                self._config.max_pages_per_station - pages_used,
                budget,
                trace,
                bounds_mode=bounds_mode,
            )
            pages_used += count
            for observation in window:
                if (
                    bounds_mode is DhmBoundsMode.POLLING
                    and observation.timestamp <= since
                ):
                    continue
                previous = observations.get(observation.timestamp)
                if previous is not None and previous.value != observation.value:
                    raise ValueError("Conflicting DHM measurements")
                observations[observation.timestamp] = observation
            window_start = window_end
        selected = list(observations.values())
        if bounds_mode is DhmBoundsMode.HISTORICAL:
            selected = [row for row in selected if since <= row.timestamp < end]
        return sorted(selected, key=lambda obs: obs.timestamp)

    def _window_url(
        self,
        binding: DhmBinding,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> httpx.URL:
        return self._history_url.copy_merge_params(
            {
                "station": binding.api_station_id,
                "historical": "true",
                "water_level_on__gt": (start - timedelta(seconds=1)).isoformat(),
                "water_level_on__lt": (end + timedelta(seconds=1)).isoformat(),
                "ordering": "water_level_on",
                "limit": self._config.page_size,
            }
        )

    def _continuation(self, raw: str, first: httpx.URL) -> httpx.URL:
        try:
            url = first.join(raw)
        except httpx.InvalidURL:
            raise ValueError("Invalid DHM continuation") from None
        if (
            (url.scheme, url.host, url.port, url.path)
            != (first.scheme, first.host, first.port, first.path)
            or url.userinfo
            or url.fragment
        ):
            raise ValueError("DHM continuation changed origin or path")
        params = url.params
        if len(params.multi_items()) != len(params):
            raise ValueError("Duplicate DHM continuation parameters")
        allowed = set(first.params) | {"offset"}
        if set(params) - allowed:
            raise ValueError("Unexpected DHM continuation parameters")
        for key, value in first.params.items():
            if key != "limit" and params.get(key) != value:
                raise ValueError("DHM continuation changed station or time filters")
        limit = params.get("limit", "")
        offset = params.get("offset", "0")
        if (
            not limit.isascii()
            or not limit.isdigit()
            or not 0 < int(limit) <= self._config.page_size
            or not offset.isascii()
            or not offset.isdigit()
        ):
            raise ValueError("Invalid DHM continuation page bounds")
        return url

    def _fetch_window(
        self,
        station: StationConfig,
        binding: DhmBinding,
        start: UtcDatetime,
        end: UtcDatetime,
        pages_remaining: int,
        budget: _BatchBudget,
        trace: _RequestTrace | None = None,
        *,
        bounds_mode: DhmBoundsMode = DhmBoundsMode.POLLING,
    ) -> tuple[list[RawObservation], int]:
        first = self._window_url(binding, start, end)
        url = first
        seen_urls: set[httpx.URL] = set()
        seen_pages: set[tuple[tuple[int, datetime, float | None], ...]] = set()
        observations: list[RawObservation] = []
        for count in range(1, pages_remaining + 1):
            if url in seen_urls:
                raise ValueError("Repeated DHM continuation")
            seen_urls.add(url)
            page = self._get_page(url, budget, trace)
            if not page.results:
                return observations, count
            signature = tuple(
                (r.station, r.measured_at, r.water_level) for r in page.results
            )
            if signature in seen_pages:
                raise ValueError("Repeated DHM response page")
            seen_pages.add(signature)
            observations.extend(
                self._observations(
                    page, station, binding, start, end, bounds_mode=bounds_mode
                )
            )
            if page.next is None:
                return observations, count
            if not page.next:
                raise ValueError("Empty DHM continuation")
            url = self._continuation(page.next, first)
        raise _FetchError(
            FetchOutcomeCause.RESOURCE_LIMIT, "DHM station page limit exhausted"
        )

    @staticmethod
    def _observations(
        page: _Page,
        station: StationConfig,
        binding: DhmBinding,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        bounds_mode: DhmBoundsMode = DhmBoundsMode.POLLING,
    ) -> list[RawObservation]:
        observations: list[RawObservation] = []
        for row in page.results:
            if row.station != binding.api_station_id:
                raise ValueError("Response station differs from requested DHM station")
            timestamp = ensure_utc(row.measured_at)
            in_window = (
                start <= timestamp <= end
                if bounds_mode is DhmBoundsMode.POLLING
                else start - timedelta(seconds=1)
                < timestamp
                < end + timedelta(seconds=1)
            )
            if row.water_level is not None and in_window:
                observations.append(
                    RawObservation(
                        station_id=station.id,
                        timestamp=timestamp,
                        parameter="water_level",
                        value=row.water_level,
                        source=ObservationSource.MEASURED,
                    )
                )
        return observations
