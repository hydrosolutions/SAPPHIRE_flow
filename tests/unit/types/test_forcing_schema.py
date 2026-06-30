"""Acceptance tests for the shared canonical forcing-schema contract.

Milestone 071-reanalysis-core, criterion 3 (the declaration half + the
mm/day rejection). The conformance half — "the adapter's output conforms
to it" — lives in
``tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py``.

These tests are LOCKED acceptance tests authored ahead of implementation.
They assert the *contract* (canonical variable names, FI-enum units anchored
to the SAP3 ForecastInterface canonical unit map, daily resolution, basin-
average spatial representation) without pinning incidental implementation
shape. Do not weaken them to make implementation easier.
"""

from __future__ import annotations

import pytest
from sapphire_flow.types.forcing_schema import CANONICAL_FORCING_SCHEMA

from sapphire_flow.adapters.forecast_interface import Unit, fi_unit_to_canonical
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import SpatialRepresentation

_CANONICAL_PARAMETERS = {
    "precipitation",
    "temperature",
    "temperature_min",
    "temperature_max",
}


def _resolution_token(resolution: object) -> str:
    # Tolerant of either a plain string token or an enum carrying a
    # ``.value`` / ``.name``. Pins the *daily* semantic, not the type.
    token = getattr(resolution, "value", None)
    if not isinstance(token, str):
        token = getattr(resolution, "name", resolution)
    return str(token).lower()


class TestCanonicalForcingSchemaDeclaration:
    def test_declares_exactly_the_canonical_parameters(self) -> None:
        assert set(CANONICAL_FORCING_SCHEMA.parameters) == _CANONICAL_PARAMETERS

    def test_precipitation_unit_is_fi_millimetre(self) -> None:
        assert CANONICAL_FORCING_SCHEMA.units["precipitation"] == Unit.MM

    @pytest.mark.parametrize(
        "parameter", ["temperature", "temperature_min", "temperature_max"]
    )
    def test_temperature_units_are_fi_degree_celsius(self, parameter: str) -> None:
        assert CANONICAL_FORCING_SCHEMA.units[parameter] == Unit.DEG_C

    def test_units_cover_every_declared_parameter(self) -> None:
        assert set(CANONICAL_FORCING_SCHEMA.units) == set(
            CANONICAL_FORCING_SCHEMA.parameters
        )

    def test_spatial_representation_is_basin_average(self) -> None:
        assert (
            CANONICAL_FORCING_SCHEMA.spatial_representation
            == SpatialRepresentation.BASIN_AVERAGE
        )

    def test_resolution_is_daily(self) -> None:
        assert _resolution_token(CANONICAL_FORCING_SCHEMA.resolution) == "daily"

    def test_declared_units_resolve_to_sap3_canonical_strings(self) -> None:
        # Anchors the schema's units to the ForecastInterface canonical unit
        # map: every declared unit must be a recognised SAP3 canonical unit.
        canonical = {
            param: fi_unit_to_canonical(unit)
            for param, unit in CANONICAL_FORCING_SCHEMA.units.items()
        }
        assert canonical["precipitation"] == "mm"
        assert canonical["temperature"] == "°C"
        assert canonical["temperature_min"] == "°C"
        assert canonical["temperature_max"] == "°C"


class TestPrecipitationUnitRejectsMmPerDay:
    def test_mm_per_day_is_not_a_sap3_canonical_unit(self) -> None:
        # mm/day is deliberately absent from the SAP3 ForecastInterface
        # canonical unit map; converting it must raise rather than silently
        # produce a string.
        with pytest.raises(ConfigurationError):
            fi_unit_to_canonical(Unit.MM_PER_DAY)

    def test_schema_does_not_declare_precipitation_in_mm_per_day(self) -> None:
        assert CANONICAL_FORCING_SCHEMA.units["precipitation"] != Unit.MM_PER_DAY
