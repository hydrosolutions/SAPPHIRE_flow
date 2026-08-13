"""D9 — `RunManifest` JSON I/O. Pydantic at the `results.json` boundary
(CLAUDE.md); `RunManifest` is the domain type on either side of it."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic must resolve this at runtime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from scripts.dhm_precip.domain_types import (
    AxisStatus,
    RunManifest,
    TableDeclaration,
    View,
    ViewCounts,
)

if TYPE_CHECKING:
    from pathlib import Path


class _ViewCountsModel(BaseModel):
    source_timestamp_rows: int
    station_timestamp_cells: int
    non_null_observations: int


class _TableDeclarationModel(BaseModel):
    name: str
    view_axis_pairs: list[tuple[str, str]]


class RunManifestModel(BaseModel):
    run_id: str
    source_path: str
    source_sha256: str
    generated_at: datetime
    parameters: dict[str, object]
    counts_by_view: dict[str, _ViewCountsModel]
    tables: list[_TableDeclarationModel]
    values: dict[str, float | int | str | list[float] | None]


def write_manifest(manifest: RunManifest, path: Path) -> None:
    model = RunManifestModel(
        run_id=manifest.run_id,
        source_path=manifest.source_path,
        source_sha256=manifest.source_sha256,
        generated_at=manifest.generated_at,
        parameters=manifest.parameters,
        counts_by_view={
            key: _ViewCountsModel(
                source_timestamp_rows=counts.source_timestamp_rows,
                station_timestamp_cells=counts.station_timestamp_cells,
                non_null_observations=counts.non_null_observations,
            )
            for key, counts in manifest.counts_by_view.items()
        },
        tables=[
            _TableDeclarationModel(
                name=table.name,
                view_axis_pairs=[
                    (view.value, axis.value) for view, axis in table.view_axis_pairs
                ],
            )
            for table in manifest.tables
        ],
        values={
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in manifest.values.items()
        },
    )
    path.write_text(model.model_dump_json(indent=2))


def read_manifest(path: Path) -> RunManifest:
    model = RunManifestModel.model_validate_json(path.read_text())
    values: dict[str, float | int | str | tuple[float, float] | None] = {
        key: (tuple(value) if isinstance(value, list) else value)  # type: ignore[misc]
        for key, value in model.values.items()
    }
    return RunManifest(
        run_id=model.run_id,
        source_path=model.source_path,
        source_sha256=model.source_sha256,
        generated_at=model.generated_at,
        parameters=model.parameters,
        counts_by_view={
            key: ViewCounts(
                source_timestamp_rows=counts.source_timestamp_rows,
                station_timestamp_cells=counts.station_timestamp_cells,
                non_null_observations=counts.non_null_observations,
            )
            for key, counts in model.counts_by_view.items()
        },
        tables=tuple(
            TableDeclaration(
                name=table.name,
                view_axis_pairs=tuple(
                    (View(view), AxisStatus(axis))
                    for view, axis in table.view_axis_pairs
                ),
            )
            for table in model.tables
        ),
        values=values,
    )
