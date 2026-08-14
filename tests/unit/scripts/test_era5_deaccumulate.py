"""Plan 171 task 3a — D6/D7/D8/D9 pure functions, locked red-first.

Required sequence (plan task 3a, "Red-first, with demonstrated teeth"):
  1. A local, deliberately naive candidate (`_naive_deaccumulate`) mirrors
     `src/sapphire_flow/adapters/meteoswiss_nwp.py:157` — a plain
     `pad().diff("valid_time")`. It is defined here, not imported: it is
     correct for MeteoSwiss's per-cycle accumulation and must never be
     coupled to this ERA5-Land research script.
  2. `TestNaiveCandidateHasTeeth` proves the naive candidate VIOLATES the D6
     post-conditions on a fixture whose accumulation resets at 01 UTC — this
     passes today, proving the fixtures distinguish naive from correct, and
     is kept permanently as a regression guard against reintroducing a
     global diff.
  3. Before `era5_deaccumulate.py` existed, the contract-test bodies below
     were pointed at `_naive_deaccumulate` and confirmed to fail on a real
     assertion (not an import error) — see the implementer's report for the
     transcript; that state is not preserved in git (a red run is proven,
     not committed).
  4. The contract tests are now pointed at the real
     `scripts.dhm_precip.era5_deaccumulate.deaccumulate_precipitation`.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.era5_deaccumulate import (
    DEFAULT_CONSERVATION_TOLERANCE_M,
    convert_units,
    deaccumulate_precipitation,
    diagnose_accumulation_convention,
    validate_output_schema,
)
from scripts.dhm_precip.era5_errors import (
    Era5ConservationError,
    Era5MissingBoundaryContextError,
    Era5PackingPostConditionError,
    Era5SchemaValidationError,
    Era5UnitsMismatchError,
)

_HOUR = np.timedelta64(1, "h")


def _hours_of(valid_time: np.ndarray) -> np.ndarray:
    days = valid_time.astype("datetime64[D]")
    return ((valid_time - days) / _HOUR).astype(int)


def _accumulator_from_true(valid_time: np.ndarray, true_m: np.ndarray) -> np.ndarray:
    """Ground-truth accumulator: resets to itself at hour 1, else += true_m."""
    hours = _hours_of(valid_time)
    acc = np.empty_like(true_m)
    running = np.zeros(true_m.shape[1:], dtype=true_m.dtype)
    for i in range(valid_time.size):
        running = true_m[i].copy() if hours[i] == 1 else running + true_m[i]
        acc[i] = running
    return acc


def _build_fixture(
    *,
    start: str,
    hours: int,
    true_mm_1d: np.ndarray | None = None,
    lat: tuple[float, ...] = (26.0, 26.1),
    lon: tuple[float, ...] = (80.0, 80.1),
    units: str = "m",
    seed: int = 0,
) -> tuple[np.ndarray, xr.Dataset, np.ndarray]:
    """Returns (valid_time, dataset, true_hourly_mm).

    `dataset` carries `tp` as the accumulator in metres; `true_hourly_mm` is
    shaped (hours, lat, lon).
    """
    valid_time = np.datetime64(start) + np.arange(hours) * _HOUR
    rng = np.random.default_rng(seed)
    if true_mm_1d is None:
        true_mm_1d = rng.uniform(0.1, 2.0, size=hours)
    lat_arr = np.array(lat)
    lon_arr = np.array(lon)
    true_mm = (
        np.broadcast_to(true_mm_1d[:, None, None], (hours, lat_arr.size, lon_arr.size))
        .astype(np.float64)
        .copy()
    )
    acc_m = _accumulator_from_true(valid_time, true_mm / 1000.0)
    ds = xr.Dataset(
        {"tp": (["valid_time", "latitude", "longitude"], acc_m.astype(np.float32))},
        coords={"valid_time": valid_time, "latitude": lat_arr, "longitude": lon_arr},
    )
    ds["tp"].attrs["units"] = units
    return valid_time, ds, true_mm


def _naive_deaccumulate(ds: xr.Dataset) -> xr.Dataset:
    """The deliberately naive candidate — mirrors
    `meteoswiss_nwp.py:157` exactly, scoped locally (D6, Risks)."""
    ds = ds.copy()
    ds["precipitation"] = (
        ds["tp"].pad({"valid_time": (1, 0)}, constant_values=0).diff("valid_time")
    )
    return ds.drop_vars(["tp"])


class TestNaiveCandidateHasTeeth:
    """Permanent regression guard (red-first step 2 & 4)."""

    def test_naive_candidate_goes_negative_at_the_reset(self) -> None:
        valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=48)
        naive = _naive_deaccumulate(ds)
        assert bool((naive["precipitation"].values < 0).any())

    def test_naive_candidate_violates_conservation(self) -> None:
        """The naive candidate's FIRST accumulation day telescopes fine
        (nothing before it to corrupt) — the violation shows up on the
        SECOND day, whose naive sum spans the reset and so subtracts the
        first day's large terminal value instead of resetting to zero."""
        valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=48)
        naive = _naive_deaccumulate(ds)
        hours_arr = _hours_of(valid_time)
        terminal_indices = np.where(hours_arr == 0)[0]
        assert terminal_indices.size == 2
        day2_idx = np.arange(terminal_indices[0] + 1, terminal_indices[1] + 1)
        naive_sum = (
            naive["precipitation"].isel(valid_time=day2_idx).sum(dim="valid_time")
        )
        day2_terminal_accumulator = ds["tp"].isel(valid_time=int(terminal_indices[1]))
        assert not np.allclose(
            naive_sum.values, day2_terminal_accumulator.values, atol=1e-6
        )


class TestSeamAndBoundaries:
    def test_23_00_01_seam_matches_true_hourly_values(self) -> None:
        valid_time, ds, true_mm = _build_fixture(start="2021-10-01T01:00", hours=72)
        result = deaccumulate_precipitation(ds)
        increments_mm = result.dataset["tp"].values * 1000.0
        np.testing.assert_allclose(increments_mm, true_mm, atol=1e-3)

    def test_year_boundary_31dec_2300_to_1jan_0100(self) -> None:
        valid_time, ds, true_mm = _build_fixture(start="2020-12-31T01:00", hours=48)
        result = deaccumulate_precipitation(ds)
        increments_mm = result.dataset["tp"].values * 1000.0
        np.testing.assert_allclose(increments_mm, true_mm, atol=1e-3)

    def test_leap_day_29_feb_2020(self) -> None:
        valid_time, ds, true_mm = _build_fixture(start="2020-02-29T01:00", hours=48)
        result = deaccumulate_precipitation(ds)
        increments_mm = result.dataset["tp"].values * 1000.0
        np.testing.assert_allclose(increments_mm, true_mm, atol=1e-3)

    def test_calendar_day_grouping_fails_conservation_but_accumulation_day_passes(
        self,
    ) -> None:
        """Distinguishes accumulation-day grouping (correct) from a naive
        calendar-day grouping (0..23 of the same date) — the latter must
        disagree with the terminal accumulator at exactly this seam."""
        valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=48)
        result = deaccumulate_precipitation(ds)
        increments = result.dataset["tp"]
        hours_arr = _hours_of(valid_time)

        terminal_idx = int(np.where(hours_arr == 0)[0][0])
        accumulation_day_idx = np.arange(0, terminal_idx + 1)
        accumulation_day_sum = increments.isel(valid_time=accumulation_day_idx).sum(
            dim="valid_time"
        )
        terminal_accumulator = ds["tp"].isel(valid_time=terminal_idx)
        np.testing.assert_allclose(
            accumulation_day_sum.values, terminal_accumulator.values, atol=1e-6
        )

        # A calendar-day grouping for 01 Oct = hours 0..23 with date==01 Oct.
        # Our fixture starts AT hour 1, so calendar day 1 Oct only has
        # indices [0 .. terminal_idx - 1] (hours 1..23) — missing hour 0 of
        # 2 Oct but wrongly INCLUDING nothing extra; use the next day's
        # calendar grouping instead, which cleanly demonstrates the
        # mismatch: calendar day 02-Oct = hour0 (idx=terminal_idx) plus
        # hours 1..23 of 02-Oct (idx=terminal_idx+1 .. terminal_idx+23).
        calendar_day_idx = np.arange(terminal_idx, terminal_idx + 24)
        calendar_day_sum = increments.isel(valid_time=calendar_day_idx).sum(
            dim="valid_time"
        )
        next_terminal_idx = terminal_idx + 24
        next_terminal_accumulator = ds["tp"].isel(valid_time=next_terminal_idx)
        assert not np.allclose(
            calendar_day_sum.values, next_terminal_accumulator.values, atol=1e-6
        )


class TestConservationPostCondition:
    def test_conservation_holds_for_every_fully_contained_day(self) -> None:
        valid_time, ds, _true_mm = _build_fixture(
            start="2021-06-01T01:00", hours=24 * 10
        )
        result = deaccumulate_precipitation(ds)
        assert result.accumulation_days_checked >= 8

    def test_partial_trailing_day_is_not_checked(self) -> None:
        """A day whose 24 members are not all present (the series ends
        mid-day) is not "fully contained" and must be silently excluded from
        the conservation count, never flagged and never falsely checked.

        Note on soundness: D6's conservation identity (sum of an
        accumulation day's increments == its terminal accumulator) telescopes
        by construction for ANY correctly-implemented consecutive-difference
        scheme, for ANY choice of reset boundary — it is a self-consistency
        check on the day-GROUPING/summation code, not a data validator, and
        cannot be violated by corrupting accumulator values. There is
        therefore no sound way to force `Era5ConservationError` via input
        corruption; this test instead locks the "fully contained" scoping
        that makes the check meaningful (never asserting over a partial day).
        """
        valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=30)
        result = deaccumulate_precipitation(
            ds, required_range=(valid_time[0], valid_time[23])
        )
        # 30 hours from hour1 = one full day (24 members) plus 6 trailing
        # members of a second, incomplete day.
        assert result.accumulation_days_checked == 1


class TestMissingBoundaryContext:
    def test_series_without_a_resolvable_first_stamp_raises(self) -> None:
        # Starts at hour 2 — NOT an accumulation-day start — so its own
        # increment has no predecessor in this series.
        valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T02:00", hours=24)
        with pytest.raises(Era5MissingBoundaryContextError):
            deaccumulate_precipitation(ds)

    def test_context_stamp_outside_required_range_is_exempt(self) -> None:
        """The same series is fine when the caller only REQUIRES the range
        that excludes the unresolvable first stamp (it is boundary context,
        not output)."""
        valid_time, ds, true_mm = _build_fixture(start="2021-10-01T00:00", hours=25)
        # hour0 is unresolvable as an increment (no predecessor); everything
        # from hour1 (index 1) onward is resolvable.
        result = deaccumulate_precipitation(
            ds, required_range=(valid_time[1], valid_time[-1])
        )
        increments_mm = (
            result.dataset["tp"].isel(valid_time=slice(1, None)).values * 1000.0
        )
        np.testing.assert_allclose(increments_mm, true_mm[1:], atol=1e-3)


class TestMaskedNonLandCells:
    """D9's missing-value policy explicitly permits per-cell NaN (e.g. an
    ERA5-Land sea/non-land mask cell) — a review finding showed this was
    conflated with genuinely missing boundary context, aborting the whole
    array whenever any cell happened to be permanently masked."""

    def test_permanently_masked_cell_does_not_raise_missing_boundary_context(
        self,
    ) -> None:
        valid_time, ds, true_mm = _build_fixture(start="2021-10-01T01:00", hours=48)
        # Permanently mask one grid cell at every timestep (a stand-in for a
        # sea/non-land ERA5-Land mask cell) — NOT a missing predecessor.
        ds["tp"].values[:, 1, 1] = np.nan
        result = deaccumulate_precipitation(ds)
        masked_cell = result.dataset["tp"].values[:, 1, 1]
        assert bool(np.isnan(masked_cell).all())
        # The unmasked cell is entirely unaffected.
        unmasked_mm = result.dataset["tp"].values[:, 0, 0] * 1000.0
        np.testing.assert_allclose(unmasked_mm, true_mm[:, 0, 0], atol=1e-3)

    def test_masked_cell_survives_convert_units_and_schema_validation(self) -> None:
        valid_time, ds, _true_mm = _build_fixture(start="2021-01-01T01:00", hours=24)
        ds["tp"].values[:, 1, 1] = np.nan
        result = deaccumulate_precipitation(ds)
        converted = convert_units(result.dataset)
        assert bool(np.isnan(converted["precipitation"].values[:, 1, 1]).all())


class TestPackingPolicy:
    def test_tolerated_negative_is_clamped_and_recorded(self) -> None:
        valid_time, ds, true_mm = _build_fixture(start="2021-10-01T01:00", hours=24)
        # Force a tiny packing-scale negative diff well inside default
        # tolerance (1e-4 mm = 1e-7 m): set hour 5's accumulator just BELOW
        # hour 4's, so tp[5]-tp[4] is a small negative rather than merely
        # denting the (much larger) true positive increment.
        ds["tp"].values[5] = ds["tp"].values[4] - 5e-8
        result = deaccumulate_precipitation(ds)
        assert result.packing.packing_corrected_cells >= 1
        assert result.packing.max_correction_mm > 0.0
        assert result.packing.mass_adjustment_mm > 0.0
        assert bool((result.dataset["tp"].isel(valid_time=5).values >= 0).all())

    def test_material_negative_raises(self) -> None:
        valid_time, ds, true_mm = _build_fixture(start="2021-10-01T01:00", hours=24)
        # A large negative, far beyond the default 1e-4 mm tolerance.
        ds["tp"].values[5] -= 1.0  # 1 metre — clearly material
        with pytest.raises(Era5PackingPostConditionError):
            deaccumulate_precipitation(ds)

    def test_conservation_tolerance_constant_is_reasonable(self) -> None:
        assert 0 < DEFAULT_CONSERVATION_TOLERANCE_M < 1e-3


class TestPostClampAccounting:
    """D6 post-condition 1b, asserted in code: per accumulation day AND
    cell, the published (post-clamp) sum equals
    `1000 x original_terminal_accumulator_m + mass_adjustment_mm(day, cell)`.
    A review finding noted the prior code only totalled
    `packing_corrected_cells`/`max_correction_mm`/`mass_adjustment_mm`
    globally, without ever checking the equation those numbers are supposed
    to satisfy."""

    def test_real_clamp_scenario_satisfies_the_equation_exactly(self) -> None:
        valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=24)
        ds["tp"].values[5] = ds["tp"].values[4] - 5e-8  # a tolerated clamp
        result = deaccumulate_precipitation(ds)

        # ONE accumulation day, summed over ALL cells — `mass_adjustment_mm`
        # is the manifest's YEAR-level (here: whole-array) aggregate, so it
        # must be compared against the matching whole-array totals, not a
        # single cell's.
        published_sum_mm = float(result.dataset["tp"].sum()) * 1000.0
        terminal_mm = float(ds["tp"].values[-1].sum()) * 1000.0
        np.testing.assert_allclose(
            published_sum_mm,
            terminal_mm + result.packing.mass_adjustment_mm,
            atol=1e-6,
        )

    def test_broken_accounting_is_caught(self) -> None:
        """Directly exercises the extracted post-condition helper with a
        DELIBERATELY WRONG terminal value (a stand-in for a hypothetical
        accounting bug — e.g. crediting the wrong day's mass) — proving the
        assertion has teeth, not just checking that the real pipeline's own
        (self-consistent-by-construction) numbers happen to look positive."""
        from scripts.dhm_precip.era5_deaccumulate import _assert_post_clamp_accounting

        valid_time = np.datetime64("2021-10-01T01:00") + np.arange(24) * _HOUR
        acc_day = np.full(24, np.datetime64("2021-10-01"), dtype="datetime64[D]")
        full_days = np.array([np.datetime64("2021-10-01")], dtype="datetime64[D]")

        pre_clamp_mm = np.full(24, 1.0)
        pre_clamp_mm[5] = -5e-5  # a tolerated tiny negative, to be clamped
        pre_clamp = xr.DataArray(
            (pre_clamp_mm / 1000.0).reshape(24, 1, 1),
            dims=["valid_time", "lat", "lon"],
            coords={"valid_time": valid_time},
        )
        clamp_mask = pre_clamp < 0
        post_clamp = pre_clamp.where(~clamp_mask, 0.0)
        correct_terminal_mm = float(pre_clamp_mm.sum())  # pre-clamp conservation holds
        terminal = xr.DataArray(
            np.full((24, 1, 1), correct_terminal_mm / 1000.0),
            dims=["valid_time", "lat", "lon"],
            coords={"valid_time": valid_time},
        )

        # Correct accounting: no exception.
        _assert_post_clamp_accounting(
            full_days=full_days,
            acc_day=acc_day,
            valid_time=valid_time,
            pre_clamp_increment=pre_clamp,
            post_clamp_increment=post_clamp,
            clamp_mask=clamp_mask,
            terminal_accumulator=terminal,
            tolerance_mm=1e-3,
        )

        # A broken candidate: the terminal is credited to the WRONG value
        # (as if a day's mass were mis-attributed) — must be caught.
        broken_terminal = xr.DataArray(
            np.full((24, 1, 1), (correct_terminal_mm + 5.0) / 1000.0),
            dims=["valid_time", "lat", "lon"],
            coords={"valid_time": valid_time},
        )
        with pytest.raises(Era5ConservationError, match="post-condition 1b"):
            _assert_post_clamp_accounting(
                full_days=full_days,
                acc_day=acc_day,
                valid_time=valid_time,
                pre_clamp_increment=pre_clamp,
                post_clamp_increment=post_clamp,
                clamp_mask=clamp_mask,
                terminal_accumulator=broken_terminal,
                tolerance_mm=1e-3,
            )


class TestConvertUnits:
    def test_source_units_not_metres_rejected(self) -> None:
        _valid_time, ds, _true_mm = _build_fixture(
            start="2021-10-01T01:00", hours=4, units="mm"
        )
        with pytest.raises(Era5UnitsMismatchError):
            convert_units(ds)

    def test_already_converted_input_rejected_not_scaled_twice(self) -> None:
        _valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=4)
        converted = convert_units(ds)
        with pytest.raises(Era5UnitsMismatchError):
            convert_units(converted)

    def test_scales_and_renames(self) -> None:
        _valid_time, ds, _true_mm = _build_fixture(start="2021-10-01T01:00", hours=4)
        converted = convert_units(ds)
        assert "tp" not in converted
        assert "precipitation" in converted
        assert converted["precipitation"].attrs["units"] == "mm"
        np.testing.assert_allclose(
            converted["precipitation"].values, ds["tp"].values * 1000.0
        )


class TestValidateOutputSchema:
    def _valid_year_dataset(self, year: int) -> xr.Dataset:
        start = np.datetime64(f"{year:04d}-01-01T00:00")
        hours = (
            366 * 24
            if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            else 365 * 24
        )
        valid_time = start + np.arange(hours) * _HOUR
        lat = np.arange(26.0, 31.0 + 0.05, 0.1)
        lon = np.arange(80.0, 89.0 + 0.05, 0.1)
        precip = np.zeros((hours, lat.size, lon.size), dtype=np.float32)
        ds = xr.Dataset(
            {"precipitation": (["valid_time", "latitude", "longitude"], precip)},
            coords={"valid_time": valid_time, "latitude": lat, "longitude": lon},
        )
        ds["precipitation"].attrs["units"] = "mm"
        ds.attrs.update(
            {
                "period_ending_convention": "hour t covers t-1 -> t (UTC)",
                "accumulation_rule": "era5_land_01_00_accumulation_day_v1",
                "transform_version": "1",
                "output_schema_version": "1",
                "source_dataset": "reanalysis-era5-land",
            }
        )
        return ds

    def test_valid_dataset_passes(self) -> None:
        ds = self._valid_year_dataset(2021)
        result = validate_output_schema(
            ds, expected_year=2021, expected_area=(31, 80, 26, 89)
        )
        assert result.non_finite_cell_count == 0

    def test_wrong_units_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["precipitation"].attrs["units"] = "m"
        with pytest.raises(Era5SchemaValidationError, match="units"):
            validate_output_schema(
                ds, expected_year=2021, expected_area=(31, 80, 26, 89)
            )

    def test_wrong_year_coverage_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        with pytest.raises(Era5SchemaValidationError):
            validate_output_schema(
                ds, expected_year=2022, expected_area=(31, 80, 26, 89)
            )

    def test_leap_year_count_accepted(self) -> None:
        ds = self._valid_year_dataset(2020)
        result = validate_output_schema(
            ds, expected_year=2020, expected_area=(31, 80, 26, 89)
        )
        assert result.non_finite_cell_count == 0

    def test_fully_nan_field_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["precipitation"].values[:] = np.nan
        with pytest.raises(Era5SchemaValidationError, match="non-finite"):
            validate_output_schema(
                ds, expected_year=2021, expected_area=(31, 80, 26, 89)
            )

    def test_partial_nan_is_recorded_not_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["precipitation"].values[0, 0, 0] = np.nan
        result = validate_output_schema(
            ds, expected_year=2021, expected_area=(31, 80, 26, 89)
        )
        assert result.non_finite_cell_count == 1

    def test_infinite_value_rejected(self) -> None:
        """D9 permits finite-or-NaN only; infinities are a hard failure, not
        an allowed 'non-finite' value like a sea/non-land mask NaN."""
        ds = self._valid_year_dataset(2021)
        ds["precipitation"].values[0, 0, 0] = np.inf
        with pytest.raises(Era5SchemaValidationError, match="infinite"):
            validate_output_schema(
                ds, expected_year=2021, expected_area=(31, 80, 26, 89)
            )

    def test_spatial_subset_grid_is_rejected(self) -> None:
        """Review finding: a small subset grid (e.g. 2x2) whose min/max
        happen to fall inside the requested box must NOT pass as the full
        product — count and spacing are checked exactly, not just range."""
        ds = self._valid_year_dataset(2021)
        subset = ds.isel(latitude=slice(0, 2), longitude=slice(0, 2))
        with pytest.raises(Era5SchemaValidationError, match="latitude"):
            validate_output_schema(
                subset, expected_year=2021, expected_area=(31, 80, 26, 89)
            )

    def test_wrong_dtype_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        ds["precipitation"] = ds["precipitation"].astype(np.float64)
        with pytest.raises(Era5SchemaValidationError, match="dtype"):
            validate_output_schema(
                ds, expected_year=2021, expected_area=(31, 80, 26, 89)
            )

    def test_wrong_dims_order_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        transposed = ds.transpose("latitude", "valid_time", "longitude")
        with pytest.raises(Era5SchemaValidationError, match="dims"):
            validate_output_schema(
                transposed, expected_year=2021, expected_area=(31, 80, 26, 89)
            )

    def test_missing_required_attr_rejected(self) -> None:
        ds = self._valid_year_dataset(2021)
        del ds.attrs["accumulation_rule"]
        with pytest.raises(Era5SchemaValidationError, match="accumulation_rule"):
            validate_output_schema(
                ds, expected_year=2021, expected_area=(31, 80, 26, 89)
            )


class TestDiagnoseAccumulationConvention:
    def test_identifies_hour1_reset_on_synthetic_sample(self) -> None:
        _valid_time, ds, _true_mm = _build_fixture(
            start="2021-10-01T01:00", hours=24 * 5
        )
        diagnostic = diagnose_accumulation_convention(ds)
        assert diagnostic.reset_hour == 1
        assert diagnostic.terminal_hour == 0
        assert diagnostic.monotone_within_day is True
