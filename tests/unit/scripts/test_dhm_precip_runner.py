"""Task 3a — runner and artefacts. Synthetic workbook only (constraint 1).

`run.PRODUCTION_SOURCE_SHA256` is monkeypatched to the fixture's own digest —
the module-level pinned constant is D9b's "not exposed via CLI or
environment" seam, so a synthetic-file test must patch the constant itself
rather than pass a flag.
"""

from __future__ import annotations

import random
from datetime import datetime

import polars as pl
import pytest

from scripts.dhm_precip import run as run_module
from scripts.dhm_precip.fixtures import (
    build_synthetic_workbook_frame,
    write_synthetic_coordinates,
    write_synthetic_workbook,
)
from scripts.dhm_precip.loader import EXPECTED_WORKBOOK_COLUMNS, compute_sha256
from scripts.dhm_precip.manifest_io import read_manifest
from scripts.dhm_precip.pipeline import ComputedTables


@pytest.fixture()
def synthetic_run_inputs(tmp_path, monkeypatch):
    rng = random.Random(158)
    empty = (EXPECTED_WORKBOOK_COLUMNS[5], EXPECTED_WORKBOOK_COLUMNS[6])
    frame = build_synthetic_workbook_frame(
        start=datetime(2024, 6, 1),
        n_hours=200,
        rng=rng,
        empty_stations=empty,
    )
    xlsx_path = tmp_path / "synthetic.xlsx"
    write_synthetic_workbook(xlsx_path, frame)
    usable = tuple(c for c in EXPECTED_WORKBOOK_COLUMNS if c not in empty)
    coords_path = tmp_path / "coords.csv"
    write_synthetic_coordinates(coords_path, usable_columns=usable)

    digest = compute_sha256(xlsx_path)
    monkeypatch.setattr(run_module, "PRODUCTION_SOURCE_SHA256", digest)
    monkeypatch.setenv("DHM_PRECIP_XLSX", str(xlsx_path))
    monkeypatch.setenv("DHM_PRECIP_COORDS", str(coords_path))
    return tmp_path


class TestRunnerExitsZeroAndWritesArtefacts:
    """The plan's task 3a verification: exits 0, writes the declared artefacts."""

    def test_run_exits_zero(self, synthetic_run_inputs, tmp_path) -> None:
        out = tmp_path / "out"
        exit_code = run_module.run(out)
        assert exit_code == 0

    def test_run_writes_results_json_and_summary(
        self, synthetic_run_inputs, tmp_path
    ) -> None:
        out = tmp_path / "out"
        run_module.run(out)
        assert (out / "results.json").exists()
        assert (out / "summary.md").exists()

    def test_run_writes_one_parquet_per_declared_table(
        self, synthetic_run_inputs, tmp_path
    ) -> None:
        out = tmp_path / "out"
        run_module.run(out)
        manifest = read_manifest(out / "results.json")
        for table in manifest.tables:
            path = out / "tables" / f"{table.name}.parquet"
            assert path.exists(), table.name

    def test_every_declared_table_has_only_its_declared_view_axis_pairs(
        self, synthetic_run_inputs, tmp_path
    ) -> None:
        out = tmp_path / "out"
        run_module.run(out)
        manifest = read_manifest(out / "results.json")
        for table in manifest.tables:
            frame = pl.read_parquet(out / "tables" / f"{table.name}.parquet")
            observed = {
                (row[0], row[1])
                for row in frame.select("view", "axis_status").unique().rows()
            }
            declared = {(v.value, a.value) for v, a in table.view_axis_pairs}
            assert observed == declared, table.name

    def test_manifest_records_the_injected_digest_and_source_path(
        self, synthetic_run_inputs, tmp_path
    ) -> None:
        out = tmp_path / "out"
        run_module.run(out)
        manifest = read_manifest(out / "results.json")
        assert manifest.source_sha256 == run_module.PRODUCTION_SOURCE_SHA256
        assert manifest.source_path.endswith("synthetic.xlsx")

    def test_all_computed_tables_are_declared(
        self, synthetic_run_inputs, tmp_path
    ) -> None:
        from dataclasses import fields

        out = tmp_path / "out"
        run_module.run(out)
        manifest = read_manifest(out / "results.json")
        declared_names = {t.name for t in manifest.tables}
        all_names = {f.name for f in fields(ComputedTables)}
        # Every table with view/axis_status columns must be declared; all of
        # ours carry those columns (D9: "each table declares the set...").
        assert declared_names == all_names


class TestRunnerExitCodes:
    def test_unset_source_path_exits_2(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("DHM_PRECIP_XLSX", raising=False)
        exit_code = run_module.run(tmp_path / "out")
        assert exit_code == 2

    def test_sha256_mismatch_exits_3(self, tmp_path, monkeypatch) -> None:
        rng = random.Random(1)
        frame = build_synthetic_workbook_frame(
            start=datetime(2024, 6, 1), n_hours=10, rng=rng
        )
        xlsx_path = tmp_path / "x.xlsx"
        write_synthetic_workbook(xlsx_path, frame)
        monkeypatch.setattr(run_module, "PRODUCTION_SOURCE_SHA256", "0" * 64)
        monkeypatch.setenv("DHM_PRECIP_XLSX", str(xlsx_path))
        exit_code = run_module.run(tmp_path / "out")
        assert exit_code == 3
