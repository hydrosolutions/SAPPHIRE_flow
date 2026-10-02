from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import ForecastDataUse


class ForecastReadUnavailableError(ConfigurationError):
    def __init__(self) -> None:
        super().__init__("Ordinary forecast reads are unavailable")


def require_standard_forecast_store(store: object) -> None:
    if getattr(store, "data_use", None) is not ForecastDataUse.STANDARD:
        raise ForecastReadUnavailableError()


def require_standard_forecast_results(results: Iterable[object]) -> None:
    for result in results:
        if getattr(result, "data_use", None) is not ForecastDataUse.STANDARD:
            raise ForecastReadUnavailableError()
