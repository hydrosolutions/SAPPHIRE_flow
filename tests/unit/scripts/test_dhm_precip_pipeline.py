"""Plan 173 (M-A3) fixer round — major-3: `table_declarations()` must not
lose a table's `(view, axis_status)` declaration when the table is
genuinely empty (zero rows). Targets `_qc_mask_table`/`_qc_exclusion_list_table`
directly, since neither the mask nor the exclusion list is reliably empty
in the runner's synthetic fixture (that's covered, best-effort, in
`test_dhm_precip_runner.py`).
"""

from __future__ import annotations

from dataclasses import fields

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import AxisStatus, View
from scripts.dhm_precip.pipeline import (
    ComputedTables,
    _qc_exclusion_list_table,
    _qc_mask_table,
    table_declarations,
)


def _blank_computed_tables(**overrides: pl.DataFrame) -> ComputedTables:
    """Every non-overridden field gets a plain empty `pl.DataFrame` — no
    `view`/`axis_status` columns, so `table_declarations` skips it. Only the
    field(s) under test carry real content."""
    kwargs: dict[str, pl.DataFrame] = {
        f.name: pl.DataFrame() for f in fields(ComputedTables)
    }
    kwargs.update(overrides)
    return ComputedTables(**kwargs)  # type: ignore[arg-type]


class TestEmptyArtefactsStillDeclareTheirAxisPair:
    def test_an_empty_qc_mask_still_declares_on_grid_normalized(self) -> None:
        frame = _qc_mask_table(frozenset())
        assert frame.height == 0  # the case that broke row-derived declarations

        tables = _blank_computed_tables(qc_mask=frame)
        declared = {d.name: d.view_axis_pairs for d in table_declarations(tables)}

        assert declared["qc_mask"] == ((View.ON_GRID, AxisStatus.NORMALIZED),)

    def test_an_empty_qc_exclusion_list_still_declares_on_grid_normalized(
        self,
    ) -> None:
        frame = _qc_exclusion_list_table(())
        assert frame.height == 0

        tables = _blank_computed_tables(qc_exclusion_list=frame)
        declared = {d.name: d.view_axis_pairs for d in table_declarations(tables)}

        assert declared["qc_exclusion_list"] == ((View.ON_GRID, AxisStatus.NORMALIZED),)

    def test_a_non_empty_table_declares_the_same_pair_as_an_empty_one(self) -> None:
        # Row presence must not change WHICH pair is declared — only
        # whether it would previously have been discoverable at all.
        from datetime import UTC, datetime

        from scripts.dhm_precip.domain_types import Station

        non_empty_mask = frozenset({(Station("A"), datetime(2024, 7, 1, tzinfo=UTC))})
        frame = _qc_mask_table(non_empty_mask)
        assert frame.height == 1

        tables = _blank_computed_tables(qc_mask=frame)
        declared = {d.name: d.view_axis_pairs for d in table_declarations(tables)}

        assert declared["qc_mask"] == ((View.ON_GRID, AxisStatus.NORMALIZED),)


if __name__ == "__main__":
    pytest.main([__file__])
