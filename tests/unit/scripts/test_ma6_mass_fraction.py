"""Plan 184 (M-A6) task T5 — the sub-freezing mass fraction.

Seam tests: assertions land on VALUES actually produced (a fraction, a
count, a raised exception) — the same discipline `test_ma6_estimands.py`
and `test_ma6_pairs.py` state for T1/T3.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import DatumReconciliationStatus, Station
from scripts.dhm_precip.ma6_estimands import (
    BandMembershipError,
    DuplicateBandMemberError,
    ElevationBand,
    EmptySubsetError,
    EstimandSubsetTypeError,
    MixedSelectionParamsError,
    ScaleNotSupportedError,
    conditional_accumulated_difference,
    matched_hour_mean_difference,
)
from scripts.dhm_precip.ma6_mass_fraction import (
    LAPSE_SENSITIVITY_DEGC_PER_KM,
    PRIMARY_LAPSE_RATE_DEGC_PER_KM,
    PRIMARY_THRESHOLD_DEGC,
    THRESHOLD_SENSITIVITY_DEGC,
    DuplicateTimestampError,
    MassFractionFrameSchemaError,
    StationElevationInputs,
    StationSetMismatchError,
    SubFreezingMassFraction,
    ZeroClassifiableMassError,
    ZeroMassError,
    build_sub_freezing_mass_fraction,
    elevation_band_mass_fraction,
    sensitivity_combinations,
    sub_freezing_mass_fraction_sensitivity,
)
from scripts.dhm_precip.ma6_pairs import PairedSeries, Scale, subset
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from collections.abc import Sequence

_STATION_A = Station("A")
_STATION_B = Station("B")

# Elevation is chosen so lapse_correct_to_station_degc's ADDED correction is
# exactly 0.0 at the primary 6.5 degC/km rate — every test below therefore
# reasons about grid_t2m_degc directly, not about an offset it would
# otherwise have to carry through arithmetic.
_ELEVATION_ZERO_CORRECTION = StationElevationInputs(
    station_elev_m=1000.0,
    orography_elev_m=1000.0,
    datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
)


def _paired_frame(rows: list[dict[str, object]], *, station: Station) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("ms")),
        pl.lit(str(station)).alias("station"),
    )


def _make_paired_series(
    station: Station,
    start: datetime,
    gauge: list[float],
    era5: list[float],
) -> PairedSeries:
    timestamps = [start + timedelta(hours=i) for i in range(len(gauge))]
    frame = _paired_frame(
        [
            {
                "timestamp": timestamps[i],
                "gauge_value_mm": gauge[i],
                "era5_nearest_mm_per_h": era5[i],
            }
            for i in range(len(gauge))
        ],
        station=station,
    )
    return PairedSeries(frame=frame)


def _t2m_frame(
    station: Station,
    start: datetime,
    temps: Sequence[float | None],
) -> pl.DataFrame:
    timestamps = [start + timedelta(hours=i) for i in range(len(temps))]
    rows = [
        (ts, temp)
        for ts, temp in zip(timestamps, temps, strict=True)
        if temp is not None
    ]
    return pl.DataFrame(
        {
            "station": [str(station)] * len(rows),
            "timestamp": [ts for ts, _ in rows],
            "grid_t2m_degc": [t for _, t in rows],
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


_START = datetime(2024, 1, 1, 0, tzinfo=UTC)


def _jjas_start() -> datetime:
    return datetime(2024, 7, 1, 0, tzinfo=UTC)


class TestSensitivityCombinations:
    def test_returns_exactly_five_combinations(self) -> None:
        combos = sensitivity_combinations()

        assert len(combos) == 5

    def test_includes_the_shared_primary(self) -> None:
        combos = sensitivity_combinations()

        assert (PRIMARY_THRESHOLD_DEGC, PRIMARY_LAPSE_RATE_DEGC_PER_KM) in combos

    def test_covers_every_threshold_and_lapse_axis_value(self) -> None:
        combos = sensitivity_combinations()

        thresholds = {t for t, _lapse in combos}
        lapses = {lapse for _t, lapse in combos}

        assert thresholds == set(THRESHOLD_SENSITIVITY_DEGC)
        assert lapses == {
            PRIMARY_LAPSE_RATE_DEGC_PER_KM,
            *LAPSE_SENSITIVITY_DEGC_PER_KM,
        }

    def test_is_not_the_nine_combination_cross_product(self) -> None:
        # Pin 3: one-at-a-time, never the full 3x3 grid a naive
        # itertools.product(THRESHOLD_SENSITIVITY_DEGC,
        # LAPSE_SENSITIVITY_DEGC_PER_KM) would produce.
        combos = sensitivity_combinations()

        assert len(combos) != 9


class TestSubFreezingMassFractionBasics:
    def test_mass_weighted_fraction_over_finite_temperatures(self) -> None:
        # 4 hours, gauge mass [10, 20, 30, 40] mm; temps put hours 0,1 below
        # 1.5 degC and hours 2,3 above it (zero-correction elevation pair).
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[10.0, 20.0, 30.0, 40.0], era5=[0.0] * 4
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, [-1.0, 0.5, 2.0, 5.0])

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.total_mass_mm == pytest.approx(100.0)
        assert result.classifiable_mass_mm == pytest.approx(100.0)
        assert result.unclassifiable_mass_mm == pytest.approx(0.0)
        assert result.unclassifiable_mass_share == pytest.approx(0.0)
        assert result.sub_freezing_mass_mm == pytest.approx(30.0)  # 10 + 20
        assert result.sub_freezing_mass_fraction == pytest.approx(0.30)

    def test_station_scale_n_are_derived_from_subset(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[1.0, 2.0], era5=[0.0, 0.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, [1.0, 1.0])

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.station == _STATION_A
        assert result.scale is Scale.DAILY
        assert result.n == s.n_common_retained == 2

    def test_lapse_correction_is_applied_not_the_raw_grid_value(self) -> None:
        # orography above station by 1000 m => +6.5 degC correction at the
        # standard rate. A raw grid value of -5.0 degC (sub-freezing raw)
        # becomes +1.5 degC corrected — NOT below the 1.5 threshold.
        elevation = StationElevationInputs(
            station_elev_m=0.0,
            orography_elev_m=1000.0,
            datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
        )
        paired = _make_paired_series(_STATION_A, _START, gauge=[10.0], era5=[0.0])
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, [-5.0])

        result = SubFreezingMassFraction(
            subset=s,
            t2m_frame=t2m,
            elevation=elevation,
            threshold_degc=1.5,
            lapse_rate_degc_per_km=6.5,
        )

        assert result.sub_freezing_mass_fraction == pytest.approx(0.0)

    def test_mismatched_timestamp_dtypes_between_gauge_and_t2m_still_join(
        self,
    ) -> None:
        # Real data: gauge timestamps come from pl.read_excel (Datetime
        # "ms"); the published t2m series comes from xarray/netCDF
        # (Datetime "ns"). The join must not require the caller to
        # pre-align these -- found by the T5 gated real-data run
        # (2026-08-26), where an uncast join raised polars SchemaError.
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[10.0, 20.0], era5=[0.0, 0.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        assert s.frame.schema["timestamp"] == pl.Datetime("ms")
        t2m = pl.DataFrame(
            {
                "station": [str(_STATION_A)] * 2,
                "timestamp": [_START, _START + timedelta(hours=1)],
                "grid_t2m_degc": [-1.0, 5.0],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ns")))
        assert t2m.schema["timestamp"] == pl.Datetime("ns")

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.total_mass_mm == pytest.approx(30.0)
        assert result.unclassifiable_mass_share == pytest.approx(0.0)
        assert result.sub_freezing_mass_fraction == pytest.approx(10.0 / 30.0)


class TestUnclassifiableMass:
    def test_missing_t2m_hour_counts_as_unclassifiable_never_dropped(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[10.0, 20.0, 30.0], era5=[0.0] * 3
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        # hour 1 (20.0 mm) has no t2m row at all.
        t2m = _t2m_frame(_STATION_A, _START, [-1.0, None, 5.0])

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.total_mass_mm == pytest.approx(60.0)
        assert result.classifiable_mass_mm == pytest.approx(40.0)
        assert result.unclassifiable_mass_mm == pytest.approx(20.0)
        assert result.unclassifiable_mass_share == pytest.approx(20.0 / 60.0)
        # The fraction is over the CLASSIFIABLE denominator only.
        assert result.sub_freezing_mass_fraction == pytest.approx(10.0 / 40.0)

    def test_non_finite_t2m_value_counts_as_unclassifiable(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[10.0, 10.0], era5=[0.0, 0.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = pl.DataFrame(
            {
                "station": [str(_STATION_A)] * 2,
                "timestamp": [_START, _START + timedelta(hours=1)],
                "grid_t2m_degc": [float("nan"), 5.0],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.classifiable_mass_mm == pytest.approx(10.0)
        assert result.unclassifiable_mass_mm == pytest.approx(10.0)

    def test_zero_total_mass_raises_zero_mass_error(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[0.0, 0.0], era5=[0.0, 0.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, [1.0, 1.0])

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        with pytest.raises(ZeroMassError, match="zero total gauge mass"):
            _ = result.unclassifiable_mass_share

    def test_all_unclassifiable_raises_zero_classifiable_mass_error(self) -> None:
        # A row present for every hour (so station identity is verifiable)
        # but every t2m value is non-finite -- everything is unclassifiable,
        # never zero rows (which is a different, identity-ambiguous case
        # covered by the schema/mismatch guards instead).
        paired = _make_paired_series(_STATION_A, _START, gauge=[10.0], era5=[0.0])
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = pl.DataFrame(
            {
                "station": [str(_STATION_A)],
                "timestamp": [_START],
                "grid_t2m_degc": [float("nan")],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.unclassifiable_mass_share == pytest.approx(1.0)
        with pytest.raises(ZeroClassifiableMassError, match="unclassifiable"):
            _ = result.sub_freezing_mass_fraction


class TestUnclassifiableMassNeverNegative:
    """`unclassifiable_mass_mm` was originally computed as
    `total_mass_mm - classifiable_mass_mm` — two separate floating-point
    sums over the same `gauge_value_mm` column that, on real data, are
    each accumulated in a different order (the second one is a `filter()`
    result). Their difference is not exactly 0.0 even when every hour is
    classifiable: measured on the real archive, 9 of 52 station/scale
    combinations returned a NEGATIVE unclassifiable mass, worst
    -9.094947017729282e-13 mm (Lukla Airport JJAS). A small fixture will
    NOT reproduce this -- float non-associativity needs enough values to
    accumulate a visible drift, so this uses a few thousand varied
    magnitudes."""

    def test_all_classifiable_over_many_rows_is_exactly_zero_never_negative(
        self,
    ) -> None:
        rng = random.Random(20260826)
        n = 4000
        gauge = [rng.uniform(0.0, 25.0) for _ in range(n)]
        # Every hour classifiable: temps stay finite, split across the
        # threshold so classifiable_mass_mm is a nontrivial (not merely
        # all-or-nothing) partial sum -- the shape that actually drifted
        # on the real archive.
        temps = [rng.uniform(-10.0, 10.0) for _ in range(n)]

        paired = _make_paired_series(_STATION_A, _START, gauge=gauge, era5=[0.0] * n)
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, temps)

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.classifiable_mass_mm == pytest.approx(result.total_mass_mm)
        assert result.unclassifiable_mass_mm == 0.0
        assert result.unclassifiable_mass_share == 0.0

    def test_mixed_finite_and_non_finite_over_many_rows_matches_direct_sum(
        self,
    ) -> None:
        rng = random.Random(20260827)
        n = 3000
        gauge = [rng.uniform(0.0, 25.0) for _ in range(n)]
        # A genuinely non-finite corrected temperature on every 7th hour
        # (nan/inf mixed with missing rows), the rest finite.
        temps: list[float | None] = []
        expected_unclassifiable_mm = 0.0
        for i in range(n):
            if i % 7 == 0:
                temps.append(None)
                expected_unclassifiable_mm += gauge[i]
            elif i % 11 == 0:
                temps.append(float("nan"))
                expected_unclassifiable_mm += gauge[i]
            else:
                temps.append(rng.uniform(-10.0, 10.0))

        paired = _make_paired_series(_STATION_A, _START, gauge=gauge, era5=[0.0] * n)
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, temps)

        result = SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

        assert result.unclassifiable_mass_mm == pytest.approx(
            expected_unclassifiable_mm
        )
        assert result.unclassifiable_mass_mm > 0.0
        assert result.unclassifiable_mass_share == pytest.approx(
            expected_unclassifiable_mm / result.total_mass_mm
        )


class TestSubFreezingMassFractionGuards:
    def test_subset_must_be_a_real_paired_retained_subset(self) -> None:
        with pytest.raises(EstimandSubsetTypeError, match="PairedRetainedSubset"):
            SubFreezingMassFraction(
                subset="not a subset",  # type: ignore[arg-type]
                t2m_frame=_t2m_frame(_STATION_A, _START, [1.0]),
                elevation=_ELEVATION_ZERO_CORRECTION,
            )

    def test_t2m_frame_missing_columns_is_refused(self) -> None:
        paired = _make_paired_series(_STATION_A, _START, gauge=[1.0], era5=[0.0])
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        bad_frame = pl.DataFrame({"timestamp": [_START], "grid_t2m_degc": [1.0]})

        with pytest.raises(MassFractionFrameSchemaError, match="station"):
            SubFreezingMassFraction(
                subset=s, t2m_frame=bad_frame, elevation=_ELEVATION_ZERO_CORRECTION
            )

    def test_mismatched_station_between_subset_and_t2m_frame_is_refused(self) -> None:
        paired = _make_paired_series(_STATION_A, _START, gauge=[1.0], era5=[0.0])
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        wrong_station_t2m = _t2m_frame(_STATION_B, _START, [1.0])

        with pytest.raises(StationSetMismatchError, match="mismatched"):
            SubFreezingMassFraction(
                subset=s,
                t2m_frame=wrong_station_t2m,
                elevation=_ELEVATION_ZERO_CORRECTION,
            )

    def test_duplicate_timestamp_in_t2m_frame_is_refused(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[1.0, 2.0], era5=[0.0, 0.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        duplicated = pl.DataFrame(
            {
                "station": [str(_STATION_A)] * 2,
                "timestamp": [_START, _START],
                "grid_t2m_degc": [1.0, 2.0],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))

        with pytest.raises(DuplicateTimestampError, match="distinct timestamps"):
            SubFreezingMassFraction(
                subset=s, t2m_frame=duplicated, elevation=_ELEVATION_ZERO_CORRECTION
            )

    def test_non_finite_threshold_is_refused(self) -> None:
        paired = _make_paired_series(_STATION_A, _START, gauge=[1.0], era5=[0.0])
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(_STATION_A, _START, [1.0])

        with pytest.raises(ValueError, match="threshold_degc"):
            SubFreezingMassFraction(
                subset=s,
                t2m_frame=t2m,
                elevation=_ELEVATION_ZERO_CORRECTION,
                threshold_degc=float("nan"),
            )


class TestExit9SameSubsetAsMagnitude:
    """Exit 9 — a magnitude may never be quoted without both its `n` and
    its sub-freezing mass fraction in the SAME cell. Proven here by
    IDENTITY, not merely equality: `build_sub_freezing_mass_fraction`
    passes `estimand.subset` straight through."""

    def test_mass_fraction_subset_is_the_magnitudes_own_subset_object(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _jjas_start(), gauge=[10.0, 20.0], era5=[1.0, 2.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.JJAS, params=DEFAULT_PARAMS)  # noqa: FBT003
        magnitude = matched_hour_mean_difference(s)

        result = build_sub_freezing_mass_fraction(
            magnitude,
            t2m_by_station={
                _STATION_A: _t2m_frame(_STATION_A, _jjas_start(), [1.0, 1.0])
            },
            elevations_by_station={_STATION_A: _ELEVATION_ZERO_CORRECTION},
        )

        assert result.subset is magnitude.subset
        assert result.n == magnitude.n
        assert result.station == magnitude.station
        assert result.scale == magnitude.scale

    def test_missing_t2m_station_raises(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _jjas_start(), gauge=[10.0], era5=[1.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.JJAS, params=DEFAULT_PARAMS)  # noqa: FBT003
        magnitude = matched_hour_mean_difference(s)

        with pytest.raises(Exception, match="t2m"):
            build_sub_freezing_mass_fraction(
                magnitude, t2m_by_station={}, elevations_by_station={}
            )

    def test_sensitivity_helper_returns_five_members_of_the_same_subset(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _jjas_start(), gauge=[10.0, 20.0], era5=[1.0, 2.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.JJAS, params=DEFAULT_PARAMS)  # noqa: FBT003
        magnitude = matched_hour_mean_difference(s)

        results = sub_freezing_mass_fraction_sensitivity(
            magnitude,
            t2m_by_station={
                _STATION_A: _t2m_frame(_STATION_A, _jjas_start(), [1.0, 1.0])
            },
            elevations_by_station={_STATION_A: _ELEVATION_ZERO_CORRECTION},
        )

        assert len(results) == 5
        assert all(r.subset is magnitude.subset for r in results)


class TestElevationBandMassFraction:
    def _member(
        self, station: Station, start: datetime, gauge: list[float], temps: list[float]
    ) -> SubFreezingMassFraction:
        paired = _make_paired_series(
            station, start, gauge=gauge, era5=[0.0] * len(gauge)
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        t2m = _t2m_frame(station, start, temps)
        return SubFreezingMassFraction(
            subset=s, t2m_frame=t2m, elevation=_ELEVATION_ZERO_CORRECTION
        )

    def test_band_value_is_the_unweighted_mean_never_pooled_mass(self) -> None:
        # Station A: huge mass, entirely sub-freezing -> fraction 1.0.
        # Station B: tiny mass, entirely above threshold -> fraction 0.0.
        # A mass-POOLED fraction would be dominated by A (near 1.0); the
        # UNWEIGHTED MEAN pin 2 requires is exactly 0.5.
        member_a = self._member(
            _STATION_A, _START, gauge=[1000.0, 1000.0], temps=[-5.0, -5.0]
        )
        member_b = self._member(
            _STATION_B, _START, gauge=[1.0, 1.0], temps=[10.0, 10.0]
        )

        band = elevation_band_mass_fraction(
            ElevationBand.BELOW_700M,
            (member_a, member_b),
            station_elev_m={_STATION_A: 500.0, _STATION_B: 500.0},
        )

        assert band.mean_sub_freezing_mass_fraction == pytest.approx(0.5)
        assert band.station_count == 2
        assert band.member_ns == (2, 2)

    def test_duplicate_station_is_refused(self) -> None:
        member_a = self._member(_STATION_A, _START, gauge=[1.0], temps=[1.0])
        member_a_again = self._member(_STATION_A, _START, gauge=[2.0], temps=[1.0])

        with pytest.raises(DuplicateBandMemberError, match="repeated station"):
            elevation_band_mass_fraction(
                ElevationBand.BELOW_700M,
                (member_a, member_a_again),
                station_elev_m={_STATION_A: 500.0},
            )

    def test_mismatched_threshold_across_members_is_refused(self) -> None:
        paired_a = _make_paired_series(_STATION_A, _START, gauge=[1.0], era5=[0.0])
        s_a = subset(paired_a, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        member_a = SubFreezingMassFraction(
            subset=s_a,
            t2m_frame=_t2m_frame(_STATION_A, _START, [1.0]),
            elevation=_ELEVATION_ZERO_CORRECTION,
            threshold_degc=1.5,
        )
        paired_b = _make_paired_series(_STATION_B, _START, gauge=[1.0], era5=[0.0])
        s_b = subset(paired_b, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        member_b = SubFreezingMassFraction(
            subset=s_b,
            t2m_frame=_t2m_frame(_STATION_B, _START, [1.0]),
            elevation=_ELEVATION_ZERO_CORRECTION,
            threshold_degc=2.0,
        )

        with pytest.raises(MixedSelectionParamsError, match="threshold_degc"):
            elevation_band_mass_fraction(
                ElevationBand.BELOW_700M,
                (member_a, member_b),
                station_elev_m={_STATION_A: 500.0, _STATION_B: 500.0},
            )

    def test_band_membership_mismatch_is_refused(self) -> None:
        member_a = self._member(_STATION_A, _START, gauge=[1.0], temps=[1.0])

        with pytest.raises(BandMembershipError, match="belongs to"):
            elevation_band_mass_fraction(
                ElevationBand.ABOVE_3000M,
                (member_a,),
                station_elev_m={_STATION_A: 500.0},
            )

    def test_mixed_scale_across_members_is_refused(self) -> None:
        paired_a = _make_paired_series(
            _STATION_A, _jjas_start(), gauge=[1.0], era5=[0.0]
        )
        s_a = subset(paired_a, pl.lit(True), scale=Scale.JJAS, params=DEFAULT_PARAMS)  # noqa: FBT003
        member_a = SubFreezingMassFraction(
            subset=s_a,
            t2m_frame=_t2m_frame(_STATION_A, _jjas_start(), [1.0]),
            elevation=_ELEVATION_ZERO_CORRECTION,
        )
        paired_b = _make_paired_series(_STATION_B, _START, gauge=[1.0], era5=[0.0])
        s_b = subset(paired_b, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        member_b = SubFreezingMassFraction(
            subset=s_b,
            t2m_frame=_t2m_frame(_STATION_B, _START, [1.0]),
            elevation=_ELEVATION_ZERO_CORRECTION,
        )

        with pytest.raises(ScaleNotSupportedError, match="more than one scale"):
            elevation_band_mass_fraction(
                ElevationBand.BELOW_700M,
                (member_a, member_b),
                station_elev_m={_STATION_A: 500.0, _STATION_B: 500.0},
            )

    def test_empty_members_is_refused(self) -> None:
        with pytest.raises(EmptySubsetError, match="zero member stations"):
            elevation_band_mass_fraction(
                ElevationBand.BELOW_700M, (), station_elev_m={}
            )

    def test_station_missing_from_elevation_mapping_is_refused(self) -> None:
        member_a = self._member(_STATION_A, _START, gauge=[1.0], temps=[1.0])

        with pytest.raises(BandMembershipError, match="no known elevation"):
            elevation_band_mass_fraction(
                ElevationBand.BELOW_700M, (member_a,), station_elev_m={}
            )


class TestAppliesToAllFourMagnitudeKinds:
    """Pin 1 — `build_sub_freezing_mass_fraction` accepts any of D1's four
    magnitude kinds, since it only ever reads `estimand.subset`/
    `estimand.station`."""

    def test_conditional_accumulated_difference_gets_a_fraction_too(self) -> None:
        paired = _make_paired_series(
            _STATION_A, _START, gauge=[10.0, 20.0], era5=[1.0, 2.0]
        )
        s = subset(paired, pl.lit(True), scale=Scale.DAILY, params=DEFAULT_PARAMS)  # noqa: FBT003
        magnitude = conditional_accumulated_difference(s)

        result = build_sub_freezing_mass_fraction(
            magnitude,
            t2m_by_station={_STATION_A: _t2m_frame(_STATION_A, _START, [-1.0, 5.0])},
            elevations_by_station={_STATION_A: _ELEVATION_ZERO_CORRECTION},
        )

        assert result.n == magnitude.n
        assert result.subset is magnitude.subset


class TestNoGatingSurface:
    """⛔ The fraction ANNOTATES — it never gates, adjusts, or corrects
    anything (D4). This is a coarse contract test: no public callable on
    the module filters/adjusts a magnitude or a PairedSeries by a mass
    fraction."""

    def test_module_exposes_no_filter_or_correct_function(self) -> None:
        import scripts.dhm_precip.ma6_mass_fraction as mod

        public_names = [name for name in dir(mod) if not name.startswith("_")]
        forbidden_substrings = (
            "filter_by",
            "correct_precip",
            "adjust_precip",
            "rescale",
        )

        offending = [
            name
            for name in public_names
            for bad in forbidden_substrings
            if bad in name.lower()
        ]

        assert offending == []
