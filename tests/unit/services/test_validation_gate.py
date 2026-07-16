"""Plan 115b3 — the reference-comparison GO/NO-GO gate (§4A-§4D).

Tests split by the plan's own "Tests" section:
- tolerance-gate classification (precip rel-bias; temperature mean-bias+RMSE)
  against synthetic inputs AT THE BOUNDARIES;
- a pinned regression fixture for the basin-mean derivation, so the gate
  itself doesn't silently drift;
- coverage-gap / degenerate-denominator handling (missing-data behaviour
  must escalate, never silently inner-join);
- the 4C overlap-window intersection rule and the 4D live-tail residual
  pairing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sapphire_flow.services.validation_gate import (
    COMPARISON_END,
    COMPARISON_START,
    GateVerdict,
    OverlapWindow,
    ReferenceComparisonReport,
    classify_precip_rel_bias,
    classify_temperature,
    compute_live_tail_residual,
    discover_overlap_window,
    evaluate_precip_basin,
    evaluate_temperature_basin,
    fetch_overlap_products,
    run_reference_comparison,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import StationId
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import FakeHistoricalForcingStore

# ---------------------------------------------------------------------------
# Tolerance-gate classification — synthetic inputs at the boundaries.
# ---------------------------------------------------------------------------


class TestClassifyPrecipRelBias:
    def test_at_five_percent_exactly_passes(self) -> None:
        assert classify_precip_rel_bias(0.05) is GateVerdict.PASS
        assert classify_precip_rel_bias(-0.05) is GateVerdict.PASS

    def test_just_over_five_percent_flags(self) -> None:
        assert classify_precip_rel_bias(0.0501) is GateVerdict.FLAG

    def test_at_twenty_percent_exactly_flags_not_escalates(self) -> None:
        assert classify_precip_rel_bias(0.20) is GateVerdict.FLAG

    def test_just_over_twenty_percent_escalates(self) -> None:
        assert classify_precip_rel_bias(0.2001) is GateVerdict.ESCALATE

    def test_a_large_negative_bias_does_not_falsely_pass(self) -> None:
        # Plan 115b3 status note: round-1 plan review caught a real
        # correctness bug where the extraction had DROPPED the absolute
        # value from the precip gate, so a large NEGATIVE (drier) bias would
        # falsely PASS. Soundness: fails RED against a classifier that
        # thresholds the SIGNED rel_bias instead of its magnitude.
        assert classify_precip_rel_bias(-0.30) is GateVerdict.ESCALATE
        assert classify_precip_rel_bias(-0.15) is GateVerdict.FLAG


class TestClassifyTemperature:
    def test_at_bias_and_rmse_thresholds_exactly_passes(self) -> None:
        assert classify_temperature(0.5, 1.0) is GateVerdict.PASS
        assert classify_temperature(-0.5, 1.0) is GateVerdict.PASS

    def test_bias_just_over_flag_threshold_flags(self) -> None:
        assert classify_temperature(0.5001, 0.0) is GateVerdict.FLAG

    def test_rmse_just_over_flag_threshold_flags(self) -> None:
        assert classify_temperature(0.0, 1.0001) is GateVerdict.FLAG

    def test_at_escalate_thresholds_exactly_still_flags(self) -> None:
        assert classify_temperature(1.0, 2.0) is GateVerdict.FLAG

    def test_bias_just_over_escalate_threshold_escalates(self) -> None:
        assert classify_temperature(1.0001, 0.0) is GateVerdict.ESCALATE

    def test_rmse_just_over_escalate_threshold_escalates(self) -> None:
        assert classify_temperature(0.0, 2.0001) is GateVerdict.ESCALATE

    def test_negative_bias_beyond_escalate_threshold_escalates(self) -> None:
        assert classify_temperature(-1.5, 0.0) is GateVerdict.ESCALATE


# ---------------------------------------------------------------------------
# Regression fixture — a small, hand-computed basin-mean derivation pinned so
# the gate itself doesn't silently drift.
# ---------------------------------------------------------------------------

_SID = StationId(uuid4())

# Five days, hand-picked so the pinned numbers below are exact and easy to
# audit: ours totals 21.0mm, camels totals 20.0mm -> rel_bias = +5%.
_OURS_PRECIP: dict[date, float] = {
    date(2020, 1, 1): 1.0,
    date(2020, 1, 2): 0.0,
    date(2020, 1, 3): 10.0,
    date(2020, 1, 4): 5.0,
    date(2020, 1, 5): 5.0,
}
_CAMELS_PRECIP: dict[date, float] = {
    date(2020, 1, 1): 1.0,
    date(2020, 1, 2): 0.0,
    date(2020, 1, 3): 9.0,
    date(2020, 1, 4): 5.0,
    date(2020, 1, 5): 5.0,
}

# ours - camels: [0.5, -0.5, 1.0, -1.0, 0.0] -> mean_bias=0.0, rmse=sqrt(0.5)
_OURS_TEMP: dict[date, float] = {
    date(2020, 1, 1): 0.5,
    date(2020, 1, 2): -0.5,
    date(2020, 1, 3): 1.0,
    date(2020, 1, 4): -1.0,
    date(2020, 1, 5): 0.0,
}
_CAMELS_TEMP: dict[date, float] = {
    date(2020, 1, 1): 0.0,
    date(2020, 1, 2): 0.0,
    date(2020, 1, 3): 0.0,
    date(2020, 1, 4): 0.0,
    date(2020, 1, 5): 0.0,
}


class TestBasinMeanDerivationRegressionFixture:
    def test_precip_basin_mean_pinned(self) -> None:
        result = evaluate_precip_basin(_SID, "TEST-BASIN", _OURS_PRECIP, _CAMELS_PRECIP)
        assert result.ours_total_mm == 21.0
        assert result.camels_total_mm == 20.0
        assert result.rel_bias is not None
        assert abs(result.rel_bias - 0.05) < 1e-12
        assert result.verdict is GateVerdict.PASS
        assert result.n_missing_in_ours == 0
        assert result.n_missing_in_camels == 0

    def test_temperature_basin_mean_pinned(self) -> None:
        result = evaluate_temperature_basin(
            _SID, "TEST-BASIN", _OURS_TEMP, _CAMELS_TEMP
        )
        assert result.mean_bias is not None
        assert abs(result.mean_bias - 0.0) < 1e-12
        assert result.rmse is not None
        assert abs(result.rmse - (0.5**0.5)) < 1e-12
        assert result.verdict is GateVerdict.PASS


# ---------------------------------------------------------------------------
# Missing-data behaviour (Plan 115b3 §4A) — a coverage gap FAILS/escalates,
# never a silent inner-join.
# ---------------------------------------------------------------------------


class TestCoverageGapHandling:
    def test_precip_date_missing_in_ours_forces_data_quality_escalate(self) -> None:
        ours = dict(_OURS_PRECIP)
        del ours[date(2020, 1, 3)]
        result = evaluate_precip_basin(_SID, "TEST-BASIN", ours, _CAMELS_PRECIP)
        assert result.verdict is GateVerdict.DATA_QUALITY_ESCALATE
        assert result.rel_bias is None
        assert result.n_missing_in_ours == 1

    def test_precip_date_missing_in_camels_forces_data_quality_escalate(self) -> None:
        camels = dict(_CAMELS_PRECIP)
        del camels[date(2020, 1, 3)]
        result = evaluate_precip_basin(_SID, "TEST-BASIN", _OURS_PRECIP, camels)
        assert result.verdict is GateVerdict.DATA_QUALITY_ESCALATE
        assert result.n_missing_in_camels == 1

    def test_precip_non_positive_camels_total_forces_data_quality_escalate(
        self,
    ) -> None:
        # Soundness: fails RED against a classifier that divides by a
        # non-positive denominator instead of guarding it.
        camels = {d: 0.0 for d in _OURS_PRECIP}
        result = evaluate_precip_basin(_SID, "TEST-BASIN", _OURS_PRECIP, camels)
        assert result.verdict is GateVerdict.DATA_QUALITY_ESCALATE
        assert result.rel_bias is None

    def test_temperature_date_missing_forces_data_quality_escalate(self) -> None:
        ours = dict(_OURS_TEMP)
        del ours[date(2020, 1, 3)]
        result = evaluate_temperature_basin(_SID, "TEST-BASIN", ours, _CAMELS_TEMP)
        assert result.verdict is GateVerdict.DATA_QUALITY_ESCALATE
        assert result.mean_bias is None
        assert result.rmse is None

    def test_temperature_no_overlap_at_all_forces_data_quality_escalate(self) -> None:
        result = evaluate_temperature_basin(_SID, "TEST-BASIN", {}, {})
        assert result.verdict is GateVerdict.DATA_QUALITY_ESCALATE


# ---------------------------------------------------------------------------
# run_reference_comparison — DB plumbing (4A/4B) over a fake store.
# ---------------------------------------------------------------------------


class TestRunReferenceComparison:
    def test_wires_fetch_and_evaluation_per_station(self) -> None:
        station = make_station_config(code="BASIN-1")
        store = FakeHistoricalForcingStore()
        day = datetime(2000, 6, 15, tzinfo=UTC)
        store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.METEOSWISS_RHIRESD.value,
                    parameter="precipitation",
                    valid_time=day,
                    value=10.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.CAMELS_CH.value,
                    parameter="precipitation",
                    valid_time=day,
                    value=8.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.METEOSWISS_TABSD.value,
                    parameter="temperature",
                    valid_time=day,
                    value=5.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.CAMELS_CH.value,
                    parameter="temperature",
                    valid_time=day,
                    value=4.0,
                ),
            ]
        )

        report = run_reference_comparison(store, [station])

        assert isinstance(report, ReferenceComparisonReport)
        assert len(report.precipitation) == 1
        assert len(report.temperature) == 1
        assert report.precipitation[0].code == "BASIN-1"
        assert report.precipitation[0].ours_total_mm == 10.0
        assert report.precipitation[0].camels_total_mm == 8.0
        assert report.temperature[0].mean_bias == 1.0

    def test_window_is_pinned_to_1981_2021(self) -> None:
        assert ensure_utc(datetime(1981, 1, 1, tzinfo=UTC)) == COMPARISON_START
        assert ensure_utc(datetime(2021, 1, 1, tzinfo=UTC)) == COMPARISON_END


# ---------------------------------------------------------------------------
# 4C — overlap-window intersection (executable rule).
# ---------------------------------------------------------------------------


class _FakeBoundaryAdapter:
    def __init__(self, ranges: dict[ForcingSource, tuple[date, date] | None]) -> None:
        self._ranges = ranges
        self.fetch_calls: list[tuple[ForcingSource, UtcDatetime, UtcDatetime]] = []
        self.rows_by_product: dict[ForcingSource, list[object]] = {}

    def discover_product_availability_range(
        self, product: ForcingSource
    ) -> tuple[date, date] | None:
        return self._ranges.get(product)

    def fetch_products(
        self,
        products: list[ForcingSource],
        station_configs: list[object],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[object]:
        (product,) = products
        self.fetch_calls.append((product, start, end))
        return self.rows_by_product.get(product, [])


class TestDiscoverOverlapWindow:
    def test_intersects_the_two_ranges(self) -> None:
        adapter = _FakeBoundaryAdapter(
            {
                ForcingSource.METEOSWISS_RHIRESD: (
                    date(1981, 1, 1),
                    date(2026, 5, 31),
                ),
                ForcingSource.METEOSWISS_RPRELIMD: (
                    date(2026, 5, 15),
                    date(2026, 7, 10),
                ),
            }
        )
        window = discover_overlap_window(adapter)
        assert window == OverlapWindow(start=date(2026, 5, 15), end=date(2026, 5, 31))

    def test_no_overlap_returns_none(self) -> None:
        # Soundness: fails RED against an intersection that doesn't check
        # start > end (would return an inverted/empty window instead of None).
        adapter = _FakeBoundaryAdapter(
            {
                ForcingSource.METEOSWISS_RHIRESD: (
                    date(1981, 1, 1),
                    date(2026, 4, 30),
                ),
                ForcingSource.METEOSWISS_RPRELIMD: (
                    date(2026, 5, 15),
                    date(2026, 7, 10),
                ),
            }
        )
        assert discover_overlap_window(adapter) is None

    def test_missing_product_range_returns_none(self) -> None:
        adapter = _FakeBoundaryAdapter(
            {
                ForcingSource.METEOSWISS_RHIRESD: (date(1981, 1, 1), date(2026, 5, 31)),
                ForcingSource.METEOSWISS_RPRELIMD: None,
            }
        )
        assert discover_overlap_window(adapter) is None


class TestFetchOverlapProducts:
    def test_fetches_both_products_over_the_same_half_open_window(self) -> None:
        adapter = _FakeBoundaryAdapter({})
        window = OverlapWindow(start=date(2026, 5, 15), end=date(2026, 5, 17))
        fetch_overlap_products(adapter, [], window)

        assert len(adapter.fetch_calls) == 2
        products = {call[0] for call in adapter.fetch_calls}
        assert products == {
            ForcingSource.METEOSWISS_RHIRESD,
            ForcingSource.METEOSWISS_RPRELIMD,
        }
        for _, start, end in adapter.fetch_calls:
            assert start == ensure_utc(datetime(2026, 5, 15, tzinfo=UTC))
            # half-open end = window.end + 1 day, so the 17th is INCLUDED.
            assert end == ensure_utc(datetime(2026, 5, 18, tzinfo=UTC))


# ---------------------------------------------------------------------------
# 4D — the live-tail residual: paired-only comparison, exclusions counted.
# ---------------------------------------------------------------------------


class TestComputeLiveTailResidual:
    def test_pairs_by_station_and_date_and_computes_bias_rmse(self) -> None:
        sid = StationId(uuid4())
        window = OverlapWindow(start=date(2026, 5, 15), end=date(2026, 5, 16))
        rhiresd_rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=datetime(2026, 5, 15, tzinfo=UTC),
                value=10.0,
            ),
            make_raw_historical_forcing(
                station_id=sid,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=datetime(2026, 5, 16, tzinfo=UTC),
                value=4.0,
            ),
        ]
        rprelimd_rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                parameter="precipitation",
                valid_time=datetime(2026, 5, 15, tzinfo=UTC),
                value=11.0,
            ),
            make_raw_historical_forcing(
                station_id=sid,
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                parameter="precipitation",
                valid_time=datetime(2026, 5, 16, tzinfo=UTC),
                value=2.0,
            ),
        ]

        result = compute_live_tail_residual(rhiresd_rows, rprelimd_rows, window)

        assert result.n_paired == 2
        assert result.n_excluded_rhiresd_only == 0
        assert result.n_excluded_rprelimd_only == 0
        # diffs (rprelimd - rhiresd): [1.0, -2.0] -> mean=-0.5, rmse=sqrt(2.5)
        assert result.mean_bias is not None
        assert abs(result.mean_bias - (-0.5)) < 1e-12
        assert result.rmse is not None
        assert abs(result.rmse - (2.5**0.5)) < 1e-12

    def test_excludes_unpaired_rows_and_counts_them(self) -> None:
        sid = StationId(uuid4())
        window = OverlapWindow(start=date(2026, 5, 15), end=date(2026, 5, 16))
        rhiresd_rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=datetime(2026, 5, 15, tzinfo=UTC),
                value=10.0,
            )
        ]
        rprelimd_rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                parameter="precipitation",
                valid_time=datetime(2026, 5, 16, tzinfo=UTC),
                value=2.0,
            )
        ]

        result = compute_live_tail_residual(rhiresd_rows, rprelimd_rows, window)

        assert result.n_paired == 0
        assert result.n_excluded_rhiresd_only == 1
        assert result.n_excluded_rprelimd_only == 1
        assert result.mean_bias is None
        assert result.rmse is None
