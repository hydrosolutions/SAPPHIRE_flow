from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast

from fastapi import HTTPException
from pydantic import BaseModel, Field

from sapphire_flow.services.selected_weather_evidence import restore_selected_weather
from sapphire_flow.types.operational_weather import (
    DerivedWeatherUse,
    FutureWeatherUse,
    PastWeatherUse,
    WeatherUse,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import ForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.ids import ForecastId, ModelId, StationId


class WeatherSourceRunResponse(BaseModel):
    source: str
    source_run: datetime


class WeatherLegUseResponse(BaseModel):
    freshness: Literal["current", "stale"]
    failure_code: str | None
    failure_at: datetime | None
    snapshot_id: str | None
    snapshot_sha256: str
    retrieved_at: datetime
    age_at_use_seconds: float = Field(ge=0)
    sources: list[WeatherSourceRunResponse]


class WeatherLegResponse(BaseModel):
    freshness: Literal["current", "stale", "unused", "unknown"]
    uses: list[WeatherLegUseResponse]


class WeatherUseResponse(BaseModel):
    station_id: str
    issue_time: datetime
    past: WeatherLegResponse
    future: WeatherLegResponse


def _leg(use: WeatherUse | None, leg: Literal["past", "future"]) -> WeatherLegResponse:
    if use is None:
        return WeatherLegResponse(freshness="unknown", uses=[])
    fact = use.past if leg == "past" else use.future
    if isinstance(fact, PastWeatherUse):
        source_runs = sorted(
            {
                (row.source.value, row.source_run)
                for day in fact.days
                for row in day.samples
            }
        )
        reason = fact.refresh_outcome.reason
        snapshot_id, digest, retrieved = (
            str(fact.snapshot_id),
            fact.snapshot_sha256,
            fact.retrieved_at,
        )
    elif isinstance(fact, FutureWeatherUse):
        source_runs = [(fact.receipt.scope.source, fact.receipt.cycle_time)]
        reason = fact.refresh_attempt.outcome.reason
        snapshot_id, digest, retrieved = (
            None,
            fact.receipt.row_sha256,
            fact.receipt.validated_at,
        )
    else:
        return WeatherLegResponse(freshness="unused", uses=[])
    return WeatherLegResponse(
        freshness=fact.freshness.value,
        uses=[
            WeatherLegUseResponse(
                freshness=fact.freshness.value,
                failure_code=reason.value if reason is not None else None,
                failure_at=use.refresh_finished_at if reason is not None else None,
                snapshot_id=snapshot_id,
                snapshot_sha256=digest,
                retrieved_at=retrieved,
                age_at_use_seconds=(use.used_at - retrieved).total_seconds(),
                sources=[
                    WeatherSourceRunResponse(source=source, source_run=run)
                    for source, run in source_runs
                ],
            )
        ],
    )


def _summary(use: WeatherUse | DerivedWeatherUse) -> WeatherUseResponse:
    facts = (
        [item.weather_use for item in use.contributors]
        if isinstance(use, DerivedWeatherUse)
        else [use]
    )

    def aggregate(leg: Literal["past", "future"]) -> WeatherLegResponse:
        summaries = [_leg(fact, leg) for fact in facts]
        states = {summary.freshness for summary in summaries}
        freshness: Literal["current", "stale", "unused", "unknown"] = "unused"
        for state in ("current", "unknown", "stale"):
            if state in states:
                freshness = state
        return WeatherLegResponse(
            freshness=freshness,
            uses=[item for summary in summaries for item in summary.uses],
        )

    return WeatherUseResponse(
        station_id=str(use.station_id),
        issue_time=use.issue_time,
        past=aggregate("past"),
        future=aggregate("future"),
    )


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
        if type(version) is not int or version != 1:
            raise ValueError("unsupported weather evidence version")
        evidence = store.fetch_evidence(forecast_id)
        if evidence is None:
            raise ValueError("selected evidence missing")
        manifest = json.loads(evidence.manifest_json)
        if not isinstance(manifest, dict):
            raise ValueError("selected weather manifest is not an object")
        manifest = cast("dict[str, object]", manifest)
        if (
            manifest.get("forecast_id") != str(forecast_id)
            or manifest.get("station_id") != str(station_id)
            or manifest.get("issued_at") != issue_time.isoformat()
            or manifest.get("data_use") != data_use.value
        ):
            raise ValueError("selected weather parent identity mismatch")
        facts = restore_selected_weather(
            evidence, model_id=model_id, issue_time=issue_time
        )
        return _summary(facts[station_id])
    except Exception:
        raise HTTPException(503, "Selected weather evidence is unavailable") from None
