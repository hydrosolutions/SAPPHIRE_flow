from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.weather import WeatherForecastRecord


def _base_kwargs() -> dict:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": uuid4(),
        "station_id": uuid4(),
        "nwp_source": "ICON-CH2-EPS",
        "cycle_time": now,
        "valid_time": now,
        "parameter": "precipitation",
        "spatial_type": SpatialRepresentation.POINT,
        "band_id": None,
        "member_id": 1,
        "value": 3.5,
        "created_at": now,
    }


class TestWeatherForecastRecord:
    def test_valid_non_gap(self) -> None:
        record = WeatherForecastRecord(**_base_kwargs())
        assert record.is_gap is False
        assert record.gap_status is None

    def test_valid_gap_with_status(self) -> None:
        record = WeatherForecastRecord(
            **_base_kwargs(), is_gap=True, gap_status="recovered"
        )
        assert record.is_gap is True
        assert record.gap_status == "recovered"

    def test_gap_without_status_raises(self) -> None:
        with pytest.raises(ValueError, match="gap_status must be set"):
            WeatherForecastRecord(**_base_kwargs(), is_gap=True)

    def test_non_gap_with_status_allowed(self) -> None:
        record = WeatherForecastRecord(
            **_base_kwargs(), is_gap=False, gap_status="unrecoverable"
        )
        assert record.gap_status == "unrecoverable"
