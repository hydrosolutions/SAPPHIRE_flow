from __future__ import annotations

from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.static_frames import align_null_static_types
from sapphire_flow.types.ids import StationId


def test_aligns_null_metadata_without_filling_missing_values() -> None:
    sid = StationId(uuid4())
    frames = [
        (sid, pl.DataFrame({"metadata": [None]})),
        (sid, pl.DataFrame({"metadata": [2.0]})),
    ]
    result = pl.concat([frame for _, frame in align_null_static_types(frames)])
    assert result.to_dict(as_series=False) == {"metadata": [None, 2.0]}


def test_conflicting_concrete_types_are_not_coerced() -> None:
    sid = StationId(uuid4())
    frames = [
        (sid, pl.DataFrame({"metadata": [2]})),
        (sid, pl.DataFrame({"metadata": [2.0]})),
    ]
    with pytest.raises(pl.exceptions.SchemaError, match="type"):
        pl.concat([frame for _, frame in align_null_static_types(frames)])
