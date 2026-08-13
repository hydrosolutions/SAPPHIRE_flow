"""Task 1a — dependencies and loaders (D3, D9b, D12).

sha256-verified loading of the DHM precipitation workbook into the canonical
long frame (D3), plus the D12 station-coordinate table. No pytest semantics
here (constraint 1) — every failure mode is a typed exception, never a skip;
the *only* skip condition (an unset `DHM_PRECIP_XLSX`) is applied by the
integration test alone, not by this module.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Final

import polars as pl
import structlog

from scripts.dhm_precip.domain_types import (
    LongFrameInventory,
    Station,
    StationCoordinate,
    StationCoordinateTable,
)

log = structlog.get_logger(__name__)

TIME_COLUMN: Final = "Time (UTC)"
STATION_SUFFIX: Final = " (mm)"

# The exact 37 workbook headers, in file order — D3's "committed expected
# list shipped by 1a itself". A schema-drifted workbook fails loudly here,
# never silently.
EXPECTED_WORKBOOK_COLUMNS: Final[tuple[str, ...]] = (
    "Syangboche Airport (mm)",
    "Humde Airport (mm)",
    "Ghorepani (mm)",
    "Lukla Airport (mm)",
    "Sarmathang (mm)",
    "Olangchunggola (mm)",
    "Lete (FNEP) (mm)",
    "Salleri (mm)",
    "Nagarkot_AWS (mm)",
    "Aiselukhark (mm)",
    "Taplejung (mm)",
    "Mai Pokhari (mm)",
    "Gumthang_AWS (mm)",
    "Okhaldhunga_AWS (mm)",
    "Pakhribas (mm)",
    "Kanyam Tea Estate (mm)",
    "Chautara (mm)",
    "Kirtipur (mm)",
    "Kathmandu Airport (AWOS) (mm)",
    "Khumaltar (mm)",
    "Num (mm)",
    "Ilam Tea Estate (mm)",
    "Dhankuta_AWS (mm)",
    "Udayapur Gadhi (mm)",
    "Sindhuli Madhi (mm)",
    "Manthali Airport (mm)",
    "Dharan Bazar (mm)",
    "Gaighat (mm)",
    "Simara Airport_AWS (mm)",
    "Tarahara (mm)",
    "Gaida (Kankai) (mm)",
    "Chandragadi Airport (mm)",
    "Biratnagar Airport (mm)",
    "Lahan (mm)",
    "Rajbiraj Airport (mm)",
    "Bharatpur Airport (mm)",
    "Madi Kalyanpur (mm)",
)

# D1 vision — sha256 of `combined_precipitation_37_stations.xlsx`. The CLI's
# pinned production digest (D9b) — never overridden via env/CLI (D9b).
PRODUCTION_SOURCE_SHA256: Final = (
    "8dc57e4364ef788b022779a42df86918200d1c8dc723948f22657bc70ff98f57"
)

DEFAULT_XLSX_PATH: Final = Path(
    "data/dhm_precip/combined_precipitation_37_stations.xlsx"
)
DEFAULT_COORDS_PATH: Final = Path("data/dhm_precip/station_coordinates.csv")

_COORDS_REQUIRED_COLUMNS: Final = frozenset(
    {"station", "excel_col", "lat", "lon", "elev"}
)


class DhmPrecipLoaderError(Exception):
    """Base class — every loader failure is typed, never a bare exception."""


class SourcePathUnsetError(DhmPrecipLoaderError):
    """`DHM_PRECIP_XLSX` is unset. Exit code 2 (D9)."""


class SourceUnreadableError(DhmPrecipLoaderError):
    """The path is set but does not resolve to a readable file. Exit code 2 (D9)."""


class Sha256MismatchError(DhmPrecipLoaderError):
    """The file at the path does not hash to the expected digest. Exit code 3 (D9)."""


class SchemaMismatchError(DhmPrecipLoaderError):
    """Column inventory or shape mismatch (D9 exit 4)."""


class ParseFailureError(DhmPrecipLoaderError):
    """Workbook parse failure, or a cell failed to cast to a number (D9 exit 5)."""


def resolve_source_path(env: dict[str, str] | None = None) -> Path:
    """`DHM_PRECIP_XLSX` — required, never defaulted silently (constraint 1)."""
    active_env = env if env is not None else os.environ
    raw = active_env.get("DHM_PRECIP_XLSX")
    if not raw:
        raise SourcePathUnsetError("DHM_PRECIP_XLSX is unset")
    return Path(raw)


def resolve_coords_path(env: dict[str, str] | None = None) -> Path:
    """`DHM_PRECIP_COORDS`, default `data/dhm_precip/station_coordinates.csv` (D12)."""
    active_env = env if env is not None else os.environ
    raw = active_env.get("DHM_PRECIP_COORDS")
    return Path(raw) if raw else DEFAULT_COORDS_PATH


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        # A permission error (or any other filesystem access failure) on an
        # otherwise-existing path is an UNREADABLE source (exit 2, D9), never
        # a parse failure (exit 5) — the content was never even reached.
        raise SourceUnreadableError(f"source file not readable: {path}: {exc}") from exc
    return digest.hexdigest()


def _strip_mm_suffix(column: str) -> str:
    if not column.endswith(STATION_SUFFIX):
        raise SchemaMismatchError(
            f"workbook column {column!r} does not end with {STATION_SUFFIX!r}"
        )
    return column[: -len(STATION_SUFFIX)]


def load_long_frame(
    path: Path, *, expected_sha256: str
) -> tuple[pl.DataFrame, LongFrameInventory]:
    """D3 — sha256-verify, parse, and reshape into the canonical long frame.

    Returns `(frame, inventory)` where `frame` has columns
    `(source_row_index, station, timestamp, value_mm)` and `station` is the
    canonical (suffix-stripped) station name shared with the D12 coordinate
    table. `expected_sha256` is the D9b digest-injection seam — the CLI
    always supplies `PRODUCTION_SOURCE_SHA256`; tests inject a fixture digest.
    """
    if not path.exists() or not path.is_file():
        raise SourceUnreadableError(f"source file not found or not a file: {path}")

    actual_sha256 = compute_sha256(path)
    if actual_sha256 != expected_sha256:
        raise Sha256MismatchError(
            f"sha256 mismatch for {path}: expected {expected_sha256}, "
            f"got {actual_sha256}"
        )

    try:
        raw = pl.read_excel(path)
    except Exception as exc:  # noqa: BLE001 — any parse-time failure becomes a typed error
        raise ParseFailureError(f"failed to parse workbook {path}: {exc}") from exc

    if raw.width == 0 or raw.columns[0] != TIME_COLUMN:
        raise SchemaMismatchError(
            f"expected first column {TIME_COLUMN!r}, got "
            f"{raw.columns[:1] if raw.width else '<empty>'}"
        )
    workbook_columns = tuple(raw.columns[1:])
    if workbook_columns != EXPECTED_WORKBOOK_COLUMNS:
        raise SchemaMismatchError(
            "workbook station-column inventory != the committed 37-name list "
            f"(got {len(workbook_columns)} columns)"
        )

    casted = raw.with_columns(
        [pl.col(c).cast(pl.Float64, strict=False).alias(c) for c in workbook_columns]
    )
    for column in workbook_columns:
        cast_failed = raw[column].is_not_null() & casted[column].is_null()
        if cast_failed.sum() > 0:
            raise ParseFailureError(
                f"column {column!r} contains a non-numeric value that failed to cast"
            )

    # A cell holding the literal string "NaN" casts successfully to a float
    # NaN — not a cast failure — but must never survive as a numeric reading:
    # it silently poisons every downstream sum/quantile it touches. Treat it
    # as missing, same as a null cell. Discovered against the real workbook
    # (`Lete (FNEP) (mm)`, 146 cells) during Phase 4a reconciliation.
    casted = casted.with_columns(
        [
            pl.when(pl.col(c).is_nan()).then(None).otherwise(pl.col(c)).alias(c)
            for c in workbook_columns
        ]
    )

    empty_columns = tuple(
        _strip_mm_suffix(c)
        for c in workbook_columns
        if casted[c].drop_nulls().len() == 0
    )

    canonical_names = {c: _strip_mm_suffix(c) for c in workbook_columns}
    indexed = casted.with_row_index(name="source_row_index").with_columns(
        pl.col("source_row_index").cast(pl.Int64)
    )
    long_frame = (
        indexed.unpivot(
            index=["source_row_index", TIME_COLUMN],
            on=workbook_columns,
            variable_name="station",
            value_name="value_mm",
        )
        .with_columns(
            pl.col("station").replace_strict(canonical_names, return_dtype=pl.Utf8),
            pl.col(TIME_COLUMN).alias("timestamp"),
        )
        .drop(TIME_COLUMN)
        .select(["source_row_index", "station", "timestamp", "value_mm"])
    )

    inventory = LongFrameInventory(
        all_columns=tuple(canonical_names.values()),
        empty_columns=empty_columns,
        total_rows=raw.height,
    )
    log.info(
        "dhm_precip.loader.loaded_long_frame",
        total_rows=inventory.total_rows,
        empty_columns=len(inventory.empty_columns),
    )
    return long_frame, inventory


def load_station_coordinates(
    path: Path, *, expected_stations: frozenset[Station]
) -> StationCoordinateTable:
    """D12 — load and schema-validate the coordinate table; assert the exact
    live station set. Absence is a typed loader error, never a skip."""
    if not path.exists() or not path.is_file():
        raise SourceUnreadableError(f"coordinate table not found or not a file: {path}")

    try:
        df = pl.read_csv(path)
    except OSError as exc:
        # Readable-per-exists()-but-not-actually-readable (e.g. a permission
        # error) is an UNREADABLE source (exit 2, D9), never a parse failure
        # (exit 5) — the content was never even reached.
        raise SourceUnreadableError(
            f"coordinate table not readable: {path}: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — any other parse-time failure becomes a typed error
        raise ParseFailureError(
            f"failed to parse coordinate table {path}: {exc}"
        ) from exc

    if set(df.columns) != set(_COORDS_REQUIRED_COLUMNS):
        raise SchemaMismatchError(
            f"coordinate table columns {set(df.columns)} != required "
            f"{_COORDS_REQUIRED_COLUMNS}"
        )

    by_station: dict[Station, StationCoordinate] = {}
    for row in df.iter_rows(named=True):
        station = Station(str(row["station"]))
        try:
            coord = StationCoordinate(
                station=station,
                excel_col=str(row["excel_col"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                elev_m=float(row["elev"]),
            )
        except ValueError as exc:
            raise SchemaMismatchError(
                f"invalid coordinate row for {station!r}: {exc}"
            ) from exc
        if station in by_station:
            raise SchemaMismatchError(
                f"duplicate station in coordinate table: {station!r}"
            )
        by_station[station] = coord

    table = StationCoordinateTable(by_station=by_station)
    actual_stations = frozenset(table.stations)
    if actual_stations != expected_stations:
        missing = sorted(expected_stations - actual_stations)
        extra = sorted(actual_stations - expected_stations)
        raise SchemaMismatchError(
            f"coordinate table station set does not match the live station set: "
            f"missing={missing} extra={extra}"
        )
    return table
