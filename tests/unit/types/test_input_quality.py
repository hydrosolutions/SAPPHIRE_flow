import pytest

from sapphire_flow.types.domain import InputQualityFlag, aggregate_input_quality
from sapphire_flow.types.enums import InputQualityCategory, InputQualityLevel


class TestInputQualityFlag:
    def test_construction_with_partial_level(self) -> None:
        flag = InputQualityFlag(
            category=InputQualityCategory.OBSERVATION,
            level=InputQualityLevel.PARTIAL,
            detail="some observations missing",
        )
        assert flag.level == InputQualityLevel.PARTIAL

    def test_construction_with_degraded_level(self) -> None:
        flag = InputQualityFlag(
            category=InputQualityCategory.NWP,
            level=InputQualityLevel.DEGRADED,
            detail="NWP fallback used",
        )
        assert flag.level == InputQualityLevel.DEGRADED

    def test_full_level_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be FULL"):
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.FULL,
                detail="this should fail",
            )

    def test_detail_field_preserved(self) -> None:
        detail = "warm-up state is 48 hours old"
        flag = InputQualityFlag(
            category=InputQualityCategory.WARM_UP,
            level=InputQualityLevel.PARTIAL,
            detail=detail,
        )
        assert flag.detail == detail

    def test_category_field_preserved(self) -> None:
        flag = InputQualityFlag(
            category=InputQualityCategory.NWP,
            level=InputQualityLevel.DEGRADED,
            detail="fallback NWP cycle used",
        )
        assert flag.category == InputQualityCategory.NWP

    def test_frozen_cannot_assign(self) -> None:
        flag = InputQualityFlag(
            category=InputQualityCategory.OBSERVATION,
            level=InputQualityLevel.PARTIAL,
            detail="some detail",
        )
        with pytest.raises(AttributeError):
            flag.level = InputQualityLevel.DEGRADED  # type: ignore[misc]


class TestAggregateInputQuality:
    def test_empty_list_returns_full(self) -> None:
        assert aggregate_input_quality([]) == InputQualityLevel.FULL

    def test_single_partial_returns_partial(self) -> None:
        flags = [
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.PARTIAL,
                detail="some missing",
            )
        ]
        assert aggregate_input_quality(flags) == InputQualityLevel.PARTIAL

    def test_single_degraded_returns_degraded(self) -> None:
        flags = [
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.DEGRADED,
                detail="bad NWP",
            )
        ]
        assert aggregate_input_quality(flags) == InputQualityLevel.DEGRADED

    def test_mixed_partial_and_degraded_returns_degraded(self) -> None:
        flags = [
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.PARTIAL,
                detail="partial obs",
            ),
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.DEGRADED,
                detail="degraded NWP",
            ),
        ]
        assert aggregate_input_quality(flags) == InputQualityLevel.DEGRADED

    def test_multiple_partial_flags_returns_partial(self) -> None:
        flags = [
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.PARTIAL,
                detail="partial obs",
            ),
            InputQualityFlag(
                category=InputQualityCategory.WARM_UP,
                level=InputQualityLevel.PARTIAL,
                detail="partial warm-up",
            ),
        ]
        assert aggregate_input_quality(flags) == InputQualityLevel.PARTIAL

    def test_multiple_degraded_flags_returns_degraded(self) -> None:
        flags = [
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.DEGRADED,
                detail="degraded NWP",
            ),
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.DEGRADED,
                detail="degraded obs",
            ),
        ]
        assert aggregate_input_quality(flags) == InputQualityLevel.DEGRADED
