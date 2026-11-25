from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from sapphire_flow.services.selected_weather_evidence import restore_selected_weather
from sapphire_flow.services.weather_summary import summarize_weather_use
from sapphire_flow.services.weather_summary_parent import validate_summary_parent
from sapphire_flow.store.weather_summary_codec import (
    SUMMARY_SCHEMA_VERSION,
    summary_digest,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import ForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.ids import ForecastId, ModelId, StationId
    from sapphire_flow.types.operational_weather import (
        DerivedWeatherUse,
        WeatherUse,
    )
    from sapphire_flow.types.operational_weather_v2 import (
        DerivedWeatherUseV2,
        WeatherUseV2,
    )
    from sapphire_flow.types.weather_summary import WeatherUseSummary


class WeatherSourceRunResponse(BaseModel):
    source: str
    source_run: datetime = Field(
        description=(
            "Weather source run/reference time, not forecast issue or retrieval time."
        )
    )


class WeatherLegUseResponse(BaseModel):
    freshness: Literal["current", "stale"]
    failure_code: str | None
    failure_at: datetime | None
    snapshot_id: str | None
    snapshot_sha256: str
    retrieved_at: datetime = Field(
        description="Time the retained weather input was retrieved or validated."
    )
    age_at_use_seconds: float = Field(
        ge=0,
        description=(
            "Seconds between retrieval/validation and use by "
            "the forecast, not age at this API request."
        ),
    )
    sources: list[WeatherSourceRunResponse]


class WeatherLegResponse(BaseModel):
    freshness: Literal["current", "stale", "unused", "unknown"]
    uses: list[WeatherLegUseResponse]


class WeatherUseResponse(BaseModel):
    station_id: str
    issue_time: datetime
    past: WeatherLegResponse
    future: WeatherLegResponse


def _response(summary: WeatherUseSummary) -> WeatherUseResponse:
    return WeatherUseResponse.model_validate(
        {**asdict(summary), "station_id": str(summary.station_id)}
    )


def _summary(
    use: WeatherUse | DerivedWeatherUse | WeatherUseV2 | DerivedWeatherUseV2,
) -> WeatherUseResponse:
    return _response(summarize_weather_use(use))


def read_weather_summary(
    *,
    version: int | None,
    forecast_id: ForecastId,
    station_id: StationId,
    model_id: ModelId,
    issue_time: UtcDatetime,
    data_use: ForecastDataUse,
    store: ForecastStore,
) -> WeatherUseResponse | None:
    if version is None:
        return None
    try:
        if type(version) is not int or version not in (1, 2):
            raise ValueError("unsupported weather evidence version")
        evidence = store.fetch_evidence(forecast_id)
        if evidence is None:
            raise ValueError("selected evidence missing")
        validate_summary_parent(
            evidence,
            version=version,
            forecast_id=forecast_id,
            station_id=station_id,
            model_id=model_id,
            issue_time=issue_time,
            data_use=data_use,
        )
        projection = store.fetch_weather_summary(forecast_id)
        if projection is not None:
            if (
                projection.schema_version != SUMMARY_SCHEMA_VERSION
                or projection.weather_evidence_version != version
                or projection.manifest_sha256 != summary_digest(evidence.manifest_json)
                or projection.snapshot_sha256 != evidence.snapshot_sha256
                or projection.summary.station_id != station_id
                or projection.summary.issue_time != issue_time
            ):
                raise ValueError("weather summary evidence binding mismatch")
            return _response(projection.summary)
        facts = restore_selected_weather(
            evidence, model_id=model_id, issue_time=issue_time
        )
        return _summary(facts[station_id])
    except Exception:
        raise HTTPException(503, "Selected weather evidence is unavailable") from None
