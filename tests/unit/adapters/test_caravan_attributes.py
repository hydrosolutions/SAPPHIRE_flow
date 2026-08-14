"""Plan 155 T1 — parsing tests for the delivered Caravan CAMELS-CH
attributes parquet."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from sapphire_flow.adapters.caravan_attributes import (
    load_caravan_attribute_rows,
    parse_bafu_code,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestParseBafuCode:
    def test_strips_the_caravan_prefix(self) -> None:
        assert parse_bafu_code("caravan_camels_ch_2009") == "2009"

    def test_raises_on_unexpected_prefix(self) -> None:
        with pytest.raises(ValueError, match="does not carry the expected"):
            parse_bafu_code("2009")


def _write_parquet(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "data.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path


class TestLoadCaravanAttributeRows:
    def test_parses_rows_keyed_on_bare_bafu_code(self, tmp_path: Path) -> None:
        path = _write_parquet(
            tmp_path,
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                {
                    "gauge_id": "caravan_camels_ch_2011",
                    "area": 88.0,
                    "slp_dg_sav": 5.0,
                },
            ],
        )
        rows = load_caravan_attribute_rows(path)
        assert set(rows) == {"2009", "2011"}
        assert rows["2009"]["area"] == 250.0
        assert rows["2009"]["slp_dg_sav"] == 12.5

    def test_stores_every_column_not_just_declared_statics(
        self, tmp_path: Path
    ) -> None:
        # T1b step 4: "store all 216 under the prefix" -- the loader carries
        # every column through, filtering is the CALLER's business (if any).
        path = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0, "gauge_name": "X"}],
        )
        rows = load_caravan_attribute_rows(path)
        assert rows["2009"]["gauge_name"] == "X"
        assert "gauge_id" in rows["2009"]

    def test_nan_is_sanitised_to_none(self, tmp_path: Path) -> None:
        path = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_2009", "area": math.nan}],
        )
        rows = load_caravan_attribute_rows(path)
        assert rows["2009"]["area"] is None

    def test_missing_gauge_id_column_raises(self, tmp_path: Path) -> None:
        path = _write_parquet(tmp_path, [{"area": 250.0}])
        with pytest.raises(ValueError, match="gauge_id"):
            load_caravan_attribute_rows(path)

    def test_duplicate_bafu_code_raises(self, tmp_path: Path) -> None:
        path = _write_parquet(
            tmp_path,
            [
                {"gauge_id": "caravan_camels_ch_2009", "area": 250.0},
                {"gauge_id": "caravan_camels_ch_2009", "area": 88.0},
            ],
        )
        with pytest.raises(ValueError, match="duplicate BAFU code"):
            load_caravan_attribute_rows(path)
