from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

import pytest

from sapphire_flow.services.rating_conversion import (
    ConversionResult,
    InverseRatingStatus,
    RatingConversionError,
    RatingConverter,
    RatingRange,
    convert_level_to_discharge,
)
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.ids import RatingCurveId, StationId
from sapphire_flow.types.rating_curve import RatingCurve

_NOW = datetime(2025, 1, 1, tzinfo=UTC)

# Two rows from the DHM RT_ sample (Plan 035 §1a).
_RT = [
    {"water_level": 1.0, "discharge": 59.6},
    {"water_level": 1.1, "discharge": 70.2},
    {"water_level": 1.2, "discharge": 81.5},
]


def _curve(
    points: list[dict],
    interpolation: InterpolationMethod = InterpolationMethod.LINEAR,
) -> RatingCurve:
    return RatingCurve(
        id=RatingCurveId(uuid.uuid4()),
        station_id=StationId(uuid.uuid4()),
        version=1,
        valid_from=_NOW,
        valid_to=None,
        points=points,
        interpolation=interpolation,
        uploaded_by=None,
        created_at=_NOW,
    )


class TestLinearConversion:
    def test_exact_tabulated_hit(self) -> None:
        conv = RatingConverter.from_curve(_curve(_RT))
        r = conv.convert(1.1)
        assert r == ConversionResult(discharge=70.2, range_flag=RatingRange.IN_RANGE)

    def test_interior_linear_interpolation(self) -> None:
        # Midpoint of (1.0, 59.6)-(1.1, 70.2): 59.6 + 0.5*10.6 = 64.9
        r = convert_level_to_discharge(1.05, _curve(_RT))
        assert r.range_flag is RatingRange.IN_RANGE
        assert r.discharge == pytest.approx(64.9)

    def test_convert_level_to_discharge_wrapper(self) -> None:
        r = convert_level_to_discharge(1.2, _curve(_RT))
        assert r.discharge == pytest.approx(81.5)


class TestLogLinearConversion:
    def test_interior_is_geometric_mean_at_midpoint(self) -> None:
        # log-discharge linear in stage → at the midpoint, Q = sqrt(Q0*Q1).
        conv = RatingConverter.from_curve(
            _curve(_RT, interpolation=InterpolationMethod.LOG_LINEAR)
        )
        r = conv.convert(1.05)
        assert r.range_flag is RatingRange.IN_RANGE
        assert r.discharge == pytest.approx(math.sqrt(59.6 * 70.2))

    def test_rejects_non_positive_discharge(self) -> None:
        pts = [
            {"water_level": 0.0, "discharge": 0.0},
            {"water_level": 1.0, "discharge": 59.6},
        ]
        with pytest.raises(RatingConversionError, match="log_linear"):
            RatingConverter.from_curve(
                _curve(pts, interpolation=InterpolationMethod.LOG_LINEAR)
            )


class TestOutOfRange:
    def test_below_range_clamps_to_lowest(self) -> None:
        r = RatingConverter.from_curve(_curve(_RT)).convert(0.5)
        assert r == ConversionResult(discharge=59.6, range_flag=RatingRange.BELOW)

    def test_above_range_clamps_to_highest(self) -> None:
        r = RatingConverter.from_curve(_curve(_RT)).convert(9.9)
        assert r == ConversionResult(discharge=81.5, range_flag=RatingRange.ABOVE)


class TestValidation:
    def test_empty_points(self) -> None:
        with pytest.raises(RatingConversionError, match="no points"):
            RatingConverter.from_curve(_curve([]))

    def test_duplicate_stage(self) -> None:
        pts = [
            {"water_level": 1.0, "discharge": 59.6},
            {"water_level": 1.0, "discharge": 60.0},
        ]
        with pytest.raises(RatingConversionError, match="duplicate|non-increasing"):
            RatingConverter.from_curve(_curve(pts))

    def test_discharge_decrease_rejected(self) -> None:
        pts = [
            {"water_level": 1.0, "discharge": 59.6},
            {"water_level": 1.1, "discharge": 50.0},
        ]
        with pytest.raises(RatingConversionError, match="discharge decreases"):
            RatingConverter.from_curve(_curve(pts))

    def test_discharge_ties_accepted(self) -> None:
        # Low-flow plateau: equal discharge at adjacent stages is valid.
        pts = [
            {"water_level": 1.0, "discharge": 59.6},
            {"water_level": 1.1, "discharge": 59.6},
            {"water_level": 1.2, "discharge": 70.2},
        ]
        conv = RatingConverter.from_curve(_curve(pts))
        assert conv.convert(1.05).discharge == pytest.approx(59.6)

    def test_unsorted_but_unique_points_accepted(self) -> None:
        conv = RatingConverter.from_curve(_curve(list(reversed(_RT))))
        assert conv.convert(1.05).discharge == pytest.approx(64.9)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_point_value_rejected(self, bad: float) -> None:
        pts = [
            {"water_level": 1.0, "discharge": 59.6},
            {"water_level": 1.1, "discharge": bad},
        ]
        with pytest.raises(RatingConversionError, match="non-finite"):
            RatingConverter.from_curve(_curve(pts))

    def test_missing_key_gives_domain_error(self) -> None:
        pts = [{"water_level": 1.0}]  # no discharge
        with pytest.raises(RatingConversionError, match="missing"):
            RatingConverter.from_curve(_curve(pts))

    def test_non_numeric_value_gives_domain_error(self) -> None:
        pts = [{"water_level": 1.0, "discharge": "lots"}]
        with pytest.raises(RatingConversionError, match="non-numeric"):
            RatingConverter.from_curve(_curve(pts))

    def test_bool_value_rejected_as_non_numeric(self) -> None:
        pts = [{"water_level": 1.0, "discharge": True}]
        with pytest.raises(RatingConversionError, match="non-numeric"):
            RatingConverter.from_curve(_curve(pts))

    def test_non_finite_level_rejected(self) -> None:
        conv = RatingConverter.from_curve(_curve(_RT))
        with pytest.raises(RatingConversionError, match="finite"):
            conv.convert(math.nan)


class TestInverse:
    @pytest.mark.parametrize("method", list(InterpolationMethod))
    def test_round_trip_and_literal_midpoint(self, method: InterpolationMethod) -> None:
        converter = RatingConverter.from_curve(
            _curve(
                [
                    {"water_level": 1.0, "discharge": 10.0},
                    {"water_level": 3.0, "discharge": 90.0},
                ],
                method,
            )
        )
        discharge = 50.0 if method is InterpolationMethod.LINEAR else 30.0
        assert converter.inverse(discharge).water_level == pytest.approx(2.0)
        for level in (1.0, 1.4, 2.8, 3.0):
            result = converter.inverse(converter.convert(level).discharge)
            assert result.status is InverseRatingStatus.IN_RANGE
            assert result.water_level == pytest.approx(level)

    @pytest.mark.parametrize(
        "flows,ambiguous", [([1, 1, 3, 4], 1), ([1, 2, 2, 4], 2), ([1, 2, 4, 4], 4)]
    )
    def test_plateau_only_refuses_its_exact_discharge(
        self, flows: list[int], ambiguous: int
    ) -> None:
        converter = RatingConverter.from_curve(
            _curve(
                [
                    {"water_level": float(i), "discharge": float(q)}
                    for i, q in enumerate(flows)
                ]
            )
        )
        assert converter.inverse(ambiguous).status is InverseRatingStatus.AMBIGUOUS
        assert converter.inverse(ambiguous).water_level is None
        nearby = 1.5 if ambiguous in (1, 2) else 3.5
        assert converter.inverse(nearby).status is InverseRatingStatus.IN_RANGE

    @pytest.mark.parametrize("method", list(InterpolationMethod))
    @pytest.mark.parametrize(
        "value,status",
        [
            (-1, InverseRatingStatus.BELOW),
            (0, InverseRatingStatus.BELOW),
            (59, InverseRatingStatus.BELOW),
            (82, InverseRatingStatus.ABOVE),
        ],
    )
    def test_no_clamp_or_extrapolation(
        self, method: InterpolationMethod, value: float, status: InverseRatingStatus
    ) -> None:
        result = RatingConverter.from_curve(_curve(_RT, method)).inverse(value)
        assert result.status is status
        assert result.water_level is None

    @pytest.mark.parametrize("value", [math.inf, math.nan, -math.inf])
    def test_nonfinite_discharge_refused(self, value: float) -> None:
        with pytest.raises(RatingConversionError, match="finite"):
            RatingConverter.from_curve(_curve(_RT)).inverse(value)
