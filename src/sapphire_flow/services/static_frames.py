from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId


def align_null_static_types(
    frames: list[tuple[StationId, pl.DataFrame]],
) -> list[tuple[StationId, pl.DataFrame]]:
    concrete: dict[str, set[pl.DataType]] = {}
    for _, frame in frames:
        for name, dtype in frame.schema.items():
            if dtype != pl.Null:
                concrete.setdefault(name, set()).add(dtype)
    peer_types = {
        name: next(iter(dtypes))
        for name, dtypes in concrete.items()
        if len(dtypes) == 1
    }
    return [
        (
            sid,
            frame.with_columns(
                pl.col(name).cast(peer_types[name])
                for name, dtype in frame.schema.items()
                if dtype == pl.Null and name in peer_types
            ),
        )
        for sid, frame in frames
    ]
