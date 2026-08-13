"""Dev-only synthetic workbook fixture builder (constraint 3, D1).

Reproduces each defect *signature* — a pinned-value block, a sentinel run —
never real DHM values (constraint 1). Depends on the dev-only `xlsxwriter`
group; imported by tests only, never by `run.py` or `evaluate.py`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.loader import EXPECTED_WORKBOOK_COLUMNS, TIME_COLUMN

if TYPE_CHECKING:
    import random
    from datetime import datetime
    from pathlib import Path

DEFAULT_SENTINEL_STATION = EXPECTED_WORKBOOK_COLUMNS[3]  # "Lukla Airport (mm)"
DEFAULT_STUCK_HIGH_STATION = EXPECTED_WORKBOOK_COLUMNS[24]  # "Sindhuli Madhi (mm)"


def build_synthetic_workbook_frame(
    *,
    start: datetime,
    n_hours: int,
    rng: random.Random,
    sentinel_value: float = -9999999.0,
    sentinel_station: str = DEFAULT_SENTINEL_STATION,
    sentinel_count: int = 5,
    stuck_high_station: str = DEFAULT_STUCK_HIGH_STATION,
    stuck_high_value: float = 72.0,
    stuck_high_run_hours: int = 6,
    zero_run_station: str | None = None,
    zero_run_hours: int = 0,
    empty_stations: tuple[str, ...] = (),
) -> pl.DataFrame:
    """A full 38-column synthetic frame matching `EXPECTED_WORKBOOK_COLUMNS`,
    with named defect signatures injected at known offsets — never real values.
    """
    timestamps = [start + timedelta(hours=i) for i in range(n_hours)]
    data: dict[str, list[float | None]] = {}
    for index, col in enumerate(EXPECTED_WORKBOOK_COLUMNS):
        if col in empty_stations:
            data[col] = [None] * n_hours
            continue
        # Alternate reporting resolution by station index so a synthetic
        # frame always contains both Group A (0.01 mm) and Group B (0.2 mm)
        # stations — real data always has both; a same-resolution-only
        # fixture would silently exercise a narrower code path than reality.
        resolution = 0.01 if index % 2 == 0 else 0.2
        data[col] = [
            round(round(rng.uniform(0.0, 5.0) / resolution) * resolution, 2)
            if rng.random() < 0.3
            else 0.0
            for _ in range(n_hours)
        ]

    if sentinel_station not in empty_stations:
        for i in range(min(sentinel_count, n_hours)):
            data[sentinel_station][i] = sentinel_value

    if stuck_high_station not in empty_stations:
        offset = n_hours // 2
        for i in range(offset, min(offset + stuck_high_run_hours, n_hours)):
            data[stuck_high_station][i] = stuck_high_value

    if zero_run_station is not None and zero_run_station not in empty_stations:
        for i in range(min(zero_run_hours, n_hours)):
            data[zero_run_station][i] = 0.0

    frame = pl.DataFrame(data)
    return frame.with_columns(pl.Series(TIME_COLUMN, timestamps)).select(
        [TIME_COLUMN, *EXPECTED_WORKBOOK_COLUMNS]
    )


def write_synthetic_workbook(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_excel(path)


def write_synthetic_coordinates(path: Path, *, usable_columns: tuple[str, ...]) -> None:
    """A D12 coordinate table for exactly the given (non-empty) workbook
    columns — synthetic lat/lon spread over a small Nepal-like grid, never
    real station coordinates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = pl.DataFrame(
        {
            "station": [col.removesuffix(" (mm)") for col in usable_columns],
            "excel_col": list(usable_columns),
            "lat": [27.0 + 0.1 * i for i in range(len(usable_columns))],
            "lon": [85.0 + 0.1 * i for i in range(len(usable_columns))],
            "elev": [500.0 + 100.0 * i for i in range(len(usable_columns))],
        }
    )
    rows.write_csv(path)
