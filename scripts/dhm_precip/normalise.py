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

import struct
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


class DuplicateDeliveredRowError(Exception):
    """D5/D6b — a delivered `(station, timestamp)` key is not unique.

    The reindex join onto the canonical axis must be one-to-one: each grid
    cell may match at most one delivered row. A one-to-many match would
    silently duplicate output rows for a single station-hour, breaking both
    D1's "exactly one row per (station, hour)" guarantee and the conservation
    proof (two output rows could each individually "match some delivered
    key" while a different delivered row goes missing entirely — see
    `assert_row_identity_conservation`, which checks both directions
    precisely because a one-to-many join can defeat a forward-only check).
    """


def _reject_duplicate_delivered_keys(restricted: pl.DataFrame) -> None:
    dupes = (
        restricted.group_by(["station", "timestamp"]).len().filter(pl.col("len") > 1)
    )
    if dupes.height:
        raise DuplicateDeliveredRowError(
            f"{dupes.height} delivered (station, timestamp) key(s) are not "
            "unique — the workbook must deliver at most one row per station "
            "per hour for the reindex join to be one-to-one"
        )


def normalise_hourly_axis(
    on_grid: pl.DataFrame, stations: frozenset[Station]
) -> pl.DataFrame:
    """D1/D2/D4/D6b — a pure function from the ON_GRID view to the
    normalised frame: `(source_row_index, station, timestamp, value_mm)`,
    one row per `(station, hour)` over ONE common hourly axis spanning the
    live-station data's global first-to-last canonical stamp.

    `stations` restricts the input BEFORE reindexing (D6b) — the output's
    station set is exactly `stations` by construction, never the workbook's
    raw 37-column population. Delivered `(station, timestamp)` keys must be
    unique (`DuplicateDeliveredRowError` otherwise) — the join is validated
    one-to-one (`validate="1:1"`) so a duplicate can never silently fan out
    into extra output rows.
    """
    restricted = on_grid.filter(pl.col("station").is_in(list(stations)))
    _reject_duplicate_delivered_keys(restricted)
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
        validate="1:1",
    )
    return normalised.select(
        ["source_row_index", "station", "timestamp", "value_mm"]
    ).sort(["station", "timestamp"])


_IDENTITY_KEY = ("source_row_index", "station", "timestamp")


def _float_bits(value: float | None) -> int | None:
    """The exact IEEE-754 bit pattern of a float64, or `None`. Two floats
    that compare numerically equal (`0.0 == -0.0`) can still have different
    bits — `eq_missing`/`==` would wrongly call them the same value, which
    is exactly what D5's "bit-identical" requirement rules out."""
    if value is None:
        return None
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _bit_identical(a: pl.Series, b: pl.Series) -> pl.Series:
    a_vals = a.cast(pl.Float64).to_list()
    b_vals = b.cast(pl.Float64).to_list()
    bits_equal = [
        _float_bits(x) == _float_bits(y) for x, y in zip(a_vals, b_vals, strict=True)
    ]
    return pl.Series(bits_equal, dtype=pl.Boolean)


def assert_row_identity_conservation(
    on_grid: pl.DataFrame,
    normalised: pl.DataFrame,
    stations: frozenset[Station],
) -> None:
    """D5 — conservation is proved by row identity, not by summing.

    Every delivered row, keyed by `(source_row_index, station, timestamp)`,
    must appear in `normalised` with a bit-identical `value_mm`, and every
    such key in `normalised` must correspond to a real delivered row —
    checked in BOTH directions via anti-joins, not by row-count equality
    plus a forward-only join. A forward-only check ("every kept key matches
    some delivered key") cannot tell a correct normalisation apart from one
    that drops one delivered row and duplicates another: the duplicate's two
    copies both "match" the single surviving delivered key, so the counts
    balance and the join still fully matches, while the dropped row's
    identity silently vanishes. Every row `normalised` added must carry BOTH
    a null `source_row_index` and a null `value_mm`. Raises
    `ConservationError` (never a silent pass) on any violation — this is an
    assertion, not a test-only check.
    """
    delivered = on_grid.filter(pl.col("station").is_in(list(stations))).select(
        "source_row_index", "station", "timestamp", "value_mm"
    )
    _reject_duplicate_delivered_keys(delivered)

    kept = normalised.filter(pl.col("source_row_index").is_not_null())
    inserted = normalised.filter(pl.col("source_row_index").is_null())

    kept_key_dupes = kept.group_by(list(_IDENTITY_KEY)).len().filter(pl.col("len") > 1)
    if kept_key_dupes.height:
        raise ConservationError(
            f"{kept_key_dupes.height} preserved row identity key(s) "
            "(source_row_index, station, timestamp) are duplicated in the "
            "normalised output — a one-to-many reindex would let a "
            "duplicate mask a different delivered row's disappearance"
        )

    delivered_keys = delivered.select(*_IDENTITY_KEY)
    kept_keys = kept.select(*_IDENTITY_KEY)

    missing = delivered_keys.join(kept_keys, on=list(_IDENTITY_KEY), how="anti")
    if missing.height:
        raise ConservationError(
            f"{missing.height} delivered row(s) are missing from the "
            "normalised output (identity present in on_grid, absent from "
            "the preserved rows)"
        )
    extra = kept_keys.join(delivered_keys, on=list(_IDENTITY_KEY), how="anti")
    if extra.height:
        raise ConservationError(
            f"{extra.height} preserved row(s) in the normalised output do "
            "not correspond to any delivered (source_row_index, station, "
            "timestamp) key"
        )

    matched = kept.join(
        delivered,
        on=list(_IDENTITY_KEY),
        how="inner",
        suffix="_delivered",
        validate="1:1",
    )
    bits_equal = _bit_identical(matched["value_mm"], matched["value_mm_delivered"])
    mismatched_value = matched.filter(~bits_equal)
    if mismatched_value.height:
        raise ConservationError(
            f"{mismatched_value.height} preserved rows have a mutated "
            "value_mm (not bit-identical to the delivered value)"
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
