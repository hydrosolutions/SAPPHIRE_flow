from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.write_principal import enforce_tenant_isolation
from sapphire_flow.store.operational_weather_cache import complete_weather_entry
from sapphire_flow.store.operational_weather_codec import encode_cache_entry
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherRequest,
    OperationalWeatherDay,
    OperationalWeatherSample,
    OperationalWeatherSource,
    OperationalWeatherWindow,
    WeatherAttemptState,
    WeatherCacheAttempt,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherProductIdentity,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.protocols.stores import StationStore
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.write_principal import WritePrincipal


ISSUE = ensure_utc(datetime(2026, 11, 6, tzinfo=UTC))
FINGERPRINT = "a" * 64


def make_operational_window(
    station_id: StationId, *, issue_time: UtcDatetime = ISSUE
) -> OperationalWeatherWindow:
    start = ensure_utc(issue_time - timedelta(days=1))
    samples = tuple(
        OperationalWeatherSample(
            parameter=parameter,
            source=OperationalWeatherSource.IFS,
            source_run=start,
            valid_time=ensure_utc(start + timedelta(hours=hour)),
            value=1.0 if parameter == "precipitation" else 10.0,
            product=WeatherProductIdentity.CONTROL,
            interval_end=ensure_utc(start + timedelta(hours=hour + 3))
            if parameter == "precipitation"
            else None,
        )
        for parameter in ("precipitation", "temperature")
        for hour in range(0, 24, 3)
    )
    day = OperationalWeatherDay(
        day=start, precipitation_mm=8.0, temperature_c=10.0, samples=samples
    )
    return OperationalWeatherWindow(
        station_id=station_id,
        binding_fingerprint=FINGERPRINT,
        issue_time=issue_time,
        start=start,
        end=issue_time,
        days=(day,),
    )


class FakeOperationalWeatherCache:
    def __init__(
        self,
        station_store: StationStore,
        *,
        max_past_bytes: int = 2 * 1024 * 1024,
        max_receipts: int = 32,
    ) -> None:
        if max_past_bytes <= 0 or not 1 <= max_receipts <= 32:
            raise ValueError("invalid operational cache bounds")
        self.station_store = station_store
        self.max_past_bytes = max_past_bytes
        self.max_receipts = max_receipts
        self.entries: dict[WeatherCacheKey, WeatherCacheEntry] = {}

    def _authorize(
        self,
        key: WeatherCacheKey,
        principal: WritePrincipal,
        now: UtcDatetime,
    ) -> None:
        if principal is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise ConfigurationError(
                "selected weather requires an explicit write principal"
            )
        station = self.station_store.fetch_station(key.station_id)
        if station is None:
            raise ConfigurationError("operational weather station does not exist")
        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=station.tenant_id,
            audit_log_store=None,
            event_type=AuditEventType.OPERATIONAL_WEATHER_REFRESH,
            target_type="station",
            target_id=str(key.station_id),
            detail=None,
            now=now,
        )

    def fetch(self, key: WeatherCacheKey) -> WeatherCacheEntry | None:
        return self.entries.get(key)

    def begin(
        self,
        *,
        key: WeatherCacheKey,
        principal: WritePrincipal,
        attempt_id: UUID,
        issue_time: UtcDatetime,
        started_at: UtcDatetime,
        future_scopes: tuple[FutureWeatherRequest, ...] = (),
    ) -> WeatherCacheAttempt:
        self._authorize(key, principal, started_at)
        prior = self.fetch(key)
        attempt = WeatherCacheAttempt(
            key=key,
            generation=prior.attempt.generation + 1 if prior else 1,
            id=attempt_id,
            issue_time=issue_time,
            started_at=started_at,
        )
        pending = WeatherLegOutcome(state=WeatherLegState.PENDING)
        entry = WeatherCacheEntry(
            attempt=attempt,
            state=WeatherAttemptState.PENDING,
            past_outcome=pending,
            future_attempts=tuple(
                FutureWeatherAttempt(scope=scope, outcome=pending)
                for scope in future_scopes
            ),
            past_snapshot=prior.past_snapshot if prior else None,
            future_receipts=prior.future_receipts if prior else (),
        )
        encode_cache_entry(entry, max_past_bytes=self.max_past_bytes)
        self.entries[key] = entry
        return attempt

    def finish(
        self,
        *,
        attempt: WeatherCacheAttempt,
        principal: WritePrincipal,
        completion: WeatherCacheCompletion,
        earliest_cycle: UtcDatetime,
    ) -> WeatherCacheEntry:
        self._authorize(attempt.key, principal, completion.finished_at)
        entry = complete_weather_entry(
            self.fetch(attempt.key),
            attempt=attempt,
            completion=completion,
            earliest_cycle=earliest_cycle,
            max_past_bytes=self.max_past_bytes,
            max_receipts=self.max_receipts,
        )
        encode_cache_entry(entry, max_past_bytes=self.max_past_bytes)
        self.entries[attempt.key] = entry
        return entry
