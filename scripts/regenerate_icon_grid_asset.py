"""Regenerate the committed ICON-CH2-EPS static mesh-coordinate asset (Plan 087).

One-shot regeneration recipe: parse the MeteoSwiss OGD horizontal-constants GRIB2
(``horizontal_constants_icon-ch2-eps.grib2``, a collection-level STATIC asset on
``ch.meteoschweiz.ogd-forecasting-icon-ch2``) into the compact
``src/sapphire_flow/data/icon_ch2_eps_grid.npz`` package asset shipped in the wheel.

The mesh is FIXED across cycles, so the coordinates are static and derived ONCE.
The runtime never reads GRIB/ecCodes — only ``np.load`` of this asset.

HARD INVARIANT (Plan 087 Task 1c / D13): the recipe asserts
``uuidOfHGrid == bbbd5a09855499243c7a4aa4c8762920`` (ICON's definitive
horizontal-grid identity) and REFUSES to write on mismatch. This guarantees the
``tlat``/``tlon`` cell ordering matches the forecast GRIBs' ``values`` ordering by
construction. cfgrib drops ``uuidOfHGrid`` from the runtime cube, so the ordering
correspondence cannot be self-verified at runtime — it is pinned HERE, at
regeneration. A future ICON grid revision (new UUID) MUST fail LOUDLY here rather
than silently corrupting every basin mean.

Source / licence: MeteoSwiss / Federal Office of Meteorology and Climatology
MeteoSwiss, Open Government Data, CC-BY 4.0.

Usage::

    uv run python scripts/regenerate_icon_grid_asset.py \
        path/to/horizontal_constants_icon-ch2-eps.grib2
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from eccodes import (
    codes_get,
    codes_get_array,
    codes_get_string,
    codes_grib_new_from_file,
    codes_release,
)

_EXPECTED_UUID = "bbbd5a09855499243c7a4aa4c8762920"
_EXPECTED_POINTS = 283876
_WANTED_SHORTNAMES = ("tlat", "tlon", "h")

_ASSET_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "sapphire_flow"
    / "data"
    / "icon_ch2_eps_grid.npz"
)


def _read_grib(grib_path: Path) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    with grib_path.open("rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                uuid = codes_get_string(gid, "uuidOfHGrid")
                if uuid != _EXPECTED_UUID:
                    raise ValueError(
                        f"uuidOfHGrid mismatch: got {uuid!r}, expected "
                        f"{_EXPECTED_UUID!r}. This is a different ICON horizontal "
                        "grid — the tlat/tlon ordering would NOT match the forecast "
                        "GRIBs. Refusing to write the asset (Plan 087 D13/Task 1c)."
                    )
                n = codes_get(gid, "numberOfDataPoints")
                if n != _EXPECTED_POINTS:
                    raise ValueError(
                        f"numberOfDataPoints {n} != expected {_EXPECTED_POINTS}"
                    )
                short_name = codes_get_string(gid, "shortName")
                if short_name in _WANTED_SHORTNAMES:
                    arrays[short_name] = codes_get_array(gid, "values").astype(
                        np.float32
                    )
            finally:
                codes_release(gid)
    return arrays


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        print("error: exactly one argument (the GRIB path) is required")
        return 2

    grib_path = Path(argv[1])
    if not grib_path.is_file():
        print(f"error: GRIB file not found: {grib_path}")
        return 2

    arrays = _read_grib(grib_path)

    missing = [s for s in _WANTED_SHORTNAMES if s not in arrays]
    if missing:
        raise ValueError(f"GRIB is missing required shortName(s): {missing}")

    for name, arr in arrays.items():
        if arr.shape != (_EXPECTED_POINTS,):
            raise ValueError(
                f"{name} has shape {arr.shape}, expected ({_EXPECTED_POINTS},)"
            )
        if arr.dtype != np.float32:
            raise ValueError(f"{name} dtype is {arr.dtype}, expected float32")

    _ASSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        _ASSET_PATH,
        tlat=arrays["tlat"],
        tlon=arrays["tlon"],
        h=arrays["h"],
    )
    print(
        f"wrote {_ASSET_PATH} "
        f"(tlat/tlon/h float32, {_EXPECTED_POINTS} cells, uuidOfHGrid={_EXPECTED_UUID})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
