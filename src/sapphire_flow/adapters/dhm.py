from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource, StationKind
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.config.dhm import DhmBinding, DhmConfig
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


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


class DhmAdapter:
    def __init__(
        self,
        *,
        config: DhmConfig,
        http_client: httpx.Client,
        clock: Callable[[], UtcDatetime],
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._http_client = http_client
        self._clock = clock
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request_at: float | None = None
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
        end = ensure_utc(self._clock())
        outcomes: list[StationFetchOutcome] = []
        for station in station_configs:
            if station.network != "dhm" or station.station_kind != StationKind.RIVER:
                log.info(
                    "observation.station_skipped",
                    station_id=str(station.id),
                    reason="unsupported_dhm_station",
                )
                continue
            outcomes.append(self._fetch_station(station, since.get(station.id), end))
        return HydroScraperBatchResult(outcomes=tuple(outcomes))

    def _binding(self, station: StationConfig) -> DhmBinding:
        binding = self._bindings.get((station.network, station.code))
        if binding is None or "water_level" not in station.measured_parameters:
            raise ConfigurationError("DHM station requires binding and measured level")
        unit = station.water_level_unit
        if binding.level_reference == "masl":
            compatible = unit in ("m", "m a.s.l.")
        else:
            compatible = unit == "m" and station.water_level_datum_masl is None
        if not compatible:
            raise ConfigurationError("Incompatible DHM station unit or datum")
        return binding

    def _fetch_station(
        self,
        station: StationConfig,
        since: UtcDatetime | None,
        end: UtcDatetime,
    ) -> StationFetchOutcome:
        try:
            binding = self._binding(station)
            if since is None:
                raise ConfigurationError("DHM station requires a fetch watermark")
            observations = self._fetch_history(station, binding, ensure_utc(since), end)
            return StationFetchOutcome(
                station_id=station.id,
                observations=tuple(observations),
                failure_cause=None,
                failure_detail=None,
            )
        except ConfigurationError as exc:
            cause, detail = FetchOutcomeCause.CONFIGURATION_ERROR, str(exc)
        except _FetchError as exc:
            cause, detail = exc.cause, str(exc)
        except httpx.RequestError:
            cause, detail = FetchOutcomeCause.TRANSPORT_ERROR, "DHM request failed"
        except (ValueError, OverflowError):
            cause = FetchOutcomeCause.MALFORMED_RESPONSE
            detail = "Invalid DHM response or time range"
        log.warning(
            "observation.fetch_failed",
            station_id=str(station.id),
            failure_cause=cause.value,
            reason=detail,
        )
        return StationFetchOutcome(
            station_id=station.id,
            observations=(),
            failure_cause=cause,
            failure_detail=detail,
        )

    def _get_page(self, url: httpx.URL) -> _Page:
        if self._last_request_at is not None:
            remaining = self._config.min_request_interval_s - (
                self._monotonic() - self._last_request_at
            )
            if remaining > 0:
                self._sleeper(remaining)
        self._last_request_at = self._monotonic()
        response = self._http_client.get(
            url,
            timeout=self._config.timeout_s,
            follow_redirects=False,
        )
        if response.status_code == 429:
            raise _FetchError(FetchOutcomeCause.RATE_LIMITED, "DHM HTTP 429")
        if not response.is_success:
            raise _FetchError(
                FetchOutcomeCause.HTTP_STATUS_ERROR, f"DHM HTTP {response.status_code}"
            )
        return _Page.model_validate_json(response.content)

    def _fetch_history(
        self,
        station: StationConfig,
        binding: DhmBinding,
        since: UtcDatetime,
        end: UtcDatetime,
    ) -> list[RawObservation]:
        observations: dict[UtcDatetime, RawObservation] = {}
        window_start = since
        pages_used = 0
        while window_start < end:
            window_end = min(
                window_start + timedelta(hours=self._config.window_hours), end
            )
            window, count = self._fetch_window(
                station,
                binding,
                window_start,
                window_end,
                self._config.max_pages_per_station - pages_used,
            )
            pages_used += count
            for observation in window:
                if observation.timestamp <= since:
                    continue
                previous = observations.get(observation.timestamp)
                if previous is not None and previous.value != observation.value:
                    raise ValueError("Conflicting DHM measurements")
                observations[observation.timestamp] = observation
            window_start = window_end
        return sorted(observations.values(), key=lambda obs: obs.timestamp)

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
            page = self._get_page(url)
            if not page.results:
                return observations, count
            signature = tuple(
                (r.station, r.measured_at, r.water_level) for r in page.results
            )
            if signature in seen_pages:
                raise ValueError("Repeated DHM response page")
            seen_pages.add(signature)
            observations.extend(self._observations(page, station, binding, start, end))
            if page.next is None:
                return observations, count
            if not page.next:
                raise ValueError("Empty DHM continuation")
            url = self._continuation(page.next, first)
        raise ValueError("DHM station page limit exhausted")

    @staticmethod
    def _observations(
        page: _Page,
        station: StationConfig,
        binding: DhmBinding,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[RawObservation]:
        observations: list[RawObservation] = []
        for row in page.results:
            if row.station != binding.api_station_id:
                raise ValueError("Response station differs from requested DHM station")
            timestamp = ensure_utc(row.measured_at)
            if row.water_level is not None and start <= timestamp <= end:
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
