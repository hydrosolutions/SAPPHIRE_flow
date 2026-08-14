"""Task 1b/2a — the canonical hourly axis (M-A2, Plan 172).

D1: every hour missing from the workbook, for every live station, becomes
an explicit row. D2: a missing hour is NULL, never 0.0. D4: `source_row_index`
is the provenance signal. D5: conservation is proved by row identity, never
by summing. D6b: the station population is supplied, never inferred.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.normalise import (
    ConservationError,
    DuplicateDeliveredRowError,
    assert_row_identity_conservation,
    build_provenance,
    normalise_hourly_axis,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


def _raw_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestNormaliseHourlyAxis:
    def test_a_missing_hour_materialises_as_null_never_zero(self) -> None:
        # Station "A" reports hours 0 and 2; hour 1 is missing entirely from
        # the workbook (D1) — it must appear as a NULL row, never 0.0 (D2).
        rows = [
            {
                "source_row_index": 0,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 2,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 2),
                "value_mm": 3.0,
            },
        ]
        result = normalise_hourly_axis(_on_grid_frame(rows), frozenset({Station("A")}))
        gap = result.filter(pl.col("timestamp") == datetime(2024, 1, 1, 1))
        assert gap.height == 1
        assert gap["value_mm"][0] is None
        assert gap["source_row_index"][0] is None

    def test_delivered_rows_keep_their_source_row_index(self) -> None:
        rows = [
            {
                "source_row_index": 7,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 1.0,
            },
        ]
        result = normalise_hourly_axis(_on_grid_frame(rows), frozenset({Station("A")}))
        delivered = result.filter(pl.col("timestamp") == datetime(2024, 1, 1, 0))
        assert delivered["source_row_index"][0] == 7
        assert delivered["value_mm"][0] == pytest.approx(1.0)

    def test_axis_is_complete_unique_strictly_increasing_and_hourly(self) -> None:
        rows = [
            {
                "source_row_index": i,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0) + timedelta(hours=i),
                "value_mm": float(i),
            }
            for i in (0, 1, 4)
        ]
        result = normalise_hourly_axis(_on_grid_frame(rows), frozenset({Station("A")}))
        timestamps = result["timestamp"].to_list()
        assert timestamps == sorted(timestamps)
        assert len(timestamps) == len(set(timestamps))
        diffs = {
            (b - a).total_seconds()
            for a, b in zip(timestamps, timestamps[1:], strict=False)
        }
        assert diffs == {3600.0}

    def test_output_station_set_is_exactly_the_supplied_set(self) -> None:
        # D6b: the 11 permanently-empty workbook columns must never enter
        # the output, even if they carry rows in `on_grid` (all-null cells).
        rows = [
            {
                "source_row_index": 0,
                "station": "Usable",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 0,
                "station": "AlwaysEmpty",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": None,
            },
        ]
        result = normalise_hourly_axis(
            _on_grid_frame(rows), frozenset({Station("Usable")})
        )
        assert set(result["station"].unique().to_list()) == {"Usable"}

    def test_multi_station_reindex_produces_one_row_per_station_per_hour(self) -> None:
        rows = [
            {
                "source_row_index": 0,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 0,
                "station": "B",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 2.0,
            },
            {
                "source_row_index": 1,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 1),
                "value_mm": 4.0,
            },
            # B has no row at hour 1 at all — a genuine cross-station gap.
        ]
        result = normalise_hourly_axis(
            _on_grid_frame(rows), frozenset({Station("A"), Station("B")})
        )
        assert result.height == 4  # 2 stations x 2 hourly slots
        b_gap = result.filter(
            (pl.col("station") == "B")
            & (pl.col("timestamp") == datetime(2024, 1, 1, 1))
        )
        assert b_gap.height == 1
        assert b_gap["value_mm"][0] is None
        assert b_gap["source_row_index"][0] is None

    def test_duplicate_delivered_key_is_rejected(self) -> None:
        # Two delivered rows for the SAME (station, timestamp) — a
        # one-to-many join would silently duplicate that grid cell in the
        # output (breaking D1's "exactly one row per station-hour").
        rows = [
            {
                "source_row_index": 0,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 1,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 2.0,
            },
        ]
        with pytest.raises(DuplicateDeliveredRowError):
            normalise_hourly_axis(_on_grid_frame(rows), frozenset({Station("A")}))


class TestAssertRowIdentityConservation:
    def _delivered(self) -> pl.DataFrame:
        return _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 1),
                    "value_mm": 3.0,
                },
            ]
        )

    def test_a_correct_normalisation_passes(self) -> None:
        delivered = self._delivered()
        normalised = normalise_hourly_axis(delivered, frozenset({Station("A")}))
        assert_row_identity_conservation(
            delivered, normalised, frozenset({Station("A")})
        )

    def test_a_dropped_delivered_row_fails_the_assertion(self) -> None:
        # A deliberately lossy "reindex" that silently drops a delivered
        # row — the row count no longer balances.
        delivered = self._delivered()
        lossy = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
            ]
        )
        with pytest.raises(ConservationError):
            assert_row_identity_conservation(
                delivered, lossy, frozenset({Station("A")})
            )

    def test_a_mutated_value_fails_even_when_the_total_still_balances(self) -> None:
        # Swap the two delivered values between rows — the SUM is unchanged
        # (1.0 + 3.0 == 3.0 + 1.0) but neither row now carries its own
        # bit-identical value, so a sum-based check would wrongly pass.
        delivered = self._delivered()
        swapped = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 3.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 1),
                    "value_mm": 1.0,
                },
            ]
        )
        with pytest.raises(ConservationError):
            assert_row_identity_conservation(
                delivered, swapped, frozenset({Station("A")})
            )

    def test_an_inserted_row_with_a_non_null_value_fails(self) -> None:
        # Needs a genuine gap (hour 1 missing) so normalise actually
        # inserts a row to tamper with.
        delivered = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 2,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 2),
                    "value_mm": 3.0,
                },
            ]
        )
        tampered = normalise_hourly_axis(delivered, frozenset({Station("A")}))
        tampered = tampered.with_columns(
            pl.when(pl.col("source_row_index").is_null())
            .then(pl.lit(999.0))
            .otherwise(pl.col("value_mm"))
            .alias("value_mm")
        )
        with pytest.raises(ConservationError):
            assert_row_identity_conservation(
                delivered, tampered, frozenset({Station("A")})
            )

    def test_duplicate_delivered_key_is_rejected(self) -> None:
        # `on_grid` itself carries two rows for the same (station,
        # timestamp) — the assertion must reject this input rather than
        # silently reasoning about which one is "the" delivered row.
        delivered = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 2.0,
                },
            ]
        )
        normalised = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
            ]
        )
        with pytest.raises(DuplicateDeliveredRowError):
            assert_row_identity_conservation(
                delivered, normalised, frozenset({Station("A")})
            )

    def test_a_dropped_and_duplicated_row_fails_the_assertion(self) -> None:
        # A one-to-many-join style corruption: the normalised output DROPS
        # delivered row 1 (source_row_index=1) entirely and instead carries
        # TWO copies of delivered row 0's identity. Row counts still balance
        # (2 delivered, 2 kept) and every kept row still "matches some
        # delivered key" (both copies match row 0) — a forward-only,
        # count-based check would wrongly pass this. The bidirectional
        # anti-join must catch it because row 1's identity never appears in
        # the output at all.
        delivered = self._delivered()
        corrupted = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 1.0,
                },
            ]
        )
        with pytest.raises(ConservationError):
            assert_row_identity_conservation(
                delivered, corrupted, frozenset({Station("A")})
            )

    def test_a_negative_zero_replacing_a_positive_zero_fails_bit_identity(
        self,
    ) -> None:
        # 0.0 and -0.0 compare numerically equal but are NOT the same IEEE
        # bit pattern — D5 requires bit-identical values, so this must be
        # caught even though a plain `==`/`eq_missing` check would pass it.
        delivered = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": 0.0,
                },
            ]
        )
        signed_zero_flipped = _on_grid_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0),
                    "value_mm": -0.0,
                },
            ]
        )
        assert 0.0 == -0.0  # sanity: numerically equal, this is the trap
        with pytest.raises(ConservationError):
            assert_row_identity_conservation(
                delivered, signed_zero_flipped, frozenset({Station("A")})
            )


class TestBuildProvenance:
    def test_off_grid_counts_match_their_own_grain(self) -> None:
        # 3 source rows: 2 on-grid (minute 0), 1 off-grid (minute 15). The
        # off-grid row carries 2 non-null observations across its stations.
        rows = [
            {
                "source_row_index": 0,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 0,
                "station": "B",
                "timestamp": datetime(2024, 1, 1, 0, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 1,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 1, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 1,
                "station": "B",
                "timestamp": datetime(2024, 1, 1, 1, 0),
                "value_mm": None,
            },
            {
                "source_row_index": 2,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 1, 15),
                "value_mm": 0.5,
            },
            {
                "source_row_index": 2,
                "station": "B",
                "timestamp": datetime(2024, 1, 1, 1, 15),
                "value_mm": 0.7,
            },
        ]
        provenance = build_provenance(_raw_frame(rows), DEFAULT_PARAMS)
        assert provenance.off_grid_source_timestamp_rows == 1
        assert provenance.off_grid_non_null_observations == 2

    def test_records_period_ending_with_a_source(self) -> None:
        rows = [
            {
                "source_row_index": 0,
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0, 0),
                "value_mm": 1.0,
            },
        ]
        provenance = build_provenance(_raw_frame(rows), DEFAULT_PARAMS)
        assert provenance.period_ending is True
        assert "M-D3" in provenance.period_ending_source
