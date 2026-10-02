from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException

from sapphire_flow.services.forecast_read import (
    ForecastReadUnavailableError,
    require_standard_forecast_results,
    require_standard_forecast_store,
)
from sapphire_flow.types.enums import ForecastDataUse


def require_standard_store(store: object) -> None:
    try:
        require_standard_forecast_store(store)
    except ForecastReadUnavailableError:
        raise HTTPException(503, "Ordinary forecast reads are unavailable") from None


def require_standard_results(results: Iterable[object]) -> None:
    try:
        require_standard_forecast_results(results)
    except ForecastReadUnavailableError:
        raise HTTPException(503, "Ordinary forecast reads are unavailable") from None


def require_standard_detail(forecast: object) -> None:
    if getattr(forecast, "data_use", None) is not ForecastDataUse.STANDARD:
        raise HTTPException(404, "Forecast not found")
