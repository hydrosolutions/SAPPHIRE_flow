from datetime import UTC, datetime

import pytest

from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import InputQualityFlag
from sapphire_flow.types.enums import (
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
)


class TestDailyInputProvenance:
    def test_unknowns_are_not_assumed_healthy(self) -> None:
        record = DailyInputProvenance()
        assert record.input_quality is None
        assert record.nwp_age_hours is None
        assert record.observation_staleness_hours is None

    @pytest.mark.parametrize("age", [-1.0, float("nan"), float("inf")])
    def test_invalid_age_is_rejected(self, age: float) -> None:
        with pytest.raises(ValueError, match="age"):
            DailyInputProvenance(
                nwp_source=NwpCycleSource.PRIMARY,
                nwp_reference_time=ensure_utc(datetime(2026, 10, 26, tzinfo=UTC)),
                nwp_age_hours=age,
            )

    def test_runoff_only_has_no_weather_reference(self) -> None:
        with pytest.raises(ValueError, match="weather reference"):
            DailyInputProvenance(
                nwp_source=NwpCycleSource.RUNOFF_ONLY,
                nwp_reference_time=ensure_utc(datetime(2026, 10, 26, tzinfo=UTC)),
            )

    def test_age_without_a_reference_is_not_invented(self) -> None:
        with pytest.raises(ValueError, match="reference"):
            DailyInputProvenance(nwp_age_hours=0.0)

    def test_large_diagnostic_details_are_not_saved_as_summary(self) -> None:
        flag = InputQualityFlag(
            category=InputQualityCategory.NWP,
            level=InputQualityLevel.DEGRADED,
            detail="x" * 1025,
        )
        with pytest.raises(ValueError, match="bounded summary"):
            DailyInputProvenance(
                input_quality=InputQualityLevel.DEGRADED, input_quality_flags=(flag,)
            )
