"""Plan 216 (M-A11) T2 — locked red-first against the spec:
- "Where two initialisations yield a forecast at the same valid time, take
  the most recent initialisation within the band, deterministically —
  never both, never an average."
- "A window with any missing gauge hour is dropped, never partially
  summed."

No network, no real gauge workbook — every test is against small synthetic
frames, mirroring `tests/unit/scripts/test_era5_deaccumulate.py`.
"""

from __future__ import annotations

import polars as pl
import pytest

from scripts.dhm_precip.tigge_gauge_timing import (
    _gauge_window_lookup,
    dedup_most_recent_init,
)


class TestDedupMostRecentInit:
    def test_keeps_only_the_most_recent_init_at_a_shared_valid_time(self) -> None:
        # Two inits both reach 2025-06-02 00:00 within band D+1's exact
        # step set: 2025-06-01 00Z step24, and 2025-05-31 12Z step36.
        series = pl.DataFrame(
            {
                "station": ["A", "A"],
                "init_time_utc": [
                    "2025-06-01T00:00:00",
                    "2025-05-31T12:00:00",
                ],
                "ending_lead_hours": [24, 36],
                "valid_time_utc": [
                    "2025-06-02T00:00:00",
                    "2025-06-02T00:00:00",
                ],
                "tigge_mm": [5.0, 9.0],
            }
        ).with_columns(
            pl.col("init_time_utc").str.to_datetime(),
            pl.col("valid_time_utc").str.to_datetime(),
        )
        out = dedup_most_recent_init(series, band_steps=(24, 30, 36, 42))
        assert out.height == 1  # never both
        row = out.row(0, named=True)
        assert (
            row["tigge_mm"] == 5.0
        )  # the MORE RECENT init (June 1 00Z), never averaged
        assert str(row["init_time_utc"]) == "2025-06-01 00:00:00"

    def test_restricts_to_the_bands_exact_steps(self) -> None:
        series = pl.DataFrame(
            {
                "station": ["A", "A"],
                "init_time_utc": ["2025-06-01T00:00:00", "2025-06-01T00:00:00"],
                "ending_lead_hours": [18, 24],  # 18h is D+0-territory, not in D+1
                "valid_time_utc": ["2025-06-01T18:00:00", "2025-06-02T00:00:00"],
                "tigge_mm": [1.0, 2.0],
            }
        ).with_columns(
            pl.col("init_time_utc").str.to_datetime(),
            pl.col("valid_time_utc").str.to_datetime(),
        )
        out = dedup_most_recent_init(series, band_steps=(24, 30, 36, 42))
        assert out.height == 1
        assert out.row(0, named=True)["ending_lead_hours"] == 24

    def test_distinct_valid_times_are_both_kept(self) -> None:
        series = pl.DataFrame(
            {
                "station": ["A", "A"],
                "init_time_utc": ["2025-06-01T00:00:00", "2025-06-01T00:00:00"],
                "ending_lead_hours": [24, 30],
                "valid_time_utc": ["2025-06-02T00:00:00", "2025-06-02T06:00:00"],
                "tigge_mm": [1.0, 2.0],
            }
        ).with_columns(
            pl.col("init_time_utc").str.to_datetime(),
            pl.col("valid_time_utc").str.to_datetime(),
        )
        out = dedup_most_recent_init(series, band_steps=(24, 30, 36, 42))
        assert out.height == 2


class TestGaugeWindowLookup:
    def test_a_window_with_any_missing_hour_is_dropped_not_partially_summed(
        self,
    ) -> None:
        # Hourly gauge values for 06:00..11:00 on 2025-06-02, MISSING 09:00
        # (M-A3-excluded or absent) — the 6h window ending at 11:00 must be
        # null (dropped), never summed over the 5 present hours.
        gauge = pl.DataFrame(
            {
                "timestamp": [
                    "2025-06-02T06:00:00",
                    "2025-06-02T07:00:00",
                    "2025-06-02T08:00:00",
                    # 09:00 missing
                    "2025-06-02T10:00:00",
                    "2025-06-02T11:00:00",
                ],
                "value_mm": [1.0, 1.0, 1.0, 1.0, 1.0],
            }
        ).with_columns(pl.col("timestamp").str.to_datetime())
        full_index = pl.datetime_range(
            pl.datetime(2025, 6, 2, 0, 0),
            pl.datetime(2025, 6, 2, 23, 0),
            interval="1h",
            eager=True,
        ).alias("timestamp")
        lookup = _gauge_window_lookup(gauge, full_index=full_index)
        row = lookup.filter(pl.col("timestamp") == pl.datetime(2025, 6, 2, 11, 0)).row(
            0, named=True
        )
        assert (
            row["gauge_window_mm"] is None
        )  # dropped, never partially summed (4 of 6 hours)

    def test_a_complete_window_sums_all_six_hours(self) -> None:
        gauge = pl.DataFrame(
            {
                "timestamp": [f"2025-06-02T{h:02d}:00:00" for h in range(0, 6)],
                "value_mm": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            }
        ).with_columns(pl.col("timestamp").str.to_datetime())
        full_index = pl.datetime_range(
            pl.datetime(2025, 6, 2, 0, 0),
            pl.datetime(2025, 6, 2, 23, 0),
            interval="1h",
            eager=True,
        ).alias("timestamp")
        lookup = _gauge_window_lookup(gauge, full_index=full_index)
        row = lookup.filter(pl.col("timestamp") == pl.datetime(2025, 6, 2, 5, 0)).row(
            0, named=True
        )
        assert row["gauge_window_mm"] == pytest.approx(
            21.0
        )  # 1+2+3+4+5+6, never partial
