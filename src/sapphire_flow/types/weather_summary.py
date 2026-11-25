from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from sapphire_flow.types.datetime import (
    UtcDatetime,  # noqa: TC001 — stored JSON decoder
)
from sapphire_flow.types.ids import StationId  # noqa: TC001 — stored JSON decoder
from sapphire_flow.types.operational_weather import require_utc


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherSourceRunSummary:
    source: str
    source_run: UtcDatetime

    def __post_init__(self) -> None:
        require_utc(self.source_run)


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherLegUseSummary:
    freshness: Literal["current", "stale"]
    failure_code: str | None
    failure_at: UtcDatetime | None
    snapshot_id: str | None
    snapshot_sha256: str
    retrieved_at: UtcDatetime
    age_at_use_seconds: float
    sources: tuple[WeatherSourceRunSummary, ...]

    def __post_init__(self) -> None:
        require_utc(self.retrieved_at)
        if self.failure_at is not None:
            require_utc(self.failure_at)
        if not math.isfinite(self.age_at_use_seconds) or self.age_at_use_seconds < 0:
            raise ValueError("weather summary age must be finite and nonnegative")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherLegSummary:
    freshness: Literal["current", "stale", "unused", "unknown"]
    uses: tuple[WeatherLegUseSummary, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherUseSummary:
    station_id: StationId
    issue_time: UtcDatetime
    past: WeatherLegSummary
    future: WeatherLegSummary

    def __post_init__(self) -> None:
        require_utc(self.issue_time)


@dataclass(frozen=True, kw_only=True, slots=True)
class PersistedWeatherSummary:
    schema_version: int
    weather_evidence_version: Literal[1, 2]
    manifest_sha256: str
    snapshot_sha256: str
    summary: WeatherUseSummary
