"""Domain types for the M-A1 DHM precipitation pipeline (Plan 170 task 1b).

D6 (views), D6b (grains), D7 (AxisStatus), D9 (RunManifest). No I/O here —
pure type definitions, consumed by every other module in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, NewType

if TYPE_CHECKING:
    from datetime import datetime

Station = NewType("Station", str)


class View(StrEnum):
    """D6 — the two named views. Every statistic declares which one it used."""

    RAW = "RAW"
    ON_GRID = "ON_GRID"


class AxisStatus(StrEnum):
    """D7 — carried as a column on every result row, never a table attribute."""

    AXIS_INDEPENDENT = "AXIS_INDEPENDENT"
    RAW_AXIS_DIAGNOSTIC = "RAW_AXIS_DIAGNOSTIC"
    RAW_PROVISIONAL = "RAW_PROVISIONAL"
    NORMALIZED = "NORMALIZED"
    """Plan 172 (M-A2) — the canonical gap-explicit hourly axis and its
    provenance record. Neither RAW nor ON_GRID's other statuses can
    represent a reindexed dataset (it is neither a raw diagnostic nor a
    provisional statistic — it IS the axis)."""


class Grain(StrEnum):
    """D6b — the three named grains. Every expectation declares its grain."""

    SOURCE_TIMESTAMP_ROWS = "source_timestamp_rows"
    STATION_TIMESTAMP_CELLS = "station_timestamp_cells"
    NON_NULL_OBSERVATIONS = "non_null_observations"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationCoordinate:
    """One row of the D12 coordinate table."""

    station: Station
    excel_col: str
    lat: float
    lon: float
    elev_m: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"latitude {self.lat} out of range for {self.station}")
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"longitude {self.lon} out of range for {self.station}")
        if self.elev_m < -420.0 or self.elev_m > 9000.0:
            # Dead Sea shore to above Everest — a generous sanity band, not a
            # Nepal-specific bound (D12 only requires "inside Nepal").
            raise ValueError(
                f"elevation {self.elev_m} m implausible for {self.station}"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class StationCoordinateTable:
    """D12 — the validated coordinate table, loaded once by 1a and passed in (D4)."""

    by_station: dict[Station, StationCoordinate]

    def __post_init__(self) -> None:
        if len(self.by_station) == 0:
            raise ValueError("station coordinate table must not be empty")

    @property
    def stations(self) -> tuple[Station, ...]:
        return tuple(self.by_station.keys())


@dataclass(frozen=True, kw_only=True, slots=True)
class LongFrameInventory:
    """D3 — the canonical long frame's fixed inventory, computed once at load time."""

    all_columns: tuple[str, ...]
    """The 37-name expected inventory, in file order (D3)."""
    empty_columns: tuple[str, ...]
    """Columns with zero non-null cells across the file — present, never dropped."""
    total_rows: int
    """`source_timestamp_rows` grain."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ViewCounts:
    """D6b — the three grain counts for one view."""

    source_timestamp_rows: int
    station_timestamp_cells: int
    non_null_observations: int


@dataclass(frozen=True, kw_only=True, slots=True)
class NormalisationProvenance:
    """D3/D6 (Plan 172, Phase 2a) — accompanies the normalised hourly axis
    (M-A2). The two off-grid count grains are the RAW view's own D3 numbers
    (never re-derived here); `period_ending` records M-D3's answer as a
    stated fact, with its source, never as an assumption."""

    off_grid_source_timestamp_rows: int
    """D3 grain: workbook rows whose minute != on_grid_minute."""
    off_grid_non_null_observations: int
    """D3 grain: non-null cells within those off-grid rows."""
    period_ending: bool
    period_ending_source: str


@dataclass(frozen=True, kw_only=True, slots=True)
class TableDeclaration:
    """D9 — a runner-written table's declared (view, axis_status) contents."""

    name: str
    view_axis_pairs: tuple[tuple[View, AxisStatus], ...]


# JSON-serialisable scalar or [min, max] range — this is the boundary
# representation written to results.json (D9); ExpectationEvaluator re-reads
# it via a pydantic model (evaluate.py), never as a bare primitive inside
# statistic logic (D4 functions return typed Polars frames, not this).
ExpectationValueScalar = float | int | str
ExpectationValueRange = tuple[float, float]


@dataclass(frozen=True, kw_only=True, slots=True)
class RunManifest:
    """D9 — what the runner serialises to `results.json`, and the gate reads back."""

    run_id: str
    source_path: str
    source_sha256: str
    generated_at: datetime
    parameters: dict[str, object]
    counts_by_view: dict[str, ViewCounts] = field(
        default_factory=dict[str, "ViewCounts"]
    )
    tables: tuple[TableDeclaration, ...] = ()
    values: dict[str, ExpectationValueScalar | ExpectationValueRange | None] = field(
        default_factory=dict[
            str, "ExpectationValueScalar | ExpectationValueRange | None"
        ]
    )
    """`None` means "not computable for this run" — see pipeline.ExpectationValue."""
