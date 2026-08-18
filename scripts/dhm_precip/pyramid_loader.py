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

from typing import TYPE_CHECKING, Final

import polars as pl
import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.dhm_precip.domain_types import Station

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


def load_pyramid_lvl1_csv(path: Path, *, station: Station) -> pl.DataFrame:
    """Returns `(station, timestamp, value_mm)` — `timestamp` is NPT
    wall-clock, UNCONVERTED (D2). `value_mm` is the raw `RR` reading at the
    file's native resolution (empirically 0.2 mm — LSI-Lastem tipping
    buckets, verified 2026-08-18 against the real files: 0.00% of 7,133 +
    9,280 positive JJAS values fall below 0.2 mm)."""
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
    log.info(
        "dhm_precip.pyramid_loader.loaded",
        station=str(station),
        path=str(path),
        rows=frame.height,
    )
    return frame
