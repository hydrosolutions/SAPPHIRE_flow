"""Plan 159 T1 — the `mm/day` <-> `m³/s` boundary SAP3 cannot express.

`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py`) deliberately omits
`MM_PER_DAY`, with a comment saying so, and `fi_unit_to_canonical` raises for it.
That omission is correct: a depth per day is not a flow, and turning one into the
other needs the catchment **area**. A bare map entry would be numerically wrong for
every basin.

So the conversion lives here, in the shim, where the area is available as one of the
model's own declared statics.

For a catchment of area ``A`` km²::

    1 mm/day over A km² = (A * 1e6 m²) * (1e-3 m) / 86400 s = A / 86.4  m³/s

so ``m³/s = mm_per_day * A / 86.4``. The constant is exact, not fitted.

Missing or invalid area is an anticipated per-station input problem. The dedicated
`AreaConversionError` lets predict/hindcast report explicit FI variable failures
without discarding healthy stations. Only total inbound failure becomes
`ModelFailure`; unrelated configuration errors and training errors still raise.
"""

from __future__ import annotations

import math
from typing import Final

from sapphire_flow.exceptions import ConfigurationError

# km² * mm/day -> m³/s. Exact: (1e6 m²/km²) * (1e-3 m/mm) / (86400 s/day) == 1/86.4.
_MM_DAY_KM2_TO_M3_S: Final[float] = 1e6 * 1e-3 / 86400.0


class AreaConversionError(ConfigurationError):
    """A station's `area` static is missing, non-numeric, non-finite, or
    non-positive — an anticipated per-station INPUT_DATA failure, not a programming
    bug. The shim maps only this specific error to FI input-failure outcomes."""


def _require_area(area_km2: object, *, station: str) -> float:
    """Area is a *divisor* in one direction, so a zero or non-finite value must fail
    loudly rather than yield ``inf``/``nan`` discharge. ``area`` is one of the model's
    declared statics, so a missing one is a data-availability problem for THAT
    station, surfaced by raising here and converted to an FI failure outcome at
    the `predict`/`hindcast` boundary (`_shim.py`)."""
    if not isinstance(area_km2, int | float) or isinstance(area_km2, bool):
        raise AreaConversionError(
            f"station {station!r}: catchment area must be numeric for the "
            f"mm/day <-> m³/s conversion, got {area_km2!r}"
        )
    if not math.isfinite(area_km2) or area_km2 <= 0.0:
        raise AreaConversionError(
            f"station {station!r}: catchment area must be finite and positive for "
            f"the mm/day <-> m³/s conversion, got {area_km2!r}"
        )
    return float(area_km2)


def mm_per_day_to_m3_per_s(value: float, *, area_km2: float, station: str) -> float:
    """aquacast's declared discharge unit -> SAP3's canonical `m³/s`."""
    return value * _require_area(area_km2, station=station) * _MM_DAY_KM2_TO_M3_S


def m3_per_s_to_mm_per_day(value: float, *, area_km2: float, station: str) -> float:
    """SAP3's canonical `m³/s` -> aquacast's declared discharge unit."""
    return value / (_require_area(area_km2, station=station) * _MM_DAY_KM2_TO_M3_S)
