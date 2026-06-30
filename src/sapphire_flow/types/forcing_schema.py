"""Shared canonical forcing-schema contract (Plan 071 criterion 3).

A single declaration that every forcing producer (the MeteoSwiss open-data
reanalysis adapter today; future reanalysis adapters tomorrow) and every
consumer agrees on: the canonical parameter names, their units (anchored to the
SAP3 ForecastInterface canonical unit map), the spatial representation, and the
temporal resolution.

Units are ``ForecastInterface`` ``Unit`` members so they resolve through
``adapters.forecast_interface.fi_unit_to_canonical`` to SAP3 canonical strings
(``Unit.MM`` -> ``"mm"``, ``Unit.DEG_C`` -> ``"°C"``). Precipitation is an
*accumulation* (``mm``), never a rate (``mm/h``) and never the un-mapped
``mm/day``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sapphire_flow.adapters.forecast_interface import Unit
from sapphire_flow.types.enums import SpatialRepresentation


class ForcingResolution(Enum):
    DAILY = "daily"


@dataclass(frozen=True, kw_only=True, slots=True)
class ForcingSchema:
    parameters: frozenset[str]
    units: dict[str, Unit]
    spatial_representation: SpatialRepresentation
    resolution: ForcingResolution


CANONICAL_FORCING_SCHEMA = ForcingSchema(
    parameters=frozenset(
        {"precipitation", "temperature", "temperature_min", "temperature_max"}
    ),
    units={
        "precipitation": Unit.MM,
        "temperature": Unit.DEG_C,
        "temperature_min": Unit.DEG_C,
        "temperature_max": Unit.DEG_C,
    },
    spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
    resolution=ForcingResolution.DAILY,
)
