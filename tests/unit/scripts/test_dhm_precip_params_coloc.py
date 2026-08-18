"""Plan 182 (M-A10) — params validation, including D9's 'threshold
coherence' invariant: D2's alignment uncertainty and D5's bootstrap
adequacy rule must both sit strictly below the 4h decision boundaries, and
the 2h refute-boundary must sit at or above both."""

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams


class TestDefaultParamsAreCoherent:
    def test_default_params_construct(self) -> None:
        assert DEFAULT_PARAMS.coloc_ablation_refuted_max_hours == 2.0
        assert DEFAULT_PARAMS.coloc_ablation_supported_min_hours == 4.0


class TestThresholdCoherenceIsEnforced:
    def test_rejects_alignment_uncertainty_at_or_above_the_ablation_boundary(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="threshold coherence"):
            replace(DEFAULT_PARAMS, coloc_alignment_uncertainty_hours=4.0)

    def test_rejects_refute_boundary_below_the_bootstrap_spread_rule(self) -> None:
        with pytest.raises(ValueError, match="threshold coherence"):
            replace(
                DEFAULT_PARAMS,
                coloc_ablation_refuted_max_hours=1.0,
                coloc_bootstrap_adequate_max_spread_hours=2.0,
            )

    def test_rejects_refuted_boundary_not_below_supported_boundary(self) -> None:
        with pytest.raises(
            ValueError, match="coloc_ablation_refuted_max_hours must be <"
        ):
            replace(
                DEFAULT_PARAMS,
                coloc_ablation_refuted_max_hours=4.0,
                coloc_ablation_supported_min_hours=4.0,
            )


class TestThresholdLadderValidation:
    def test_rejects_unsorted_ladder(self) -> None:
        with pytest.raises(ValueError, match="strictly ascending"):
            DhmPrecipParams(coloc_threshold_ladder_mm=(0.2, 0.1, 0.0))

    def test_rejects_matched_resolution_threshold_not_in_ladder(self) -> None:
        with pytest.raises(ValueError, match="coloc_matched_resolution_threshold_mm"):
            DhmPrecipParams(
                coloc_threshold_ladder_mm=(0.0, 0.1),
                coloc_matched_resolution_threshold_mm=0.2,
            )
