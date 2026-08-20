"""Plan 191 task T2 — the ERA5-Land INSTANTANEOUS transform path (t2m:
K -> degC), locked red-first.

Two properties are proven RED-FIRST against a deliberately naive first
draft of `era5_instantaneous.py` before the real guards were added (see the
implementer's report for the transcript — matching
`tests/unit/scripts/test_era5_deaccumulate.py`'s convention, a red run is
proven, not committed):

  1. `test_requires_exactly_twelve_source_hashes` — D4: an
     `instantaneous_identity` built over 14 source hashes (12 product months
     + 2 accumulator-only boundary windows) must be REJECTED, not merely
     produce a different-looking hash. Against the naive draft (no D4 count
     check) this failed with "DID NOT RAISE" — the naive identity silently
     accepted the 14-hash list.
  2. `test_kelvin_dataset_converted_twice_raises_not_minus_273` — a Kelvin
     dataset run through `convert_kelvin_to_celsius` twice must raise, not
     silently land near -273 degC. Against the naive draft (mutates `t2m`
     in place, never renames to `temperature`) this failed with a `KeyError`
     on the first call's `converted["temperature"]` lookup — the naive
     draft never implemented the D2 rename/drop contract at all, which is
     itself the mechanism the real guard relies on.

`TestNaiveCandidateHasTeeth` below is a permanent, separate regression
guard (mirroring `test_era5_deaccumulate.py`'s `_naive_deaccumulate`): it
runs a deliberately guardless K->degC candidate (in-place, no rename) twice
and proves it silently drifts to ~-273 degC — the concrete danger the real
guard exists to prevent.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.era5_errors import (
    Era5SchemaValidationError,
    Era5TransformFailedError,
    Era5UnitsMismatchError,
)
from scripts.dhm_precip.era5_instantaneous import (
    convert_kelvin_to_celsius,
    instantaneous_identity,
    validate_instantaneous_schema,
)

_HOUR = np.timedelta64(1, "h")
_SMALL_AREA = (26.2, 85.0, 26.0, 85.2)  # north, west, south, east — 3x3 grid
_KELVIN_TO_CELSIUS_OFFSET = 273.15


def _naive_convert_kelvin_to_celsius_no_guard(ds: xr.Dataset) -> xr.Dataset:
    """A deliberately buggy candidate, kept permanently (mirroring
    `test_era5_deaccumulate.py`'s `_naive_deaccumulate` convention): applies
    the K->degC offset IN PLACE on `t2m`, without renaming/dropping the
    source variable and without any double-conversion guard. This is
    exactly what happens if `convert_kelvin_to_celsius`'s guard is ever
    removed — proof the guard exists for a real, demonstrated failure mode
    (silently landing near -273 degC), not a hypothetical one."""
    ds = ds.copy()
    ds["t2m"] = ds["t2m"] - _KELVIN_TO_CELSIUS_OFFSET
    return ds


def _kelvin_raw_dataset(
    *,
    hours: int = 4,
    k_value: float = 300.15,
    units: str = "K",
    lat: tuple[float, ...] = (26.0, 26.1),
    lon: tuple[float, ...] = (85.0, 85.1),
) -> xr.Dataset:
    valid_time = np.datetime64("2021-07-01T00:00") + np.arange(hours) * _HOUR
    values = np.full((hours, len(lat), len(lon)), k_value, dtype=np.float32)
    ds = xr.Dataset(
        {"t2m": (["valid_time", "latitude", "longitude"], values)},
        coords={
            "valid_time": valid_time,
            "latitude": np.array(lat),
            "longitude": np.array(lon),
        },
    )
    ds["t2m"].attrs["units"] = units
    return ds


class TestNaiveCandidateHasTeeth:
    """Permanent regression guard: proves the guardless candidate genuinely
    drifts to ~-273 degC on a double conversion, so the real
    `convert_kelvin_to_celsius` guard is demonstrably necessary, not
    decorative."""

    def test_naive_candidate_silently_drifts_to_minus_273_on_double_conversion(
        self,
    ) -> None:
        ds = _kelvin_raw_dataset(k_value=273.0)  # ~0 degC
        once = _naive_convert_kelvin_to_celsius_no_guard(ds)
        np.testing.assert_allclose(once["t2m"].values, -0.15, atol=1e-4)
        twice = _naive_convert_kelvin_to_celsius_no_guard(once)
        np.testing.assert_allclose(twice["t2m"].values, -273.3, atol=1e-4)


class TestConvertKelvinToCelsius:
    def test_missing_variable_rejected(self) -> None:
        ds = _kelvin_raw_dataset().drop_vars(["t2m"])
        with pytest.raises(Era5UnitsMismatchError, match="t2m"):
            convert_kelvin_to_celsius(ds)

    def test_wrong_units_rejected(self) -> None:
        ds = _kelvin_raw_dataset(units="degC")
        with pytest.raises(Era5UnitsMismatchError, match="units"):
            convert_kelvin_to_celsius(ds)

    def test_kelvin_alias_case_insensitive(self) -> None:
        ds = _kelvin_raw_dataset(units="Kelvin")
        converted = convert_kelvin_to_celsius(ds)
        assert "temperature" in converted

    def test_scales_renames_and_drops_source(self) -> None:
        ds = _kelvin_raw_dataset(k_value=300.15)
        converted = convert_kelvin_to_celsius(ds)
        assert "t2m" not in converted
        assert "temperature" in converted
        assert converted["temperature"].attrs["units"] == "degC"
        np.testing.assert_allclose(converted["temperature"].values, 27.0, atol=1e-4)

    def test_output_dtype_is_float32(self) -> None:
        ds = _kelvin_raw_dataset()
        converted = convert_kelvin_to_celsius(ds)
        assert str(converted["temperature"].dtype) == "float32"

    def test_kelvin_dataset_converted_twice_raises_not_minus_273(self) -> None:
        """RED-FIRST #2 — see module docstring."""
        ds = _kelvin_raw_dataset(k_value=273.0)  # ~0 degC
        converted = convert_kelvin_to_celsius(ds)
        np.testing.assert_allclose(converted["temperature"].values, -0.15, atol=1e-4)
        with pytest.raises(Era5UnitsMismatchError):
            convert_kelvin_to_celsius(converted)


class TestValidateInstantaneousSchema:
    def _valid_year_dataset(
        self, year: int, *, area: tuple[float, float, float, float] = _SMALL_AREA
    ) -> xr.Dataset:
        from scripts.dhm_precip.era5_request import expected_grid_shape

        start = np.datetime64(f"{year:04d}-01-01T00:00")
        hours = (
            366 * 24
            if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            else 365 * 24
        )
        valid_time = start + np.arange(hours) * _HOUR
        north, west, south, east = area
        lat_count, lon_count = expected_grid_shape(area)
        lat = np.round(np.linspace(south, north, lat_count), 10)
        lon = np.round(np.linspace(west, east, lon_count), 10)
        temperature = np.full((hours, lat_count, lon_count), 15.0, dtype=np.float32)
        ds = xr.Dataset(
            {"temperature": (["valid_time", "latitude", "longitude"], temperature)},
            coords={"valid_time": valid_time, "latitude": lat, "longitude": lon},
        )
        ds["temperature"].attrs["units"] = "degC"
        ds.attrs.update(
            {
                "transform_version": "1",
                "output_schema_version": "1",
                "source_dataset": "reanalysis-era5-land",
                "units_conversion": "kelvin_to_celsius_subtract_273.15",
            }
        )
        return ds

    def test_valid_dataset_passes(self) -> None:
        ds = self._valid_year_dataset(2021)
        result = validate_instantaneous_schema(
            ds, expected_year=2021, expected_area=_SMALL_AREA
        )
        assert result.non_finite_cell_count == 0

    def test_wrong_units_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["temperature"].attrs["units"] = "K"
        with pytest.raises(Era5SchemaValidationError, match="units"):
            validate_instantaneous_schema(
                ds, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_wrong_year_coverage_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        with pytest.raises(Era5SchemaValidationError):
            validate_instantaneous_schema(
                ds, expected_year=2022, expected_area=_SMALL_AREA
            )

    def test_leap_year_count_accepted(self) -> None:
        ds = self._valid_year_dataset(2020)
        result = validate_instantaneous_schema(
            ds, expected_year=2020, expected_area=_SMALL_AREA
        )
        assert result.non_finite_cell_count == 0

    def test_fully_nan_field_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["temperature"].values[:] = np.nan
        with pytest.raises(Era5SchemaValidationError, match="non-finite"):
            validate_instantaneous_schema(
                ds, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_partial_nan_is_recorded_not_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["temperature"].values[0, 0, 0] = np.nan
        result = validate_instantaneous_schema(
            ds, expected_year=2021, expected_area=_SMALL_AREA
        )
        assert result.non_finite_cell_count == 1

    def test_infinite_value_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["temperature"].values[0, 0, 0] = np.inf
        with pytest.raises(Era5SchemaValidationError, match="infinite"):
            validate_instantaneous_schema(
                ds, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_spatial_subset_grid_is_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        subset = ds.isel(latitude=slice(0, 2), longitude=slice(0, 2))
        with pytest.raises(Era5SchemaValidationError, match="latitude"):
            validate_instantaneous_schema(
                subset, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_wrong_dtype_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["temperature"] = ds["temperature"].astype(np.float64)
        with pytest.raises(Era5SchemaValidationError, match="dtype"):
            validate_instantaneous_schema(
                ds, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_wrong_dims_order_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        transposed = ds.transpose("latitude", "valid_time", "longitude")
        with pytest.raises(Era5SchemaValidationError, match="dims"):
            validate_instantaneous_schema(
                transposed, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_missing_required_attr_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        del ds.attrs["units_conversion"]
        with pytest.raises(Era5SchemaValidationError, match="units_conversion"):
            validate_instantaneous_schema(
                ds, expected_year=2021, expected_area=_SMALL_AREA
            )

    def test_accumulator_only_attrs_are_not_required(self) -> None:
        """D9's accumulator required-attrs tuple (period_ending_convention,
        accumulation_rule) is meaningless for an instantaneous field — a
        dataset lacking BOTH of them must still validate, proving the
        instantaneous schema does not silently inherit them."""
        ds = self._valid_year_dataset(2021)
        assert "period_ending_convention" not in ds.attrs
        assert "accumulation_rule" not in ds.attrs
        result = validate_instantaneous_schema(
            ds, expected_year=2021, expected_area=_SMALL_AREA
        )
        assert result.non_finite_cell_count == 0

    def test_negative_temperature_survives_validation(self) -> None:
        """The non-negativity trap: temperature is legitimately negative in
        the Himalaya. A copied precipitation-style non-negativity check
        would wrongly reject this."""
        ds = self._valid_year_dataset(2021)
        ds["temperature"].values[:] = -40.0
        result = validate_instantaneous_schema(
            ds, expected_year=2021, expected_area=_SMALL_AREA
        )
        assert result.non_finite_cell_count == 0


def _twelve_sha256s() -> list[str]:
    return [format(i, "064x") for i in range(12)]


def _fourteen_sha256s_with_boundary_windows() -> list[str]:
    """Twelve product-month hashes plus the two D4 boundary-context hashes
    (`2019-12-31`, `2026-01-01T00` style edge windows) that
    `era5_transform.transform_year` reads for the ACCUMULATOR path but the
    instantaneous path must never consume."""
    months = [format(i, "064x") for i in range(12)]
    boundary = [format(200 + i, "064x") for i in range(2)]
    return [*boundary[:1], *months, *boundary[1:]]


def _identity_kwargs() -> dict[str, object]:
    return {
        "raw_sha256s": _twelve_sha256s(),
        "units_conversion": "kelvin_to_celsius_subtract_273.15",
        "output_schema_version": "1",
        "transform_version": "1",
        "output_format": "netcdf4_h5netcdf",
        "output_dtype": "float32",
        "output_encoding": {
            "temperature": {"dtype": "float32", "zlib": True, "complevel": 4},
            "valid_time": {
                "units": "hours since 1970-01-01 00:00:00",
                "dtype": "int64",
            },
        },
    }


class TestInstantaneousIdentity:
    def test_requires_exactly_twelve_source_hashes(self) -> None:
        """RED-FIRST #1 — see module docstring."""
        kwargs = _identity_kwargs()
        kwargs["raw_sha256s"] = _fourteen_sha256s_with_boundary_windows()
        with pytest.raises(Era5TransformFailedError, match="twelve|12"):
            instantaneous_identity(**kwargs)

    def test_too_few_source_hashes_also_rejected(self) -> None:
        kwargs = _identity_kwargs()
        kwargs["raw_sha256s"] = _twelve_sha256s()[:11]
        with pytest.raises(Era5TransformFailedError):
            instantaneous_identity(**kwargs)

    def test_exactly_twelve_source_hashes_accepted(self) -> None:
        kwargs = _identity_kwargs()
        instantaneous_identity(**kwargs)  # must not raise

    def test_changes_when_transform_version_bumps(self) -> None:
        kwargs = _identity_kwargs()
        id_a = instantaneous_identity(**kwargs)
        kwargs["transform_version"] = "2"
        id_b = instantaneous_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_output_schema_version_bumps(self) -> None:
        kwargs = _identity_kwargs()
        id_a = instantaneous_identity(**kwargs)
        kwargs["output_schema_version"] = "2"
        id_b = instantaneous_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_units_conversion_changes(self) -> None:
        kwargs = _identity_kwargs()
        id_a = instantaneous_identity(**kwargs)
        kwargs["units_conversion"] = "kelvin_to_celsius_offset_v2"
        id_b = instantaneous_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_raw_sha256s_change(self) -> None:
        kwargs = _identity_kwargs()
        id_a = instantaneous_identity(**kwargs)
        replaced = _twelve_sha256s()
        replaced[0] = "f" * 64
        kwargs["raw_sha256s"] = replaced
        id_b = instantaneous_identity(**kwargs)
        assert id_a != id_b

    def test_stable_under_encoding_key_ordering(self) -> None:
        kwargs_a = _identity_kwargs()
        kwargs_b = _identity_kwargs()
        kwargs_b["output_encoding"] = {
            "valid_time": {
                "dtype": "int64",
                "units": "hours since 1970-01-01 00:00:00",
            },
            "temperature": {
                "complevel": 4,
                "zlib": True,
                "dtype": "float32",
            },
        }
        assert instantaneous_identity(**kwargs_a) == instantaneous_identity(**kwargs_b)

    def test_signature_rejects_accumulator_only_parameters(self) -> None:
        """D3 — a K->degC transform reads none of `transform_identity`'s
        accumulator parameters; the function must not even ACCEPT them."""
        kwargs = _identity_kwargs()
        kwargs["accumulation_rule_id"] = "era5_land_01_00_accumulation_day_v1"
        with pytest.raises(TypeError):
            instantaneous_identity(**kwargs)
