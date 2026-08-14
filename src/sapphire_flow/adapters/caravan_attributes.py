# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""T1 (Plan 155, closes G12) — parse the delivered Caravan CAMELS-CH
attributes parquet.

Source: a Caravan PASSTHROUGH (not a re-extraction -- see the plan's
"Provenance of the Swiss parquet" section), 296 rows x 216 cols,
``gauge_id`` = ``caravan_camels_ch_<BAFU code>``. Pure parsing only -- no DB
access; the write side lives in ``store/caravan_import.py``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)

_GAUGE_ID_PREFIX = "caravan_camels_ch_"


def parse_bafu_code(gauge_id: str) -> str:
    """The identity join key (Plan 155 T1b step 1): Caravan's
    ``caravan_camels_ch_<BAFU code>`` -> the bare BAFU station code our
    ``stations.code`` column carries."""
    if not gauge_id.startswith(_GAUGE_ID_PREFIX):
        raise ValueError(
            f"gauge_id {gauge_id!r} does not carry the expected "
            f"{_GAUGE_ID_PREFIX!r} prefix -- refusing to guess a BAFU code"
        )
    return gauge_id.removeprefix(_GAUGE_ID_PREFIX)


def load_caravan_attribute_rows(path: str | Path) -> dict[str, dict[str, Any]]:
    """Parse the parquet into a per-BAFU-code dict of RAW (unprefixed)
    column -> sanitised value.

    Every column is carried through -- T1b step 4, "store all 216 under the
    prefix": a namespaced key cannot collide, so breadth costs only rows.
    NaN/Inf are sanitised to ``None`` (non-finite floats are invalid JSON),
    mirroring ``adapters/camelsch_adapter.py::geometry_to_basin``.
    """
    df = pd.read_parquet(path)
    if "gauge_id" not in df.columns:
        raise ValueError(
            f"Caravan attributes parquet at {path} has no 'gauge_id' column"
        )

    rows: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = parse_bafu_code(str(row["gauge_id"]))
        if code in rows:
            raise ValueError(
                f"duplicate BAFU code {code!r} in Caravan attributes "
                f"parquet at {path} -- refusing to silently pick one row"
            )
        raw = row.to_dict()
        rows[code] = {
            col: (None if isinstance(val, float) and not math.isfinite(val) else val)
            for col, val in raw.items()
        }
    log.info(
        "caravan_attributes.parsed",
        path=str(path),
        row_count=len(rows),
    )
    return rows
