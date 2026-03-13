from __future__ import annotations

from datetime import timedelta

import pytest

from sapphire_flow.types.domain import (
    DangerLevelDefinition,
    GeoCoord,
    QcFlag,
    SeasonDefinition,
    aggregate_qc_status,
)
from sapphire_flow.types.enums import QcStatus, ThresholdDirection


class TestGeoCoord:
    def test_valid(self) -> None:
        g = GeoCoord(lon=8.5, lat=47.4)
        assert g.lon == 8.5
        assert g.lat == 47.4
        assert g.altitude_masl is None

    def test_with_altitude(self) -> None:
        g = GeoCoord(lon=8.5, lat=47.4, altitude_masl=500.0)
        assert g.altitude_masl == 500.0

    def test_lon_out_of_range_high(self) -> None:
        with pytest.raises(ValueError, match="longitude"):
            GeoCoord(lon=181.0, lat=0.0)

    def test_lon_out_of_range_low(self) -> None:
        with pytest.raises(ValueError, match="longitude"):
            GeoCoord(lon=-181.0, lat=0.0)

    def test_lat_out_of_range_high(self) -> None:
        with pytest.raises(ValueError, match="latitude"):
            GeoCoord(lon=0.0, lat=91.0)

    def test_lat_out_of_range_low(self) -> None:
        with pytest.raises(ValueError, match="latitude"):
            GeoCoord(lon=0.0, lat=-91.0)

    def test_boundary_values(self) -> None:
        g = GeoCoord(lon=180.0, lat=-90.0)
        assert g.lon == 180.0
        assert g.lat == -90.0


class TestDangerLevelDefinition:
    def test_valid(self) -> None:
        d = DangerLevelDefinition(
            name="Moderate",
            display_order=2,
            trigger_probability=0.5,
            resolve_probability=0.3,
            min_trigger_duration=timedelta(hours=1),
            min_resolve_duration=timedelta(hours=2),
        )
        assert d.name == "Moderate"
        assert d.direction == ThresholdDirection.ABOVE

    def test_trigger_probability_zero(self) -> None:
        with pytest.raises(ValueError, match="trigger_probability"):
            DangerLevelDefinition(
                name="X",
                display_order=1,
                trigger_probability=0.0,
                resolve_probability=0.0,
                min_trigger_duration=timedelta(0),
                min_resolve_duration=timedelta(0),
            )

    def test_trigger_probability_above_one(self) -> None:
        with pytest.raises(ValueError, match="trigger_probability"):
            DangerLevelDefinition(
                name="X",
                display_order=1,
                trigger_probability=1.1,
                resolve_probability=0.5,
                min_trigger_duration=timedelta(0),
                min_resolve_duration=timedelta(0),
            )

    def test_resolve_ge_trigger(self) -> None:
        with pytest.raises(ValueError, match="resolve_probability"):
            DangerLevelDefinition(
                name="X",
                display_order=1,
                trigger_probability=0.5,
                resolve_probability=0.5,
                min_trigger_duration=timedelta(0),
                min_resolve_duration=timedelta(0),
            )

    def test_resolve_equal_zero(self) -> None:
        with pytest.raises(ValueError, match="resolve_probability"):
            DangerLevelDefinition(
                name="X",
                display_order=1,
                trigger_probability=0.5,
                resolve_probability=0.0,
                min_trigger_duration=timedelta(0),
                min_resolve_duration=timedelta(0),
            )

    def test_negative_trigger_duration(self) -> None:
        with pytest.raises(ValueError, match="min_trigger_duration"):
            DangerLevelDefinition(
                name="X",
                display_order=1,
                trigger_probability=0.5,
                resolve_probability=0.3,
                min_trigger_duration=timedelta(hours=-1),
                min_resolve_duration=timedelta(0),
            )

    def test_negative_resolve_duration(self) -> None:
        with pytest.raises(ValueError, match="min_resolve_duration"):
            DangerLevelDefinition(
                name="X",
                display_order=1,
                trigger_probability=0.5,
                resolve_probability=0.3,
                min_trigger_duration=timedelta(0),
                min_resolve_duration=timedelta(hours=-1),
            )

    def test_below_direction(self) -> None:
        d = DangerLevelDefinition(
            name="Low",
            display_order=1,
            trigger_probability=0.5,
            resolve_probability=0.3,
            min_trigger_duration=timedelta(0),
            min_resolve_duration=timedelta(0),
            direction=ThresholdDirection.BELOW,
        )
        assert d.direction == ThresholdDirection.BELOW


class TestQcFlag:
    def test_valid_passed(self) -> None:
        f = QcFlag(
            rule_id="range_check", rule_version="1.0.0", status=QcStatus.QC_PASSED
        )
        assert f.status == QcStatus.QC_PASSED
        assert f.detail is None

    def test_valid_with_detail(self) -> None:
        f = QcFlag(
            rule_id="range_check",
            rule_version="1.0.0",
            status=QcStatus.QC_FAILED,
            detail="out of range",
        )
        assert f.detail == "out of range"

    def test_raw_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAW"):
            QcFlag(rule_id="range_check", rule_version="1.0.0", status=QcStatus.RAW)

    def test_suspect(self) -> None:
        f = QcFlag(
            rule_id="rate_of_change", rule_version="2.0.0", status=QcStatus.QC_SUSPECT
        )
        assert f.status == QcStatus.QC_SUSPECT


class TestAggregateQcStatus:
    def test_empty_flags(self) -> None:
        assert aggregate_qc_status([]) == QcStatus.QC_PASSED

    def test_all_passed(self) -> None:
        flags = [
            QcFlag("a", "1.0", QcStatus.QC_PASSED),
            QcFlag("b", "1.0", QcStatus.QC_PASSED),
        ]
        assert aggregate_qc_status(flags) == QcStatus.QC_PASSED

    def test_suspect_wins_over_passed(self) -> None:
        flags = [
            QcFlag("a", "1.0", QcStatus.QC_PASSED),
            QcFlag("b", "1.0", QcStatus.QC_SUSPECT),
        ]
        assert aggregate_qc_status(flags) == QcStatus.QC_SUSPECT

    def test_failed_wins_over_suspect(self) -> None:
        flags = [
            QcFlag("a", "1.0", QcStatus.QC_SUSPECT),
            QcFlag("b", "1.0", QcStatus.QC_FAILED),
        ]
        assert aggregate_qc_status(flags) == QcStatus.QC_FAILED


class TestSeasonDefinition:
    def test_valid(self) -> None:
        s = SeasonDefinition(name="winter", months=frozenset({11, 12, 1, 2, 3}))
        assert s.name == "winter"
        assert len(s.months) == 5

    def test_empty_months(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            SeasonDefinition(name="x", months=frozenset())

    def test_invalid_month(self) -> None:
        with pytest.raises(ValueError, match="months must be in"):
            SeasonDefinition(name="x", months=frozenset({0, 1, 2}))

    def test_month_13(self) -> None:
        with pytest.raises(ValueError, match="months must be in"):
            SeasonDefinition(name="x", months=frozenset({13}))
