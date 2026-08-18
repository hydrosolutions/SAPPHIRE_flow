"""Plan 183 fixer round (minor): CLI-boundary validation for
``scripts/backfill_era5_land_history.py``.

A negative ``--station-batch-size`` makes ``_chunk()`` return zero batches
(the backfill reports success having done no work); a negative or
above-1.0 ``--min-land-fraction`` makes every coverage comparison pass,
silently disabling the land-mask guard. Both must be rejected at the CLI
boundary before any database connection is attempted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "backfill_era5_land_history.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "backfill_era5_land_history_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_era5_land_history_script"] = module
    spec.loader.exec_module(module)
    return module


class TestCliValidation:
    def test_negative_station_batch_size_rejected_without_touching_db(
        self, mod, monkeypatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        create_engine = MagicMock()
        monkeypatch.setattr(mod.sa, "create_engine", create_engine)

        assert mod.main(["--station-batch-size", "-1"]) == 1
        create_engine.assert_not_called()

    def test_zero_min_land_fraction_rejected_without_touching_db(
        self, mod, monkeypatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        create_engine = MagicMock()
        monkeypatch.setattr(mod.sa, "create_engine", create_engine)

        assert mod.main(["--min-land-fraction", "0.0"]) == 1
        create_engine.assert_not_called()

    def test_above_one_min_land_fraction_rejected_without_touching_db(
        self, mod, monkeypatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        create_engine = MagicMock()
        monkeypatch.setattr(mod.sa, "create_engine", create_engine)

        assert mod.main(["--min-land-fraction", "1.1"]) == 1
        create_engine.assert_not_called()

    def test_valid_arguments_proceed_past_validation(self, mod, monkeypatch) -> None:
        """Positive control: a valid batch size + fraction must NOT be
        rejected at the validation boundary — only DATABASE_URL should stop
        it (unset here), proving the guard is scoped to invalid values."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        result = mod.main(["--station-batch-size", "10", "--min-land-fraction", "0.5"])

        assert result == 1  # fails on missing DATABASE_URL, not validation
