from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError

from sapphire_flow.api.deps import get_observation_clock
from sapphire_flow.api.station_history import (
    StationHistoryReader,
    get_station_history_reader,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station_history import (
    HistoryResolution,
    StationHistoryRequest,
    StationHistoryResponse,
)

router = APIRouter(prefix="/api/v1/stations", tags=["stations"])


@router.get("/{station_id}/observation-history", response_model=StationHistoryResponse)
def observation_history(
    station_id: UUID,
    request: Request,
    parameter: Literal["discharge"],
    start: datetime,
    end: datetime,
    resolution: HistoryResolution = HistoryResolution.NATIVE,
    data_use: Annotated[
        list[Literal["standard", "provisional_rating"]] | None, Query()
    ] = None,
    reader: StationHistoryReader = Depends(get_station_history_reader),
    now: datetime = Depends(get_observation_clock),
) -> StationHistoryResponse:
    allowed = {"parameter", "start", "end", "resolution", "data_use"}
    keys = request.query_params
    if set(keys) - allowed or any(
        len(keys.getlist(k)) > 1 for k in keys if k != "data_use"
    ):
        raise RequestValidationError(
            [
                {
                    "loc": ("query",),
                    "msg": "Unknown or repeated history parameter",
                    "type": "value_error",
                }
            ]
        )
    try:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("History timestamps require a timezone")
        query = StationHistoryRequest(
            station_id=StationId(station_id),
            start=ensure_utc(start),
            end=ensure_utc(end),
            generated_at=ensure_utc(now),
            resolution=resolution,
            purposes=tuple(ForecastDataUse(p) for p in (data_use or ["standard"])),
        )
    except (ValueError, OverflowError):
        raise RequestValidationError(
            [
                {
                    "loc": ("query",),
                    "msg": "Invalid history interval or purpose selection",
                    "type": "value_error",
                }
            ]
        ) from None
    return reader.read(query)
