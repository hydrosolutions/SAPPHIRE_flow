from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sapphire_flow.store.hindcast_period_codec import (
    parse_source_periods,
    source_periods_content,
)
from sapphire_flow.types.enums import AggregationMethod
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.hindcast_inputs import HistoricalSourcePeriod
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid


def _declaration() -> HistoricalSourcePeriod:
    return HistoricalSourcePeriod(
        station_id=StationId(uuid4()),
        source="manual",
        parameter="discharge",
        evidence="Provider's stated daily period",
        period=ForecastPeriodDefinition(
            grid=TimeGrid(
                step=timedelta(days=1),
                phase=timedelta(hours=18, minutes=15),
            ),
            label=PeriodLabel.END,
            aggregation=AggregationMethod.MEAN,
        ),
        stored_label_shift=timedelta(hours=-24),
        input_support_displacement=timedelta(minutes=15),
    )


class TestHistoricalSourceCodec:
    def test_round_trip_preserves_physical_and_stored_label_semantics(self) -> None:
        declaration = _declaration()
        assert parse_source_periods(source_periods_content((declaration,))) == (
            declaration,
        )

    @pytest.mark.parametrize("offset", [True, 900.5, "900"])
    def test_displacement_is_not_silently_coerced(self, offset: object) -> None:
        payload = source_periods_content((_declaration(),))
        payload[0]["input_support_displacement_seconds"] = offset
        with pytest.raises(ValidationError, match="valid integer"):
            parse_source_periods(payload)

    def test_conflicting_declarations_for_one_source_are_rejected(self) -> None:
        payload = source_periods_content((_declaration(),))
        payload.append({**payload[0], "stored_label_shift_seconds": 0})
        with pytest.raises(ValueError, match="duplicate historical source"):
            parse_source_periods(payload)

    def test_subsecond_offset_cannot_be_lost_in_serialization(self) -> None:
        from dataclasses import replace

        with pytest.raises(ValueError, match="whole seconds"):
            replace(_declaration(), stored_label_shift=timedelta(microseconds=1))
