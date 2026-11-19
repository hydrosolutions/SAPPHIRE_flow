from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.station_observation_load import (
    DatumRequiredError,
    ObservationCsvError,
    ObservationValueKind,
    days_with_value,
    parse_observation_csv,
    to_raw_observations,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, WaterLevelGaugeType
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
CSV = "t,v\n2026-11-01 06:00,2.0\n2026-11-02 06:00,2.5\n"


def _parse(text: str = CSV, **kw: object) -> list[tuple[object, float]]:
    args: dict[str, object] = {
        "time_column": "t",
        "value_column": "v",
        "utc_offset": timedelta(minutes=345),
        "timezone": None,
        "now": NOW,
    }
    args.update(kw)
    return parse_observation_csv(text, **args)  # pyright: ignore[reportArgumentType]


class TestParse:
    def test_offset_converts_to_utc(self) -> None:
        rows = _parse()
        assert rows[0] == (ensure_utc(datetime(2026, 11, 1, 0, 15, tzinfo=UTC)), 2.0)

    def test_zone_converts_to_utc(self) -> None:
        rows = _parse(utc_offset=None, timezone=ZoneInfo("Asia/Kathmandu"))
        assert rows[1][0] == ensure_utc(datetime(2026, 11, 2, 0, 15, tzinfo=UTC))

    def test_explicit_offset_in_text_needs_no_zone(self) -> None:
        rows = _parse("t,v\n2026-11-01T06:00:00+00:00,1\n", utc_offset=None)
        assert rows[0][0] == ensure_utc(datetime(2026, 11, 1, 6, tzinfo=UTC))

    @pytest.mark.parametrize(
        ("text", "kw", "fragment"),
        [
            ("", {}, "empty"),
            ("t,v\n", {}, "no data rows"),
            ("t,x\n2026-11-01 06:00,1\n", {}, "not in the file"),
            ("t,v\n2026-11-01 06:00,abc\n", {}, "not a number"),
            ("t,v\n2026-11-01 06:00,nan\n", {}, "not finite"),
            ("t,v\n2026-11-01 06:00,inf\n", {}, "not finite"),
            ("t,v\n2026-11-01 06:00,\n", {}, "not a number"),
            ("t,v\nyesterday,1\n", {}, "ISO timestamp"),
            ("t,v\n2026-11-01 06:00,1\n2026-11-01 06:00,2\n", {}, "duplicate"),
            ("t,v\n2026-11-01 06:00,1\n", {"utc_offset": None}, "no time zone"),
            ("t,v\n2027-01-01 06:00,1\n", {}, "future"),
            (CSV, {"timezone": ZoneInfo("UTC")}, "not both"),
        ],
    )
    def test_rejects(self, text: str, kw: dict[str, object], fragment: str) -> None:
        with pytest.raises(ObservationCsvError, match=fragment):
            _parse(text, **kw)

    def test_duplicates_detected_after_zone_conversion(self) -> None:
        text = "t,v\n2026-11-01 06:00,1\n2026-11-01T00:15:00+00:00,2\n"
        with pytest.raises(ObservationCsvError, match="duplicate"):
            _parse(text)


ROWS = [(ensure_utc(datetime(2026, 11, 1, tzinfo=UTC)), 2.123456789)]


class TestToRaw:
    def test_staff_gauge_adds_datum(self) -> None:
        station = make_station_config(
            water_level_datum_masl=1480.0,
            water_level_unit="m",
            water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
        )
        (raw,) = to_raw_observations(
            ROWS, station, "water_level", ObservationValueKind.GAUGE_READING
        )
        assert raw.value == 1480.0 + 2.123456789
        assert (raw.parameter, raw.source) == (
            "water_level",
            ObservationSource.MEASURED,
        )
        assert raw.station_id == station.id

    def test_bridge_sensor_subtracts_distance(self) -> None:
        station = make_station_config(
            water_level_datum_masl=1480.0,
            water_level_unit="m",
            water_level_gauge_type=WaterLevelGaugeType.BRIDGE_SENSOR,
            water_level_sensor_masl=1490.0,
        )
        (raw,) = to_raw_observations(
            ROWS, station, "water_level", ObservationValueKind.GAUGE_READING
        )
        assert raw.value == 1490.0 - 2.123456789

    def test_gauge_reading_refused_without_datum_or_type(self) -> None:
        station = make_station_config()
        with pytest.raises(DatumRequiredError, match="gauge type or datum"):
            to_raw_observations(
                ROWS, station, "water_level", ObservationValueKind.GAUGE_READING
            )

    def test_datum_error_is_a_configuration_error(self) -> None:
        assert issubclass(DatumRequiredError, ConfigurationError)

    def test_level_masl_and_discharge_pass_through_without_datum(self) -> None:
        station = make_station_config()
        for kind, parameter in (
            (ObservationValueKind.LEVEL_MASL, "water_level"),
            (ObservationValueKind.DISCHARGE_M3S, "discharge"),
        ):
            (raw,) = to_raw_observations(ROWS, station, parameter, kind)
            assert raw.value == 2.123456789


class TestDaysWithValue:
    def test_counts_distinct_local_days_in_window(self) -> None:
        stamps = [
            ensure_utc(datetime(2026, 11, d, h, tzinfo=UTC))
            for d in (1, 2, 2, 10)
            for h in (1,)
        ]
        day = datetime(2026, 11, 10, tzinfo=UTC).date()
        assert days_with_value(stamps, ZoneInfo("UTC"), end=day, lookback_days=30) == 3
        assert days_with_value(stamps, ZoneInfo("UTC"), end=day, lookback_days=5) == 1
