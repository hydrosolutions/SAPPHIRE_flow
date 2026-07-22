from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from sapphire_flow.types.bafu_observation import BafuObservationRow
from sapphire_flow.types.datetime import ensure_utc

_MEASUREMENT_TIME = ensure_utc(datetime(2026, 7, 21, 15, 0, tzinfo=UTC))


class TestBafuObservationRow:
    def test_constructs_river_discharge_row(self) -> None:
        row = BafuObservationRow(
            gauge_code="2135",
            lindas_kind="river",
            parameter="discharge",
            value=12.3,
            measurement_time=_MEASUREMENT_TIME,
        )
        assert row.gauge_code == "2135"
        assert row.lindas_kind == "river"
        assert row.parameter == "discharge"
        assert row.value == 12.3
        assert row.measurement_time == _MEASUREMENT_TIME

    def test_constructs_lake_water_level_row(self) -> None:
        row = BafuObservationRow(
            gauge_code="2004",
            lindas_kind="lake",
            parameter="water_level",
            value=372.1,
            measurement_time=_MEASUREMENT_TIME,
        )
        assert row.lindas_kind == "lake"
        assert row.parameter == "water_level"

    def test_is_kw_only(self) -> None:
        with pytest.raises(TypeError):
            BafuObservationRow(  # type: ignore[misc, call-arg]
                "2135", "river", "discharge", 12.3, _MEASUREMENT_TIME
            )

    def test_is_frozen(self) -> None:
        row = BafuObservationRow(
            gauge_code="2135",
            lindas_kind="river",
            parameter="discharge",
            value=12.3,
            measurement_time=_MEASUREMENT_TIME,
        )
        with pytest.raises(FrozenInstanceError):
            row.value = 99.0  # type: ignore[misc]

    def test_no_station_id_field(self) -> None:
        # DC-2: RawObservation/StationId are never constructed on this path —
        # the row type has no station_id field at all.
        row = BafuObservationRow(
            gauge_code="2135",
            lindas_kind="river",
            parameter="discharge",
            value=12.3,
            measurement_time=_MEASUREMENT_TIME,
        )
        assert not hasattr(row, "station_id")
