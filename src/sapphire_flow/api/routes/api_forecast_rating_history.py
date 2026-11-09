from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from sapphire_flow.api.forecast_rating_history import (
    ForecastRatingHistoryReader,
    get_rating_history_reader,
)
from sapphire_flow.types.forecast_rating_history import ForecastRatingHistory
from sapphire_flow.types.ids import ForecastId

router = APIRouter(prefix="/api/v1/forecasts", tags=["forecasts"])


@router.get("/{forecast_id}/rating-input-history", response_model=ForecastRatingHistory)
def rating_input_history(
    forecast_id: UUID,
    reader: ForecastRatingHistoryReader = Depends(get_rating_history_reader),
) -> ForecastRatingHistory:
    return reader.read(ForecastId(forecast_id))
