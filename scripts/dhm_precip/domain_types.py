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


class AccumulationConvention(StrEnum):
    """How a timestamp relates to the interval it summarises. A `bool` cannot
    express this: `False` would conflate period-starting, instantaneous and
    unknown, and CLAUDE.md forbids a bool for a domain state with named
    possibilities. DHM answered PERIOD_ENDING for this dataset (M-D3), and
    ERA5-Land shares it — which is why M-A6 needs no offset."""

    PERIOD_ENDING = "period_ending"
    PERIOD_BEGINNING = "period_beginning"
    INSTANTANEOUS = "instantaneous"
    UNKNOWN = "unknown"


class OrographySource(StrEnum):
    """Plan 174 (M-A5) D3a — which physical quantity a station's
    `orography_elev_m` actually is. Model orography is what the ERA5-Land
    land-surface scheme ran on; a DEM proxy is what the terrain actually is.
    The distinction is load-bearing (never a bool): a downstream consumer
    must not read the elevation-mismatch number without reading which of
    these two it is measuring against."""

    MODEL_OROGRAPHY = "MODEL_OROGRAPHY"
    DEM_PROXY = "DEM_PROXY"


class VerticalDatum(StrEnum):
    """Plan 174 (M-A5) D3b — the vertical reference of an elevation value.
    `UNKNOWN` is the honest value for the station side today (DHM has not
    stated one); the orography side carries whichever this StrEnum member
    the producer's own documentation states, recorded verbatim (D3a)."""

    EGM96 = "EGM96"
    EGM2008 = "EGM2008"
    WGS84_ELLIPSOID = "WGS84_ELLIPSOID"
    LOCAL_MSL = "LOCAL_MSL"
    UNKNOWN = "UNKNOWN"


class ExtractionOperator(StrEnum):
    """Plan 174 (M-A5) D1/D1a — the two point-extraction operators. `NEAREST`
    is THE operator (D1, locked); `BILINEAR` exists only as D1a's
    sensitivity comparand, never as an alternate primary."""

    NEAREST = "NEAREST"
    BILINEAR = "BILINEAR"


class DatumReconciliationStatus(StrEnum):
    """Plan 174 (M-A5) D3b/M-8 — whether a station's `elev_mismatch_m` sits
    on a common vertical reference. `station_elevation_datum` is `UNKNOWN`
    today (DHM has not stated one), so this is `UNRECONCILED` for every row
    until M-D2 (or DHM) states a datum AND it agrees with the orography
    side's — never a bool, per CLAUDE.md, and never silently assumed."""

    RECONCILED = "RECONCILED"
    UNRECONCILED = "UNRECONCILED"


class SensitivityScope(StrEnum):
    """Plan 174 (M-A5) D9/D1a — `operator_sensitivity.csv`'s `scope` column."""

    STATION = "STATION"
    ACROSS_STATION = "ACROSS_STATION"


class SensitivityStatistic(StrEnum):
    """Plan 174 (M-A5) D9/D1a — `operator_sensitivity.csv`'s `statistic`
    column."""

    QUANTILE = "QUANTILE"
    WET_MEAN_INTENSITY = "WET_MEAN_INTENSITY"
    WET_FREQUENCY = "WET_FREQUENCY"


class SensitivityDeltaUnit(StrEnum):
    """Plan 174 (M-A5) D9 — `operator_sensitivity.csv`'s `delta_unit` column
    (wet frequency is a FRACTION, not mm/h — a review finding, M-9/D9)."""

    MM_PER_H = "MM_PER_H"
    FRACTION = "FRACTION"


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
    (never re-derived here); `accumulation_convention` records M-D3's answer as a
    stated fact, with its source, never as an assumption."""

    off_grid_source_timestamp_rows: int
    """D3 grain: workbook rows whose minute != on_grid_minute."""
    off_grid_non_null_observations: int
    """D3 grain: non-null cells within those off-grid rows."""
    accumulation_convention: AccumulationConvention
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
