from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.services.rating_conversion import (
    RatingConversionError,
    RatingConverter,
    RatingRange,
    convert_provisional_level,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.ids import RatingCurveId, StationId
from sapphire_flow.types.rating_curve import RatingCurve


def curve(points: list[tuple[float, float]]) -> RatingCurve:
    return RatingCurve(
        id=RatingCurveId(uuid4()),
        station_id=StationId(uuid4()),
        version=1,
        valid_from=ensure_utc(datetime(2000, 1, 1, tzinfo=UTC)),
        valid_to=ensure_utc(datetime(2001, 1, 1, tzinfo=UTC)),
        points=[{"water_level": h, "discharge": q} for h, q in points],
        interpolation=InterpolationMethod.LINEAR,
        uploaded_by=None,
        created_at=ensure_utc(datetime(2000, 1, 1, tzinfo=UTC)),
    )


class TestConvertProvisionalLevel:
    @pytest.mark.parametrize(
        "level,expected,flag",
        [
            (0.5, 0.0, RatingRange.BELOW),
            (1.0, 1.0, RatingRange.IN_RANGE),
            (1.5, 2.0, RatingRange.IN_RANGE),
            (2.0, 3.0, RatingRange.IN_RANGE),
            (3.0, 5.0, RatingRange.ABOVE),
        ],
    )
    def test_linear_endpoints(
        self, level: float, expected: float, flag: RatingRange
    ) -> None:
        result = convert_provisional_level(level, curve([(1.0, 1.0), (2.0, 3.0)]))
        assert result.discharge == expected
        assert result.range_flag is flag

    def test_plateau(self) -> None:
        assert (
            convert_provisional_level(0.0, curve([(1.0, 0.0), (2.0, 0.0)])).discharge
            == 0.0
        )

    def test_strict_converter_still_clamps(self) -> None:
        assert (
            RatingConverter.from_curve(curve([(1.0, 1.0), (2.0, 3.0)]))
            .convert(3.0)
            .discharge
            == 3.0
        )

    @pytest.mark.parametrize(
        "points",
        [
            [(1.0, 1.0)],
            [(1.0, 1.0), (1.0, 2.0)],
            [(1.0, -1.0), (2.0, 2.0)],
            [(1.0, 3.0), (2.0, 2.0)],
            [(1.0, 1.0), (2.0, float("inf"))],
        ],
    )
    def test_rejects_bad_table(self, points: list[tuple[float, float]]) -> None:
        with pytest.raises(RatingConversionError, match="rating|discharge|stage"):
            convert_provisional_level(1.0, curve(points))

    @pytest.mark.parametrize("level", [0.0, float("inf"), 1e308])
    def test_rejects_invalid_result(self, level: float) -> None:
        with pytest.raises(RatingConversionError, match="finite|negative"):
            convert_provisional_level(level, curve([(1.0, 1.0), (2.0, 3.0)]))

    def test_rejects_log_table(self) -> None:
        with pytest.raises(RatingConversionError, match="LINEAR"):
            convert_provisional_level(
                1.0,
                replace(
                    curve([(1.0, 1.0), (2.0, 3.0)]),
                    interpolation=InterpolationMethod.LOG_LINEAR,
                ),
            )
