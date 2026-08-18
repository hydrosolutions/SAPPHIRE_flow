"""Plan 182 (M-A10) — normalised diurnal profiles, the D7.2 zeroing ablation,
and the D3 common-retained-timestamp pairing.

Imports are guarded so a not-yet-implemented module fails these tests as a
genuine RED assertion, never a collection-time ImportError.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

try:
    from scripts.dhm_precip.stats_coloc import (
        NonPositiveGrandMeanError,
        common_retained_frame,
        dhm_utc_to_npt,
        normalised_diurnal_profile,
        paired_wet_hour_fraction,
        peak_hour,
        zero_below_threshold,
    )
except ImportError:
    NonPositiveGrandMeanError = None  # type: ignore[assignment]
    common_retained_frame = None  # type: ignore[assignment]
    dhm_utc_to_npt = None  # type: ignore[assignment]
    normalised_diurnal_profile = None  # type: ignore[assignment]
    paired_wet_hour_fraction = None  # type: ignore[assignment]
    peak_hour = None  # type: ignore[assignment]
    zero_below_threshold = None  # type: ignore[assignment]

from scripts.dhm_precip.domain_types import Station

_STATION = Station("Lukla Airport")
_START = datetime(2021, 7, 1, 0, 0)


def _frame(hours: list[int], values: list[float], *, days: int = 20) -> pl.DataFrame:
    """One row per (day, hour), station fixed. `values` gives the value at
    each `hours[i]` for EVERY day, so the profile is stable and easy to
    reason about."""
    rows: list[dict[str, object]] = []
    for day in range(days):
        for hour, value in zip(hours, values, strict=True):
            rows.append(
                {
                    "station": _STATION,
                    "timestamp": _START + timedelta(days=day, hours=hour),
                    "value_mm": value,
                }
            )
    return pl.DataFrame(rows)


class TestZeroBelowThreshold:
    def test_zeroes_not_drops(self) -> None:
        """D7.2 — the ablation ZEROES sub-threshold values; it must never
        drop the row (row count is unchanged)."""
        assert zero_below_threshold is not None, "zero_below_threshold not implemented"
        frame = _frame([2, 14], [0.05, 5.0], days=2)
        result = zero_below_threshold(frame, 0.2)
        assert result.height == frame.height
        assert set(result["value_mm"].to_list()) == {0.0, 5.0}

    def test_leaves_null_as_null(self) -> None:
        assert zero_below_threshold is not None, "zero_below_threshold not implemented"
        frame = pl.DataFrame(
            {
                "station": [_STATION],
                "timestamp": [_START],
                "value_mm": [None],
            }
        )
        result = zero_below_threshold(frame, 0.2)
        assert result["value_mm"].is_null()[0]


class TestAblationDetectsConcentratedNotUniformNoise:
    """D7.2/round-2 finding: 'the daily mean changes but as a COMMON SCALAR
    cannot move the peak.' Sub-threshold mass spread EVENLY across every
    hour is a common scalar and must not move the peak; sub-threshold mass
    CONCENTRATED at one hour is not a common scalar and (if large enough to
    dominate) must move the peak. This is the mechanism the whole D7 ladder
    depends on to distinguish H1 (noise-floor contamination) from H0."""

    def test_hour_uniform_subthreshold_noise_does_not_move_the_peak(self) -> None:
        assert zero_below_threshold is not None, "zero_below_threshold not implemented"
        assert normalised_diurnal_profile is not None, (
            "normalised_diurnal_profile not implemented"
        )
        assert peak_hour is not None, "peak_hour not implemented"
        hours = list(range(24))
        # Real signal peaks at hour 14 (5.0 mm); every hour ALSO carries the
        # SAME 0.05 mm sub-threshold noise floor — a common scalar.
        values = [0.05 + (5.0 if h == 14 else 0.0) for h in hours]
        frame = _frame(hours, values)

        all_values_peak = peak_hour(normalised_diurnal_profile(frame), station=_STATION)
        ablated = zero_below_threshold(frame, 0.2)
        matched_peak = peak_hour(normalised_diurnal_profile(ablated), station=_STATION)
        assert all_values_peak == 14
        assert matched_peak == 14

    def test_hour_concentrated_subthreshold_noise_moves_the_peak(self) -> None:
        assert zero_below_threshold is not None, "zero_below_threshold not implemented"
        assert normalised_diurnal_profile is not None, (
            "normalised_diurnal_profile not implemented"
        )
        assert peak_hour is not None, "peak_hour not implemented"
        days = 20
        rows: list[dict[str, object]] = []
        for day in range(days):
            for hour in range(24):
                if hour == 2:
                    # Sub-threshold (0.15 < 0.2 mm) resolution-level noise,
                    # present EVERY day -> mean 0.15.
                    value = 0.15
                elif hour == 14 and day == 0:
                    # A single genuine rain event, ONE day out of 20 ->
                    # mean 2.0/20 = 0.10. Rarer than the noise, so the
                    # UNABLATED mean profile is dominated by the noise hour,
                    # not the real one — exactly H1's claimed mechanism.
                    value = 2.0
                else:
                    value = 0.0
                rows.append(
                    {
                        "station": _STATION,
                        "timestamp": _START + timedelta(days=day, hours=hour),
                        "value_mm": value,
                    }
                )
        frame = pl.DataFrame(rows)

        all_values_peak = peak_hour(normalised_diurnal_profile(frame), station=_STATION)
        ablated = zero_below_threshold(frame, 0.2)
        matched_peak = peak_hour(normalised_diurnal_profile(ablated), station=_STATION)
        assert all_values_peak == 2
        assert matched_peak == 14


class TestCommonRetainedFrame:
    def test_pairs_on_common_timestamps_only(self) -> None:
        assert common_retained_frame is not None, (
            "common_retained_frame not implemented"
        )
        dhm = pl.DataFrame(
            {
                "station": [_STATION] * 3,
                "timestamp": [
                    _START,
                    _START + timedelta(hours=1),
                    _START + timedelta(hours=2),
                ],
                "value_mm": [1.0, 2.0, 3.0],
            }
        )
        pyramid = pl.DataFrame(
            {
                "station": ["AWS3 Lukla"] * 2,
                "timestamp": [_START, _START + timedelta(hours=2)],
                "value_mm": [0.2, 0.4],
            }
        )
        result = common_retained_frame(dhm, pyramid)
        assert result.n_dhm_retained == 3
        assert result.n_pyramid_retained == 2
        assert result.n_common_retained == 2
        assert sorted(result.paired["timestamp"].to_list()) == [
            _START,
            _START + timedelta(hours=2),
        ]

    def test_pairs_across_the_two_loaders_own_timestamp_precisions(self) -> None:
        """The production seam: DHM comes from `pl.read_excel`, which yields
        `Datetime('ms')`, and Pyramid from `pl.read_csv`, which yields
        `Datetime('us')`. Joining them raw raises `SchemaError`, so no test
        that builds both sides at one precision can reach the real
        pairing."""
        assert common_retained_frame is not None, (
            "common_retained_frame not implemented"
        )
        timestamps = [_START, _START + timedelta(hours=1), _START + timedelta(hours=2)]
        dhm = pl.DataFrame(
            {
                "station": [_STATION] * 3,
                "timestamp": timestamps,
                "value_mm": [1.0, 2.0, 3.0],
            },
            schema_overrides={"timestamp": pl.Datetime("ms")},
        )
        pyramid = pl.DataFrame(
            {
                "station": ["AWS3 Lukla"] * 2,
                "timestamp": [timestamps[0], timestamps[2]],
                "value_mm": [0.2, 0.4],
            },
            schema_overrides={"timestamp": pl.Datetime("us")},
        )

        result = common_retained_frame(dhm, pyramid)

        assert result.n_common_retained == 2
        assert sorted(result.paired["timestamp"].to_list()) == [
            timestamps[0],
            timestamps[2],
        ]


class TestPairedWetHourFractionPreventsAsymmetricMaskingArtefact:
    """D3, the review-driven correction: 'asymmetric masking MANUFACTURES the
    very gap we would read as evidence.' This locks the fix by construction:
    DHM and Pyramid observe the IDENTICAL wet/dry pattern (co-located, same
    reality) at 20 timestamps, but a QC mask preferentially strips DHM's DRY
    hours (as M-A3's dominant zero-run population does), leaving DHM's OWN
    retained population skewed wet. Computed on EACH side's own independently
    retained population, this manufactures a fake gap. Computed on the PAIRED
    common-retained population (D3's fix), the gap must vanish, because both
    sides are evaluated over the identical timestamp set."""

    def test_paired_fraction_has_no_gap_when_underlying_pattern_is_identical(
        self,
    ) -> None:
        assert common_retained_frame is not None, (
            "common_retained_frame not implemented"
        )
        assert paired_wet_hour_fraction is not None, (
            "paired_wet_hour_fraction not implemented"
        )
        timestamps = [_START + timedelta(hours=i) for i in range(20)]
        # First 10 timestamps wet (0.5 mm both sides), last 10 dry (0.0 both
        # sides) — DHM and Pyramid report the SAME truth at every timestamp.
        values = [0.5] * 10 + [0.0] * 10

        dhm_all = pl.DataFrame(
            {
                "station": [_STATION] * 20,
                "timestamp": timestamps,
                "value_mm": values,
            }
        )
        pyramid_all = pl.DataFrame(
            {
                "station": ["AWS3 Lukla"] * 20,
                "timestamp": timestamps,
                "value_mm": values,
            }
        )

        # M-A3-style mask: drop 8 of the 10 DRY DHM hours (indices 12-19),
        # keep all 10 wet + 2 dry -> DHM's OWN retained fraction = 10/12.
        dhm_retained = dhm_all.filter(~pl.col("timestamp").is_in(timestamps[12:20]))
        # Pyramid has no such masking: all 20 retained -> its OWN retained
        # fraction = 10/20 = 0.5, purely from the population mismatch.
        pyramid_retained = pyramid_all

        naive_dhm_fraction = (
            dhm_retained.filter(pl.col("value_mm") >= 0.2).height / dhm_retained.height
        )
        naive_pyramid_fraction = (
            pyramid_retained.filter(pl.col("value_mm") >= 0.2).height
            / pyramid_retained.height
        )
        # The artefact this test exists to catch: computed on each side's
        # own retained population, a large fake gap appears even though the
        # underlying weather was identical.
        assert naive_dhm_fraction - naive_pyramid_fraction > 0.3

        paired = common_retained_frame(dhm_retained, pyramid_retained)
        result = paired_wet_hour_fraction(paired.paired, wet_threshold_mm=0.2)
        assert result.n_common_retained == 12
        assert result.dhm_wet_fraction == result.pyramid_wet_fraction


class TestDhmUtcToNpt:
    """D2 — the UTC->NPT reconciliation `coloc_adjudication` applies to
    every DHM frame before it touches anything else in this module."""

    def test_shifts_every_timestamp_by_the_hour_offset(self) -> None:
        assert dhm_utc_to_npt is not None, "dhm_utc_to_npt not implemented"
        frame = pl.DataFrame(
            {
                "station": [_STATION],
                "timestamp": [datetime(2021, 7, 1, 2, 0)],
                "value_mm": [1.0],
            }
        )
        result = dhm_utc_to_npt(frame, hour_offset=6, jjas_months=(6, 7, 8, 9))
        assert result["timestamp"].to_list() == [datetime(2021, 7, 1, 8, 0)]

    def test_reapplies_jjas_filter_after_the_shift(self) -> None:
        """A UTC-JJAS hour can cross a calendar-month boundary once shifted
        into NPT — 30 Sept 23:00 UTC + 6h -> 1 Oct 05:00 NPT, no longer
        JJAS on the timebase everything else is compared on, and must be
        dropped rather than silently retained."""
        assert dhm_utc_to_npt is not None, "dhm_utc_to_npt not implemented"
        frame = pl.DataFrame(
            {
                "station": [_STATION, _STATION],
                "timestamp": [
                    datetime(2021, 9, 30, 23, 0),  # crosses out of JJAS
                    datetime(2021, 7, 1, 2, 0),  # stays well inside JJAS
                ],
                "value_mm": [1.0, 2.0],
            }
        )
        result = dhm_utc_to_npt(frame, hour_offset=6, jjas_months=(6, 7, 8, 9))
        assert result.height == 1
        assert result["timestamp"].to_list() == [datetime(2021, 7, 1, 8, 0)]

    def test_never_touches_a_pyramid_frame_shape_it_is_not_given(self) -> None:
        """Sanity check: calling it with `hour_offset=0` is a pure identity
        shift plus a JJAS re-filter — never anything else — so the
        function has no hidden network-specific behaviour beyond the shift
        it's told to apply."""
        assert dhm_utc_to_npt is not None, "dhm_utc_to_npt not implemented"
        frame = pl.DataFrame(
            {
                "station": [_STATION],
                "timestamp": [datetime(2021, 7, 1, 2, 0)],
                "value_mm": [1.0],
            }
        )
        result = dhm_utc_to_npt(frame, hour_offset=0, jjas_months=(6, 7, 8, 9))
        assert result["timestamp"].to_list() == frame["timestamp"].to_list()


class TestNormalisedDiurnalProfileRejectsNonPositiveGrandMean:
    """A zero (or non-finite) grand mean would otherwise silently divide
    into an infinite/NaN `normalised_value`, and `peak_hour`'s sort would
    then pick an arbitrary hour instead of reporting "no usable signal"."""

    def test_all_zero_values_raises_rather_than_dividing_by_zero(self) -> None:
        assert normalised_diurnal_profile is not None, (
            "normalised_diurnal_profile not implemented"
        )
        assert NonPositiveGrandMeanError is not None, (
            "NonPositiveGrandMeanError not implemented"
        )
        frame = _frame([2, 14], [0.0, 0.0], days=3)
        with pytest.raises(NonPositiveGrandMeanError):
            normalised_diurnal_profile(frame)
