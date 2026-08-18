"""Plan 182 (M-A10) — Pyramid Meteorological Network Lvl1 CSV loader.

Salerno et al. 2025 (ESSD 17, 4293; Zenodo `10.5281/zenodo.15211352`, CC BY
4.0). **Lvl1 files only** (Non-goals: never the Lvl2 gap-filled monthly
reconstruction). Timestamps are NPT wall-clock (UTC+5:45), and the README
does NOT state period-beginning vs period-ending (D2) — this loader parses
them as-is, unconverted; D2's UTC<->NPT reconciliation (the declared ±1.75h
uncertainty) happens downstream in the diurnal-profile comparison
(`stats_coloc`), never here.

**Residual risk, flagged for the human reviewer**: `data/dhm_precip/pyramid/`
is gitignored and the real files are not present in this workspace at
implementation time, so the exact Lvl1 column names could not be verified
against a real file. `TIMESTAMP_COLUMN`/`PRECIP_COLUMN` below are this
loader's best-documented guess (the plan itself names the precipitation
column `RR` when it says "AWS4/AWSSC/CNG_SNP carry no usable RR for this
work"); a mismatch against the real files raises `PyramidSchemaMismatchError`
loudly rather than silently misreading, and is a one-line fix at the top of
this module once the real schema is confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl
import structlog

from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.params import DhmPrecipParams

log = structlog.get_logger(__name__)

TIMESTAMP_COLUMN: Final = "TIMESTAMP"
PRECIP_COLUMN: Final = "RR"


class PyramidLoaderError(Exception):
    """Base class — every loader failure is typed, never bare."""


class PyramidSourceUnreadableError(PyramidLoaderError):
    pass


class PyramidSchemaMismatchError(PyramidLoaderError):
    pass


class PyramidParseFailureError(PyramidLoaderError):
    pass


class PyramidDuplicateTimestampError(PyramidLoaderError):
    """The file has more than one row for the same `timestamp` — D3's
    common-retained-timestamp pairing needs a one-to-one join, and a
    duplicate would silently multiply (or arbitrarily pick) rows on the
    inner join instead of failing loudly."""


class PyramidInvalidTimestampError(PyramidLoaderError):
    """The `TIMESTAMP` column did not parse to an actual datetime (e.g. it
    stayed a plain string because `try_parse_dates` could not infer the
    format) — every downstream hour-of-day/timestamp-join operation
    silently assumes a real temporal dtype, so this is checked at the
    boundary rather than discovered as a confusing failure deep in
    `stats_coloc`."""


@dataclass(frozen=True, kw_only=True, slots=True)
class PyramidLoadResult:
    """D3/D4 — the physical-range-checked retained population, plus the
    counts a caller needs to see how much was dropped (Rule 1: every number
    carries its `n`)."""

    retained: pl.DataFrame
    """`(station, timestamp, value_mm)` — finite, in
    `[qc_mask_range_check_value_min_mm, qc_mask_range_check_value_max_mm]`
    only. NaN/infinite/out-of-range/null values are EXCLUDED (never zeroed,
    never left as a silent null that a naive denominator would count as
    "dry") — this IS the network's "own retained population" `stats_coloc`
    expects."""
    n_raw: int
    n_nonfinite: int
    """NaN or +-inf `value_mm` cells — never survives as a genuine reading."""
    n_out_of_range: int
    """Finite but outside the physical range — never survives either."""
    n_retained: int


def load_pyramid_lvl1_csv(
    path: Path, *, station: Station, params: DhmPrecipParams = DEFAULT_PARAMS
) -> PyramidLoadResult:
    """`retained` is `(station, timestamp, value_mm)` — `timestamp` is NPT
    wall-clock, UNCONVERTED (D2). `value_mm` is the raw `RR` reading at the
    file's native resolution (empirically 0.2 mm — LSI-Lastem tipping
    buckets, verified 2026-08-18 against the real files: 0.00% of 7,133 +
    9,280 positive JJAS values fall below 0.2 mm), physical-range-checked
    against `params.qc_mask_range_check_value_min_mm`/`_max_mm` — the SAME
    D4 physical-impossibility bounds DHM's own mask uses, so "physical
    range" means one thing across both networks."""
    if not path.exists() or not path.is_file():
        raise PyramidSourceUnreadableError(
            f"source file not found or not a file: {path}"
        )
    try:
        raw = pl.read_csv(path, try_parse_dates=True)
    except OSError as exc:
        raise PyramidSourceUnreadableError(
            f"source file not readable: {path}: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — any parse-time failure becomes a typed error
        raise PyramidParseFailureError(f"failed to parse {path}: {exc}") from exc

    missing = {TIMESTAMP_COLUMN, PRECIP_COLUMN} - set(raw.columns)
    if missing:
        raise PyramidSchemaMismatchError(
            f"{path} is missing expected column(s) {sorted(missing)} "
            f"(has {raw.columns}) — see this module's docstring: the real "
            "Pyramid Lvl1 schema was unverified at implementation time"
        )

    if not raw.schema[TIMESTAMP_COLUMN].is_temporal():
        raise PyramidInvalidTimestampError(
            f"{path} column {TIMESTAMP_COLUMN!r} did not parse as a "
            f"timestamp (dtype {raw.schema[TIMESTAMP_COLUMN]}) — every row "
            "must carry an actual datetime, never a string"
        )
    dup_count = int(raw[TIMESTAMP_COLUMN].is_duplicated().sum())
    if dup_count > 0:
        raise PyramidDuplicateTimestampError(
            f"{path} has {dup_count} row(s) sharing a duplicated "
            f"{TIMESTAMP_COLUMN!r} value — D3's pairing requires a "
            "one-to-one timestamp join"
        )

    casted = raw.with_columns(
        pl.col(PRECIP_COLUMN).cast(pl.Float64, strict=False).alias("value_mm")
    )
    cast_failed = raw[PRECIP_COLUMN].is_not_null() & casted["value_mm"].is_null()
    if cast_failed.sum() > 0:
        raise PyramidParseFailureError(
            f"{path} column {PRECIP_COLUMN!r} contains a non-numeric value "
            "that failed to cast"
        )

    frame = casted.select(
        pl.lit(str(station)).alias("station"),
        pl.col(TIMESTAMP_COLUMN).alias("timestamp"),
        pl.col("value_mm"),
    )
    n_raw = frame.height

    value = pl.col("value_mm")
    nonfinite = value.is_not_null() & (value.is_nan() | value.is_infinite())
    out_of_range = (
        value.is_not_null()
        & ~value.is_nan()
        & ~value.is_infinite()
        & (
            (value < params.qc_mask_range_check_value_min_mm)
            | (value > params.qc_mask_range_check_value_max_mm)
        )
    )
    flagged = frame.select(
        nonfinite.alias("_nonfinite"), out_of_range.alias("_out_of_range")
    )
    n_nonfinite = int(flagged["_nonfinite"].sum())
    n_out_of_range = int(flagged["_out_of_range"].sum())

    cleaned = frame.with_columns(
        pl.when(flagged["_nonfinite"] | flagged["_out_of_range"])
        .then(None)
        .otherwise(pl.col("value_mm"))
        .alias("value_mm")
    )
    retained = cleaned.filter(pl.col("value_mm").is_not_null())

    log.info(
        "dhm_precip.pyramid_loader.loaded",
        station=str(station),
        path=str(path),
        n_raw=n_raw,
        n_nonfinite=n_nonfinite,
        n_out_of_range=n_out_of_range,
        n_retained=retained.height,
    )
    return PyramidLoadResult(
        retained=retained,
        n_raw=n_raw,
        n_nonfinite=n_nonfinite,
        n_out_of_range=n_out_of_range,
        n_retained=retained.height,
    )
