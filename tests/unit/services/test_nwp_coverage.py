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


class TestAssessFutureCoverageNaN:
    """Plan 145 review escalation: Polars' ``is_not_null()`` is TRUE for NaN, so
    an all-NaN (or partially-NaN) required column was previously counted as
    fully clean. A NaN forcing value is never usable -- it must be treated the
    same as a missing/null row by the shared coverage gate."""

    def test_all_nan_required_column_is_inadequate(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [_TS, _TS, _TS],
                "swe": [float("nan"), float("nan"), float("nan")],
            }
        )
        result = assess_future_coverage(
            df,
            required_features=frozenset({"swe"}),
            required_steps=3,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        assert result.adequate is False
        assert result.available_steps == 0

    def test_mixed_finite_and_nan_counts_only_finite_rows(self) -> None:
        # 2 finite + 3 NaN rows across a 5-row frame; required_steps=2 must be
        # met by the finite rows alone, and required_steps=3 must NOT be met.
        df = pl.DataFrame(
            {
                "timestamp": [_TS] * 5,
                "swe": [1.0, 2.0, float("nan"), float("nan"), float("nan")],
            }
        )
        adequate_result = assess_future_coverage(
            df,
            required_features=frozenset({"swe"}),
            required_steps=2,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        assert adequate_result.adequate is True
        assert adequate_result.available_steps == 2

        inadequate_result = assess_future_coverage(
            df,
            required_features=frozenset({"swe"}),
            required_steps=3,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        assert inadequate_result.adequate is False
        assert inadequate_result.available_steps == 2

    def test_ensemble_all_nan_member_column_is_inadequate(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [_TS, _TS, _TS],
                "precipitation_0": [float("nan"), float("nan"), float("nan")],
            }
        )
        result = assess_future_coverage(
            df,
            required_features=frozenset({"precipitation"}),
            required_steps=3,
            ensemble_mode=EnsembleMode.ENSEMBLE,
        )
        assert result.adequate is False
        assert result.available_steps == 0
