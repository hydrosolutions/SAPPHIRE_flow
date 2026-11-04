from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.native_capture import (
        CanonicalProviderIdentity,
        CapturedLevelReference,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoricalCaptureRequest:
    tenant_code: str
    station_ids: tuple[StationId, ...]
    start: UtcDatetime
    end: UtcDatetime

    def __post_init__(self) -> None:
        if not self.tenant_code or self.tenant_code.strip() != self.tenant_code:
            raise ValueError("historical request requires an explicit tenant code")
        if not self.station_ids or len(set(self.station_ids)) != len(self.station_ids):
            raise ValueError("historical station IDs must be nonempty and unique")
        if self.start.utcoffset() != timedelta(0) or self.end.utcoffset() != timedelta(
            0
        ):
            raise ValueError("historical bounds require UTC")
        if not self.start < self.end or self.end - self.start > timedelta(days=31):
            raise ValueError("historical interval must be positive and at most 31 days")

    @classmethod
    def parse(
        cls,
        *,
        tenant_code: str,
        station_ids: list[str],
        start: datetime,
        end: datetime,
        now: UtcDatetime,
    ) -> HistoricalCaptureRequest:
        try:
            if start.utcoffset() is None or end.utcoffset() is None:
                raise ValueError("naive historical bounds")
            request = cls(
                tenant_code=tenant_code,
                station_ids=tuple(StationId(UUID(value)) for value in station_ids),
                start=ensure_utc(start),
                end=ensure_utc(end),
            )
            if request.end > now:
                raise ValueError("historical end is in the future")
            return request
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise ConfigurationError("Invalid historical capture request") from None


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeCaptureScope:
    tenant_id: TenantId
    station_id: StationId
    station_code: str
    network: str
    provider: CanonicalProviderIdentity
    level_reference: CapturedLevelReference
