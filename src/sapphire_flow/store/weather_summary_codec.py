from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from sapphire_flow.types.weather_summary import (
    PersistedWeatherSummary,
    WeatherUseSummary,
)

if TYPE_CHECKING:
    from typing import Literal

SUMMARY_SCHEMA_VERSION = 1
_ADAPTER = TypeAdapter(WeatherUseSummary)


def summary_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def encode_weather_summary(summary: WeatherUseSummary) -> str:
    return _ADAPTER.dump_json(summary).decode("utf-8")


def decode_weather_summary(value: str, digest: str) -> WeatherUseSummary:
    if summary_digest(value) != digest:
        raise ValueError("weather summary hash mismatch")
    return _ADAPTER.validate_json(value, strict=True, extra="forbid")


def bind_weather_summary(
    summary: WeatherUseSummary,
    *,
    manifest_json: str,
    snapshot_sha256: str,
    weather_evidence_version: Literal[1, 2],
) -> PersistedWeatherSummary:
    return PersistedWeatherSummary(
        schema_version=SUMMARY_SCHEMA_VERSION,
        weather_evidence_version=weather_evidence_version,
        manifest_sha256=summary_digest(manifest_json),
        snapshot_sha256=snapshot_sha256,
        summary=summary,
    )
