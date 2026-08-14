"""Task 1b/2a — the canonical hourly axis (M-A2, Plan 172).

D1: materialise every hour missing from the workbook, for every live
station, as an explicit row. D2: a missing hour becomes NULL, never
0.0 — manufacturing zeros would fabricate exactly the dry-run defect M-A3
exists to find. D4: `source_row_index` IS the provenance signal — preserved
for a delivered cell, null for an inserted one. D5: conservation is proved
by row identity (bit-identical `value_mm` per `(source_row_index, station,
timestamp)`), never by summing — reindexing changes summation order and
float addition is not associative. D6b: the station population is
supplied (the runner's already-validated live-station set), never inferred
from the workbook's 37 raw columns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip import stats_axis
from scripts.dhm_precip.domain_types import NormalisationProvenance, Station
from scripts.dhm_precip.numeric import as_datetime, as_int

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams

# D6 — M-D3 ANSWERED the gauge accumulation convention: period-ending. This
# is now a stated fact with its source, not an assumption (Plan 172).
PERIOD_ENDING_SOURCE = (
    "M-D3, ANSWERED 2026-08-13: period-ending — 16:00 UTC = accumulation "
    "15:00 -> 16:00 UTC (docs/design/dhm-precipitation-milestones.md, "
    "'M-D3 - Processing provenance')"
)


class ConservationError(Exception):
    """D5 — the row-identity conservation assertion failed."""


def normalise_hourly_axis(
    on_grid: pl.DataFrame, stations: frozenset[Station]
) -> pl.DataFrame:
    """D1/D2/D4/D6b — a pure function from the ON_GRID view to the
    normalised frame: `(source_row_index, station, timestamp, value_mm)`,
    one row per `(station, hour)` over ONE common hourly axis spanning the
    live-station data's global first-to-last canonical stamp.

    `stations` restricts the input BEFORE reindexing (D6b) — the output's
    station set is exactly `stations` by construction, never the workbook's
    raw 37-column population.
    """
    restricted = on_grid.filter(pl.col("station").is_in(list(stations)))
    lo = as_datetime(restricted["timestamp"].min())
    hi = as_datetime(restricted["timestamp"].max())
    axis_dtype = restricted["timestamp"].dtype

    axis = pl.DataFrame(
        {
            "timestamp": pl.datetime_range(lo, hi, interval="1h", eager=True).cast(
                axis_dtype
            )
        }
    )
    grid = axis.join(pl.DataFrame({"station": sorted(stations)}), how="cross")

    normalised = grid.join(
        restricted.select("source_row_index", "station", "timestamp", "value_mm"),
        on=["station", "timestamp"],
        how="left",
    )
    return normalised.select(
        ["source_row_index", "station", "timestamp", "value_mm"]
    ).sort(["station", "timestamp"])


def assert_row_identity_conservation(
    on_grid: pl.DataFrame,
    normalised: pl.DataFrame,
    stations: frozenset[Station],
) -> None:
    """D5 — conservation is proved by row identity, not by summing.

    Every delivered row, keyed by `(source_row_index, station, timestamp)`,
    must appear in `normalised` with a bit-identical `value_mm`. Every row
    `normalised` added must carry BOTH a null `source_row_index` and a null
    `value_mm`. Raises `ConservationError` (never a silent pass) on any
    violation — this is an assertion, not a test-only check.
    """
    delivered = on_grid.filter(pl.col("station").is_in(list(stations))).select(
        "source_row_index", "station", "timestamp", "value_mm"
    )
    kept = normalised.filter(pl.col("source_row_index").is_not_null())
    inserted = normalised.filter(pl.col("source_row_index").is_null())

    if kept.height != delivered.height:
        raise ConservationError(
            f"expected {delivered.height} delivered rows preserved in the "
            f"normalised output, found {kept.height}"
        )

    matched = kept.join(
        delivered,
        on=["source_row_index", "station", "timestamp"],
        how="inner",
        suffix="_delivered",
    )
    if matched.height != kept.height:
        raise ConservationError(
            f"{kept.height - matched.height} preserved rows do not match a "
            "delivered (source_row_index, station, timestamp) key"
        )
    mismatched_value = matched.filter(
        ~pl.col("value_mm").eq_missing(pl.col("value_mm_delivered"))
    )
    if mismatched_value.height:
        raise ConservationError(
            f"{mismatched_value.height} preserved rows have a mutated value_mm"
        )

    bad_inserted = inserted.filter(pl.col("value_mm").is_not_null())
    if bad_inserted.height:
        raise ConservationError(
            f"{bad_inserted.height} inserted row(s) (null source_row_index) "
            "carry a non-null value_mm"
        )


def build_provenance(
    raw: pl.DataFrame, params: DhmPrecipParams
) -> NormalisationProvenance:
    """D3/D6 (Phase 2a) — the two off-grid count grains, computed from the
    RAW view by the existing D3 diagnostics (`stats_axis.py`), plus the
    period-ending statement with its source (M-D3)."""
    row_counts = stats_axis.row_count_diagnostics(raw, params)
    off_grid_obs = stats_axis.off_grid_observation_diagnostics(raw, params)
    return NormalisationProvenance(
        off_grid_source_timestamp_rows=as_int(row_counts["off_grid_row_count"][0]),
        off_grid_non_null_observations=as_int(
            off_grid_obs["off_grid_observation_count"][0]
        ),
        period_ending=True,
        period_ending_source=PERIOD_ENDING_SOURCE,
    )
