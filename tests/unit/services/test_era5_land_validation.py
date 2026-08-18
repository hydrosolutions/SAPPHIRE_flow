"""Plan 183 T3 — ERA5-Land vs Caravan climate-index parity acceptance tests.

Red-first (Plan-105-safe): every test guards its import so a missing symbol
fails as a genuine assertion, never a collection-time ``ImportError``.

Formulas are Addor et al. (2017) hydrologic signatures (the basis for
Caravan's published indices) — every expected value below is hand-computed
from the synthetic series, not re-derived from the implementation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from shapely.geometry import box

from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import BasinId, StationId
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import FakeBasinStore, FakeHistoricalForcingStore

_EPOCH = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _import_module():
    try:
        from sapphire_flow.services import era5_land_validation
    except ImportError:
        pytest.fail(
            "sapphire_flow.services.era5_land_validation is not implemented yet — "
            "expected p_mean/frac_snow/high_prec_freq/low_prec_dur (Addor et al. "
            "2017) plus a 5%-relative-per-basin comparison against caravan:-"
            "namespaced basin static attributes."
        )
    return era5_land_validation


class TestPMean:
    def test_mean_of_daily_precipitation(self) -> None:
        mod = _import_module()
        assert mod.p_mean([0.0, 0.0, 10.0, 0.0, 0.0]) == pytest.approx(2.0)

    def test_empty_series_raises(self) -> None:
        mod = _import_module()
        with pytest.raises(ValueError, match="empty"):
            mod.p_mean([])


class TestFracSnow:
    def test_fraction_of_precip_in_sub_zero_climatological_months(self) -> None:
        """Two DISTINCT calendar months, each internally consistent (Jan is
        entirely cold, Feb entirely mild) — per-day and per-month
        classification agree here, so this alone would not catch a
        per-day-vs-per-month regression (see the counterexample below)."""
        mod = _import_module()
        precip = [10.0, 10.0, 10.0, 10.0]
        temp = [-1.0, -1.0, 5.0, 5.0]
        dates = [
            date(2020, 1, 1),
            date(2020, 1, 2),
            date(2020, 2, 1),
            date(2020, 2, 2),
        ]

        assert mod.frac_snow(precip, temp, dates) == pytest.approx(0.5)

    def test_same_month_sub_zero_days_do_not_count_when_month_mean_is_not(
        self,
    ) -> None:
        """Caravan's ``frac_snow`` classifies the whole CALENDAR MONTH by its
        climatological mean temperature, not each day individually. Four
        January days at [-1, -1, 5, 5] degC average to +2.0 degC for the
        month — above zero — so NONE of that month's precipitation counts as
        snow, even though two of the four days were individually sub-zero.
        A per-day formula (summing precip on sub-zero days) would wrongly
        return 0.5 here; Caravan's own formula returns 0.0."""
        mod = _import_module()
        precip = [10.0, 10.0, 10.0, 10.0]
        temp = [-1.0, -1.0, 5.0, 5.0]
        dates = [date(2020, 1, d) for d in (1, 2, 3, 4)]

        assert mod.frac_snow(precip, temp, dates) == pytest.approx(0.0)

    def test_length_mismatch_raises(self) -> None:
        mod = _import_module()
        with pytest.raises(ValueError, match="length mismatch"):
            mod.frac_snow([1.0, 2.0], [0.0], [date(2020, 1, 1)])

    def test_zero_total_precipitation_raises(self) -> None:
        mod = _import_module()
        with pytest.raises(ValueError, match="zero or negative"):
            mod.frac_snow([0.0, 0.0], [-1.0, 1.0], [date(2020, 1, 1), date(2020, 1, 2)])


class TestHighPrecFreq:
    def test_fraction_of_days_at_or_above_5x_mean(self) -> None:
        mod = _import_module()
        # mean = 4.0, threshold = 20.0; exactly one of five days qualifies.
        precip = [0.0, 0.0, 0.0, 0.0, 20.0]

        assert mod.high_prec_freq(precip) == pytest.approx(0.2)

    def test_empty_series_raises(self) -> None:
        mod = _import_module()
        with pytest.raises(ValueError, match="empty"):
            mod.high_prec_freq([])


class TestLowPrecDur:
    def test_mean_dry_spell_length(self) -> None:
        mod = _import_module()
        # Dry (< 1mm) runs: [0.5, 0.5] (len 2), [0.5, 0.5, 0.5] (len 3).
        precip = [0.5, 0.5, 5.0, 0.5, 0.5, 0.5, 5.0]

        assert mod.low_prec_dur(precip) == pytest.approx(2.5)

    def test_no_dry_days_returns_zero(self) -> None:
        mod = _import_module()
        assert mod.low_prec_dur([5.0, 5.0, 5.0]) == pytest.approx(0.0)

    def test_empty_series_raises(self) -> None:
        mod = _import_module()
        with pytest.raises(ValueError, match="empty"):
            mod.low_prec_dur([])


class TestCompareClimateIndices:
    def test_within_tolerance_flag_uses_relative_diff(self) -> None:
        mod = _import_module()
        sid = StationId(uuid4())

        agreements = mod.compare_climate_indices(
            station_id=sid,
            computed={"p_mean": 4.61 * 1.03},  # 3% high — within 5% tolerance
            reference={"p_mean": 4.61},
            tolerance=0.05,
        )

        assert len(agreements) == 1
        assert agreements[0].index_name == "p_mean"
        assert agreements[0].within_tolerance is True
        assert agreements[0].relative_diff == pytest.approx(0.03, abs=1e-6)

    def test_outside_tolerance_flag_false(self) -> None:
        mod = _import_module()
        sid = StationId(uuid4())

        agreements = mod.compare_climate_indices(
            station_id=sid,
            computed={"p_mean": 4.61 * 1.10},  # 10% high — outside 5%
            reference={"p_mean": 4.61},
            tolerance=0.05,
        )

        assert agreements[0].within_tolerance is False

    def test_ordered_p_mean_frac_snow_freq_dur(self) -> None:
        mod = _import_module()
        sid = StationId(uuid4())
        computed = {
            "low_prec_dur": 3.0,
            "p_mean": 4.6,
            "high_prec_freq": 0.03,
            "frac_snow": 0.25,
        }
        reference = dict(computed)

        agreements = mod.compare_climate_indices(
            station_id=sid, computed=computed, reference=reference
        )

        assert [a.index_name for a in agreements] == [
            "p_mean",
            "frac_snow",
            "high_prec_freq",
            "low_prec_dur",
        ]

    def test_missing_reference_index_is_skipped_not_erased(self) -> None:
        mod = _import_module()
        sid = StationId(uuid4())

        agreements = mod.compare_climate_indices(
            station_id=sid,
            computed={"p_mean": 4.6, "frac_snow": 0.25},
            reference={"p_mean": 4.6},
        )

        assert [a.index_name for a in agreements] == ["p_mean"]


class TestValidateEra5LandAgainstCaravan:
    def test_end_to_end_agreement_from_stored_forcing_and_basin_attributes(
        self,
    ) -> None:
        """A window and reference that FULLY cover what was requested (2/2
        days, the only reference index caravan: carries) is a genuine full
        parity pass — not narrowed, just small by construction."""
        mod = _import_module()
        basin = Basin(
            id=BasinId(uuid4()),
            code="test-basin",
            name="Test basin",
            geometry=box(6.0, 46.0, 10.0, 48.0),
            area_km2=100.0,
            attributes={"caravan:p_mean": 5.0},
            band_geometries=None,
            created_at=_EPOCH,
            network="bafu",
        )
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        forcing_store = FakeHistoricalForcingStore()
        # Two days: precip [4.0, 6.0] -> computed p_mean = 5.0 (exact match).
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="precipitation",
                    valid_time=datetime(1981, 1, 1, tzinfo=UTC),
                    value=4.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="precipitation",
                    valid_time=datetime(1981, 1, 2, tzinfo=UTC),
                    value=6.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="temperature",
                    valid_time=datetime(1981, 1, 1, tzinfo=UTC),
                    value=5.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="temperature",
                    valid_time=datetime(1981, 1, 2, tzinfo=UTC),
                    value=5.0,
                ),
            ]
        )

        result = mod.validate_era5_land_against_caravan(
            forcing_store=forcing_store,
            basin_store=basin_store,
            stations=[station],
            window_start=datetime(1981, 1, 1).date(),
            window_end=datetime(1981, 1, 2).date(),
        )

        assert len(result.agreements) == 1
        assert result.agreements[0].index_name == "p_mean"
        assert result.agreements[0].computed == pytest.approx(5.0)
        assert result.agreements[0].within_tolerance is True
        assert result.skips == []
        assert len(result.coverage) == 1
        assert result.coverage[0].expected_days == 2
        assert result.coverage[0].compared_days == 2
        assert result.coverage[0].coverage_fraction == pytest.approx(1.0)
        assert result.coverage[0].indices_compared == ("p_mean",)
        # Only p_mean was ever a Caravan reference on this basin (the other
        # three indices are simply absent) -> cannot count as a FULL parity
        # pass (all four indices), even though what WAS compared matched.
        assert result.is_full_parity_pass is False
        assert result.basins_missing_indices == result.coverage

    def test_station_without_basin_attributes_is_skipped(self) -> None:
        mod = _import_module()
        basin = Basin(
            id=BasinId(uuid4()),
            code="test-basin",
            name="Test basin",
            geometry=box(6.0, 46.0, 10.0, 48.0),
            area_km2=100.0,
            attributes=None,
            band_geometries=None,
            created_at=_EPOCH,
            network="bafu",
        )
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)
        forcing_store = FakeHistoricalForcingStore()

        result = mod.validate_era5_land_against_caravan(
            forcing_store=forcing_store,
            basin_store=basin_store,
            stations=[station],
        )

        assert result.agreements == []
        assert result.coverage == []
        assert len(result.skips) == 1
        assert result.skips[0].station_id == station.id
        assert result.skips[0].reason == "no_basin_attributes"
        assert result.is_full_parity_pass is False

    def test_empty_fleet_never_reports_a_pass(self) -> None:
        """B1: an empty result (nothing compared at all) must never evaluate
        as success — the failure mode this whole result type exists to
        prevent."""
        mod = _import_module()
        forcing_store = FakeHistoricalForcingStore()
        basin_store = FakeBasinStore()

        result = mod.validate_era5_land_against_caravan(
            forcing_store=forcing_store, basin_store=basin_store, stations=[]
        )

        assert result.agreements == []
        assert result.skips == []
        assert result.coverage == []
        assert result.is_full_parity_pass is False

    def test_narrow_comparison_is_visible_as_data_not_indistinguishable_pass(
        self,
    ) -> None:
        """B1's exact scenario: a basin compared on 2 days out of a nominal
        40-year (14,610-day) window, on 1 index out of 4. The agreements list
        alone looks all-green; ``is_full_parity_pass`` must say otherwise, and
        the shortfall must be readable from ``coverage``."""
        mod = _import_module()
        basin = Basin(
            id=BasinId(uuid4()),
            code="test-basin",
            name="Test basin",
            geometry=box(6.0, 46.0, 10.0, 48.0),
            area_km2=100.0,
            attributes={"caravan:p_mean": 5.0},
            band_geometries=None,
            created_at=_EPOCH,
            network="bafu",
        )
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        forcing_store = FakeHistoricalForcingStore()
        # Only the first two days of a nominal 1981-2020 window are stored.
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="precipitation",
                    valid_time=datetime(1981, 1, 1, tzinfo=UTC),
                    value=4.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="precipitation",
                    valid_time=datetime(1981, 1, 2, tzinfo=UTC),
                    value=6.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="temperature",
                    valid_time=datetime(1981, 1, 1, tzinfo=UTC),
                    value=5.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="temperature",
                    valid_time=datetime(1981, 1, 2, tzinfo=UTC),
                    value=5.0,
                ),
            ]
        )

        # The full Caravan window (1981-2020) — the fleet's stored data covers
        # only the first two of its 14,610 days.
        result = mod.validate_era5_land_against_caravan(
            forcing_store=forcing_store,
            basin_store=basin_store,
            stations=[station],
        )

        assert len(result.agreements) == 1  # still "all-green" on its face
        assert result.agreements[0].within_tolerance is True
        assert result.skips == []
        assert len(result.coverage) == 1
        # Derived, never hardcoded: the default window is the FULL ERA5-Land
        # record (owner decision 2026-08-18) and widens as the store grows.
        expected_days = (mod.ERA5_LAND_RECORD_END - mod.ERA5_LAND_RECORD_START).days + 1
        assert result.coverage[0].expected_days == expected_days
        assert result.coverage[0].compared_days == 2
        assert result.coverage[0].coverage_fraction == pytest.approx(2 / expected_days)
        # The one thing a caller must not be able to miss:
        assert result.is_full_parity_pass is False
        assert result.basins_below_coverage_floor == result.coverage
        assert result.basins_missing_indices == result.coverage

    def test_missing_forcing_records_reason(self) -> None:
        mod = _import_module()
        basin = Basin(
            id=BasinId(uuid4()),
            code="test-basin",
            name="Test basin",
            geometry=box(6.0, 46.0, 10.0, 48.0),
            area_km2=100.0,
            attributes={"caravan:p_mean": 5.0},
            band_geometries=None,
            created_at=_EPOCH,
            network="bafu",
        )
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)
        forcing_store = FakeHistoricalForcingStore()

        result = mod.validate_era5_land_against_caravan(
            forcing_store=forcing_store,
            basin_store=basin_store,
            stations=[station],
            window_start=datetime(1981, 1, 1).date(),
            window_end=datetime(1981, 1, 2).date(),
        )

        assert result.agreements == []
        assert result.coverage == []
        assert len(result.skips) == 1
        assert result.skips[0].reason == "missing_forcing_coverage"
