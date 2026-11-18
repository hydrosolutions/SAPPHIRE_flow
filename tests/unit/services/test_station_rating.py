from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.services.rating_conversion import RatingConverter
from sapphire_flow.services.station_observation_load import (
    DatumRequiredError,
    ObservationValueKind,
)
from sapphire_flow.services.station_rating import (
    RatingCsvError,
    derive_discharge,
    parse_rating_csv,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    InterpolationMethod,
    ObservationSource,
    QcStatus,
    WaterLevelGaugeType,
)
from sapphire_flow.types.ids import ObservationId, RatingCurveId
from sapphire_flow.types.observation import Observation
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
STAFF = make_station_config(
    water_level_datum_masl=1480.0,
    water_level_unit="m",
    water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
)
BRIDGE = make_station_config(
    water_level_datum_masl=1480.0,
    water_level_unit="m",
    water_level_gauge_type=WaterLevelGaugeType.BRIDGE_SENSOR,
    water_level_sensor_masl=1490.0,
)
TABLE = "h,q\n1.0,10\n2.0,40\n3.0,90\n"


def _parse(
    text: str = TABLE,
    station: object = STAFF,
    kind: ObservationValueKind = ObservationValueKind.GAUGE_READING,
    interpolation: InterpolationMethod = InterpolationMethod.LINEAR,
):  # noqa: ANN202
    return parse_rating_csv(
        text,
        level_column="h",
        discharge_column="q",
        level_kind=kind,
        station=station,  # pyright: ignore[reportArgumentType]
        curve_id=RatingCurveId(uuid4()),
        version=1,
        valid_from=NOW,
        interpolation=interpolation,
        label="SYNTHETIC",
        created_at=NOW,
    )


class TestParseRatingCsv:
    def test_staff_gauge_stage_is_datum_plus_reading(self) -> None:
        curve = _parse()
        assert [p["water_level"] for p in curve.points] == [1481.0, 1482.0, 1483.0]
        assert [p["discharge"] for p in curve.points] == [10, 40, 90]
        assert curve.rating_type_label == "SYNTHETIC"

    def test_bridge_sensor_stage_is_sensor_minus_reading_and_sorted(self) -> None:
        # a deeper reading (distance down to the water) means a lower level
        curve = _parse("h,q\n1.0,90\n2.0,40\n3.0,10\n", station=BRIDGE)
        assert [p["water_level"] for p in curve.points] == [1487.0, 1488.0, 1489.0]
        assert [p["discharge"] for p in curve.points] == [10, 40, 90]

    def test_level_masl_is_stored_as_is(self) -> None:
        curve = _parse("h,q\n1481,10\n1482,40\n", kind=ObservationValueKind.LEVEL_MASL)
        assert [p["water_level"] for p in curve.points] == [1481.0, 1482.0]

    def test_gauge_reading_without_datum_is_refused(self) -> None:
        with pytest.raises(DatumRequiredError, match="datum"):
            _parse(station=make_station_config())

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("", "empty"),
            ("a,b\n1,2\n", "not in the file"),
            ("h,q\n1,2\n", "at least 2"),
            ("h,q\n1,2\nx,3\n", "not a number"),
            ("h,q\n1,2\nnan,3\n", "not finite"),
            ("h,q\n1,2\n2,inf\n", "not finite"),
            ("h,q\n1,2\n1,3\n", "non-increasing"),
            ("h,q\n1,20\n2,10\n", "decreases"),
        ],
    )
    def test_invalid_tables_are_rejected(self, text: str, match: str) -> None:
        with pytest.raises(RatingCsvError, match=match):
            _parse(text, kind=ObservationValueKind.LEVEL_MASL)

    def test_bridge_error_quotes_the_csv_line_and_explains_direction(self) -> None:
        with pytest.raises(RatingCsvError, match="line 3: reading 2 .*larger reading"):
            _parse(TABLE, station=BRIDGE)

    def test_zero_discharge_rejected_for_log_linear_only(self) -> None:
        text = "h,q\n1481,0\n1482,5\n"
        kind = ObservationValueKind.LEVEL_MASL
        assert _parse(text, kind=kind).points
        with pytest.raises(RatingCsvError, match="> 0"):
            _parse(text, kind=kind, interpolation=InterpolationMethod.LOG_LINEAR)


def _obs(station_id: object, hour: int, value: float | None) -> Observation:
    return Observation(
        id=ObservationId(uuid4()),
        station_id=station_id,  # pyright: ignore[reportArgumentType]
        timestamp=ensure_utc(datetime(2026, 11, 1, hour, tzinfo=UTC)),
        parameter="water_level",
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.RAW,
        qc_flags=[],
        qc_rule_version=None,
        created_at=NOW,
    )


class TestDeriveDischarge:
    def test_in_range_values_match_the_converter_and_carry_the_curve(self) -> None:
        curve = _parse()
        converter = RatingConverter.from_curve(curve)
        levels = [_obs(STAFF.id, 1, 1481.5), _obs(STAFF.id, 2, 1482.5)]
        derived = derive_discharge(levels, curve)
        assert [r.value for r in derived.rows] == [
            converter.convert(1481.5).discharge,
            converter.convert(1482.5).discharge,
        ]
        assert {r.source for r in derived.rows} == {
            ObservationSource.RATING_CURVE_DERIVED
        }
        assert {r.rating_curve_id for r in derived.rows} == {curve.id}
        assert {r.parameter for r in derived.rows} == {"discharge"}

    def test_out_of_range_levels_are_skipped_and_counted_not_clamped(self) -> None:
        curve = _parse()
        levels = [
            _obs(STAFF.id, 1, 1480.5),
            _obs(STAFF.id, 2, 1482.0),
            _obs(STAFF.id, 3, 1490.0),
            _obs(STAFF.id, 4, 1491.0),
        ]
        derived = derive_discharge(levels, curve)
        assert [r.value for r in derived.rows] == [40.0]
        assert (derived.skipped_below, derived.skipped_above) == (1, 2)
