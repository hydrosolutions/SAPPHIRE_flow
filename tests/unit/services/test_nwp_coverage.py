from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from sapphire_flow.services.nwp_coverage import assess_future_coverage
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleMode

_TS = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


class TestAssessFutureCoverageEmptyFrame:
    """Plan 145 D3.2d: an EMPTY ``future_dynamic`` frame (the relaxed assembly
    guard's log-and-continue path) must suppress a model requiring a future
    feature — never raise, never silently pass — so the per-model gate (not the
    assembly guard) is what advances the fallback loop."""

    def test_empty_frame_required_feature_absent_is_inadequate(self) -> None:
        result = assess_future_coverage(
            pl.DataFrame(),
            required_features=frozenset({"swe"}),
            required_steps=5,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        assert result.adequate is False
        assert result.available_steps == 0
        assert "swe" in result.detail
        assert "absent" in result.detail

    def test_empty_frame_ensemble_model_required_feature_absent_is_inadequate(
        self,
    ) -> None:
        result = assess_future_coverage(
            pl.DataFrame(),
            required_features=frozenset({"precipitation"}),
            required_steps=5,
            ensemble_mode=EnsembleMode.ENSEMBLE,
        )
        assert result.adequate is False
        assert result.available_steps == 0

    def test_no_required_features_is_adequate_even_on_empty_frame(self) -> None:
        result = assess_future_coverage(
            pl.DataFrame(),
            required_features=frozenset(),
            required_steps=5,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        assert result.adequate is True

    def test_adequate_when_enough_clean_rows_present(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [_TS, _TS, _TS],
                "swe": [1.0, 2.0, 3.0],
            }
        )
        result = assess_future_coverage(
            df,
            required_features=frozenset({"swe"}),
            required_steps=3,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        assert result.adequate is True
        assert result.available_steps == 3
