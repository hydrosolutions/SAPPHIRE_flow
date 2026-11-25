from __future__ import annotations

import pytest

from sapphire_flow.services.threshold_probability import threshold_probability
from sapphire_flow.types.enums import EnsembleRepresentation as Representation
from sapphire_flow.types.probability import ProbabilityMethod


class TestThresholdProbability:
    @pytest.mark.parametrize(
        "threshold,point,lower,upper",
        [
            (5, None, 0.95, 1),
            (10, 0.95, 0.5, 0.95),
            (15, 0.725, 0.5, 0.95),
            (20, 0.5, 0.05, 0.5),
            (25, 0.275, 0.05, 0.5),
            (30, None, 0, 0.05),
            (100, None, 0, 0.05),
        ],
    )
    def test_unequal_quantile_spacing_and_tails(
        self,
        threshold: float,
        point: float | None,
        lower: float,
        upper: float,
    ) -> None:
        result = threshold_probability(
            Representation.QUANTILES, [(0.05, 10), (0.5, 20), (0.95, 30)], threshold
        )
        if point is None:
            assert result.probability is None
        else:
            assert result.probability == pytest.approx(point)
        assert (result.lower, result.upper) == pytest.approx((lower, upper))

    def test_rightmost_tie_for_strict_exceedance(self) -> None:
        result = threshold_probability(
            Representation.QUANTILES, [(0.05, 0), (0.5, 0), (0.95, 10)], 0
        )
        assert result.probability == 0.5
        assert (result.lower, result.upper) == pytest.approx((0.05, 0.5))
        assert result.method is ProbabilityMethod.INTERPOLATED_QUANTILES

    @pytest.mark.parametrize(
        "threshold,lower,upper", [(-1, 0.95, 1), (0, 0, 0.05), (1, 0, 0.05)]
    )
    def test_all_equal_remains_bounded(
        self, threshold: float, lower: float, upper: float
    ) -> None:
        result = threshold_probability(
            Representation.QUANTILES, [(0.05, 0), (0.95, 0)], threshold
        )
        assert result.probability is None
        assert (result.lower, result.upper) == pytest.approx((lower, upper))

    @pytest.mark.parametrize("threshold,expected", [(-1, 1), (10, 0), (11, 0)])
    def test_full_support_collapsed_tail_bounds(
        self, threshold: float, expected: float
    ) -> None:
        result = threshold_probability(
            Representation.QUANTILES, [(0, 0), (1, 10)], threshold
        )
        assert result.probability == expected
        assert result.lower == result.upper == expected

    def test_members_use_all_samples_with_strict_equality(self) -> None:
        result = threshold_probability(
            Representation.MEMBERS, [(0, 0), (2, 5), (9, 10)], 5
        )
        assert result.probability == result.lower == result.upper == 1 / 3

    @pytest.mark.parametrize(
        "values",
        [
            [],
            [(0.5, 1)],
            [(0.5, 1), (0.5, 2)],
            [(0.1, 2), (0.9, 1)],
            [(-0.1, 1), (0.9, 2)],
            [(0.1, float("nan")), (0.9, 2)],
        ],
    )
    def test_invalid_quantiles_are_unavailable(
        self, values: list[tuple[float, float]]
    ) -> None:
        result = threshold_probability(Representation.QUANTILES, values, 1)
        assert result.method is ProbabilityMethod.UNAVAILABLE
        assert result.probability is result.lower is result.upper is None

    def test_nonfinite_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match="threshold must be finite"):
            threshold_probability(Representation.MEMBERS, [(0, 1)], float("nan"))

    def test_extreme_finite_knots_do_not_overflow(self) -> None:
        result = threshold_probability(
            Representation.QUANTILES, [(0.1, -1e308), (0.9, 1e308)], 0
        )
        assert result.probability == pytest.approx(0.5)
