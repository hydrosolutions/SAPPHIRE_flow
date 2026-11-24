from __future__ import annotations

import pytest

from sapphire_flow.services.skill.quantiles import QuantileSample


class TestQuantileSample:
    def test_median_uses_probability_not_knot_position(self) -> None:
        sample = QuantileSample(
            probabilities=(0.05, 0.1, 0.5, 0.6, 0.7, 0.8, 0.95),
            values=(0, 1, 5, 6, 7, 8, 9.5),
        )
        assert sample.at(0.5) == 5
        assert sample.at(0.75) - sample.at(0.25) == pytest.approx(5)

    def test_score_weights_probability_spacing_and_clips_supported_endpoints(
        self,
    ) -> None:
        sample = QuantileSample(probabilities=(0, 0.5, 1), values=(0, 0.5, 1))
        # Trapezoidal integral of q(1-q) at .05, .5, .95, scaled by 2/.9.
        assert sample.score(0) == pytest.approx(0.2975)

    def test_point_distribution_matches_absolute_error(self) -> None:
        sample = QuantileSample(
            probabilities=(0.05, 0.12, 0.7, 0.95), values=(4, 4, 4, 4)
        )
        assert sample.score(7) == pytest.approx(3)

    @pytest.mark.parametrize(
        ("probabilities", "values", "message"),
        [
            ((0.05, 0.95), (0,), "matching"),
            ((0.05, 0.05, 0.95), (0, 1, 2), "strictly increasing"),
            ((0.05, 0.95), (2, 1), "must not cross"),
            ((0.1, 0.9), (0, 1), "must cover"),
            ((0.05, 0.95), (0, float("nan")), "finite"),
        ],
    )
    def test_invalid_samples_fail_explicitly(
        self, probabilities: tuple[float, ...], values: tuple[float, ...], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            QuantileSample(probabilities=probabilities, values=values)

    def test_cannot_extrapolate_tails(self) -> None:
        sample = QuantileSample(probabilities=(0.05, 0.95), values=(1, 10))
        with pytest.raises(ValueError, match="outside supplied support"):
            sample.at(0.99)
