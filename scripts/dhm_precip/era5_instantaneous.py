"""Plan 191 task T2 — the ERA5-Land INSTANTANEOUS transform path (t2m:
K -> degC). D1: a SEPARATE module from `era5_deaccumulate.py`, not a
generalisation of it — `era5_deaccumulate.py` is not touched. An
instantaneous field needs no deaccumulation, no boundary context and no
packing/conservation accounting; merging the two paths behind a shared
strategy seam would put the 01 UTC accumulation-day reset one boolean away
from a field it does not apply to. See
`docs/plans/191-era5-land-instantaneous-t2m.md`.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import numpy as np

from scripts.dhm_precip.era5_deaccumulate import SchemaValidationResult
from scripts.dhm_precip.era5_errors import (
    Era5SchemaValidationError,
    Era5TransformFailedError,
    Era5UnitsMismatchError,
)
from scripts.dhm_precip.era5_request import (
    GRID_SPACING_DEG,
    expected_grid_shape,
    expected_units,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import xarray as xr

_KELVIN_TO_CELSIUS_OFFSET = 273.15
_OUTPUT_DTYPE = "float32"  # matches era5_deaccumulate._OUTPUT_DTYPE
_OUTPUT_DIMS = ("valid_time", "latitude", "longitude")
_HOUR = np.timedelta64(1, "h")
_GRID_TOLERANCE_DEG = 1e-6

# D7 (this plan) — an instantaneous field carries none of the accumulator's
# temporal-convention attrs: `period_ending_convention` describes how an
# accumulation window is closed, and `accumulation_rule` names which
# accumulation-day rule applied — neither concept exists for a field that
# was never accumulated. `units_conversion` is the instantaneous analogue
# of `accumulation_rule`: it names the transform actually applied (e.g.
# "kelvin_to_celsius_subtract_273.15"), so the product still traces back to
# what produced it. `transform_version`, `output_schema_version` and
# `source_dataset` are unchanged — they trace code version and provenance
# regardless of what kind of field is being transformed.
_REQUIRED_OUTPUT_ATTRS = (
    "transform_version",
    "output_schema_version",
    "source_dataset",
    "units_conversion",
)

_SOURCE_MONTHS_PER_YEAR = 12
"""D4 — a product year is assembled from exactly its twelve monthly raw
artifacts. Unlike the accumulator path (`era5_transform._product_year_window_ids`
plus its two boundary-context neighbours = 14), an instantaneous field needs
no previous/next-month context: there is no 01 UTC reset to close."""


def convert_kelvin_to_celsius(ds: xr.Dataset) -> xr.Dataset:
    """K -> degC for raw ERA5-Land `t2m`, guarded against double-conversion
    exactly like `era5_deaccumulate.convert_units` guards `tp`: the source
    variable is renamed away and dropped, so a second pass over the output
    fails the `'t2m' not in ds` check rather than re-applying the offset."""
    if "t2m" not in ds:
        raise Era5UnitsMismatchError(
            "expected raw instantaneous variable 't2m'; not found (already "
            "converted, or wrong input)"
        )
    units = str(ds["t2m"].attrs.get("units", "")).strip().lower()
    if units not in expected_units("2m_temperature"):
        raise Era5UnitsMismatchError(
            f"'t2m' units attribute is {units!r}, expected kelvin"
        )
    temperature = (ds["t2m"] - _KELVIN_TO_CELSIUS_OFFSET).astype(_OUTPUT_DTYPE)
    temperature.attrs = {"units": "degC"}
    ds = ds.assign(temperature=temperature)
    return ds.drop_vars(["t2m"])


def validate_instantaneous_schema(
    ds: xr.Dataset, *, expected_year: int, expected_area: tuple[int, int, int, int]
) -> SchemaValidationResult:
    """The instantaneous counterpart to
    `era5_deaccumulate.validate_output_schema`: the same generic
    axis/grid/attr checks (coords present, dims, dtype, required attrs,
    non-empty axis, no duplicate stamps, strictly hourly, exact
    calendar-year range, grid spacing/counts, no inf, not entirely NaN),
    over `temperature`/`degC` rather than `precipitation`/`mm`.

    Deliberately DOES NOT check non-negativity: temperature is legitimately
    negative across the study box, so a copied precipitation-style
    non-negativity check would reject valid data. Carries no packing,
    conservation or accumulation-rule checks — none of those apply to a
    field that was never accumulated."""
    if "temperature" not in ds:
        raise Era5SchemaValidationError("missing 'temperature' data variable")
    var = ds["temperature"]
    if str(var.attrs.get("units", "")) != "degC":
        raise Era5SchemaValidationError(
            f"'temperature' units {var.attrs.get('units')!r} != 'degC'"
        )
    for coord in ("valid_time", "latitude", "longitude"):
        if coord not in ds.coords and coord not in ds.dims:
            raise Era5SchemaValidationError(f"missing coordinate {coord!r}")
    if tuple(var.dims) != _OUTPUT_DIMS:
        raise Era5SchemaValidationError(
            f"'temperature' dims {tuple(var.dims)} != {_OUTPUT_DIMS}"
        )
    if str(var.dtype) != _OUTPUT_DTYPE:
        raise Era5SchemaValidationError(
            f"'temperature' dtype {var.dtype} != {_OUTPUT_DTYPE!r}"
        )
    missing_attrs = [a for a in _REQUIRED_OUTPUT_ATTRS if not str(ds.attrs.get(a, ""))]
    if missing_attrs:
        raise Era5SchemaValidationError(
            f"missing or blank required attrs: {missing_attrs}"
        )

    valid_time = ds["valid_time"].values
    if valid_time.size == 0:
        raise Era5SchemaValidationError("empty valid_time axis")
    if len(set(valid_time.tolist())) != valid_time.size:
        raise Era5SchemaValidationError("valid_time contains duplicate stamps")
    if valid_time.size > 1 and not np.all(np.diff(valid_time) == _HOUR):
        raise Era5SchemaValidationError(
            "valid_time is not strictly increasing and exactly hourly"
        )

    expected_start = np.datetime64(f"{expected_year:04d}-01-01T00:00:00")
    expected_end = np.datetime64(f"{expected_year:04d}-12-31T23:00:00")
    if valid_time[0] != expected_start or valid_time[-1] != expected_end:
        raise Era5SchemaValidationError(
            f"valid_time range {valid_time[0]}..{valid_time[-1]} != expected "
            f"{expected_start}..{expected_end}"
        )
    expected_count = int((expected_end - expected_start) / _HOUR) + 1
    if valid_time.size != expected_count:
        raise Era5SchemaValidationError(
            f"valid_time has {valid_time.size} stamps, expected {expected_count} "
            f"for calendar year {expected_year}"
        )

    north, west, south, east = expected_area
    lat = ds["latitude"].values
    lon = ds["longitude"].values

    expected_lat_count, expected_lon_count = expected_grid_shape(expected_area)
    if lat.size != expected_lat_count:
        raise Era5SchemaValidationError(
            f"latitude has {lat.size} points, expected exactly "
            f"{expected_lat_count} at {GRID_SPACING_DEG} deg spacing for box "
            f"{expected_area}"
        )
    if lon.size != expected_lon_count:
        raise Era5SchemaValidationError(
            f"longitude has {lon.size} points, expected exactly "
            f"{expected_lon_count} at {GRID_SPACING_DEG} deg spacing for box "
            f"{expected_area}"
        )
    if lat.size > 1 and not np.allclose(
        np.abs(np.diff(np.sort(lat))), GRID_SPACING_DEG, atol=_GRID_TOLERANCE_DEG
    ):
        raise Era5SchemaValidationError(
            f"latitude spacing is not uniformly {GRID_SPACING_DEG} deg"
        )
    if lon.size > 1 and not np.allclose(
        np.abs(np.diff(np.sort(lon))), GRID_SPACING_DEG, atol=_GRID_TOLERANCE_DEG
    ):
        raise Era5SchemaValidationError(
            f"longitude spacing is not uniformly {GRID_SPACING_DEG} deg"
        )
    if (
        lat.max() > north + _GRID_TOLERANCE_DEG
        or lat.min() < south - _GRID_TOLERANCE_DEG
    ):
        raise Era5SchemaValidationError("latitude outside requested box")
    if lon.max() > east + _GRID_TOLERANCE_DEG or lon.min() < west - _GRID_TOLERANCE_DEG:
        raise Era5SchemaValidationError("longitude outside requested box")

    values = var.values
    if bool(np.isinf(values).any()):
        raise Era5SchemaValidationError(
            "'temperature' contains infinite value(s); non-negativity is NOT "
            "checked here (temperature is legitimately negative)"
        )
    non_finite = int(np.isnan(values).sum())
    if non_finite == values.size:
        raise Era5SchemaValidationError("'temperature' field is entirely non-finite")
    return SchemaValidationResult(non_finite_cell_count=non_finite)


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def instantaneous_identity(
    *,
    raw_sha256s: Sequence[str],
    units_conversion: str,
    output_schema_version: str,
    transform_version: str,
    output_format: str,
    output_dtype: str,
    output_encoding: Mapping[str, object],
) -> str:
    """D3 — the instantaneous transform's own resume identity, over ONLY
    what it reads. `era5_manifest.transform_identity` hashes
    `accumulation_rule_id`, `packing_tolerance_mm`, `conservation_tolerance_m`
    and `units_factor` — a K->degC transform reads none of them (M-A5 rule
    P7a: an identity hashes only inputs that are actually READ), so this
    function's signature does not even accept them.

    D4 — enforced, not merely documented: `raw_sha256s` must be exactly the
    twelve monthly windows of the product year. An instantaneous field
    needs no boundary context, so a caller that hands in the two extra
    edge-window hashes the accumulator path reads gets a typed error here,
    rather than a silently-different-but-still-"valid" identity.
    """
    if len(raw_sha256s) != _SOURCE_MONTHS_PER_YEAR:
        raise Era5TransformFailedError(
            f"instantaneous_identity requires exactly {_SOURCE_MONTHS_PER_YEAR} "
            "source sha256s (one per calendar month of the product year — "
            f"D4: no boundary context); got {len(raw_sha256s)}"
        )
    canonical = _canonical_json(
        {
            "raw_sha256s": list(raw_sha256s),
            "units_conversion": units_conversion,
            "output_schema_version": output_schema_version,
            "transform_version": transform_version,
            "output_format": output_format,
            "output_dtype": output_dtype,
            "output_encoding": dict(output_encoding),
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
