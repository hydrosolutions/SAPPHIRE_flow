from __future__ import annotations

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ConfigurationError


@pytest.mark.parametrize(
    ("unit", "canonical"),
    [
        (fi_boundary.Unit.M3_PER_S, "m³/s"),
        (fi_boundary.Unit.MM, "mm"),
        (fi_boundary.Unit.CM, "cm"),
        (fi_boundary.Unit.M, "m"),
        (fi_boundary.Unit.DEG_C, "°C"),
        (fi_boundary.Unit.PERCENT, "%"),
        (fi_boundary.Unit.M_PER_S, "m/s"),
        (fi_boundary.Unit.W_PER_M2, "W/m²"),
        (fi_boundary.Unit.MM_PER_HOUR, "mm/h"),
    ],
)
def test_confirmed_fi_units_map_to_sap3_canonical_strings(
    unit: fi_boundary.Unit,
    canonical: str,
) -> None:
    assert fi_boundary.fi_unit_to_canonical(unit) == canonical


def test_unmapped_fi_unit_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match=r"Unit\.MM_PER_DAY"):
        fi_boundary.fi_unit_to_canonical(fi_boundary.Unit.MM_PER_DAY)
