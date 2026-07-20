"""LOCKED failing-first tests for the Plan 129 S1 seam-continuity gate.

A coarse units/scale sanity check over RAW provenance-bearing rows at the two
precipitation seams (RhiresD -> RprelimD, RprelimD -> NWP). T1-only
diagnostic (owner decision 2) — never wired into operational assembly.

Scope (owner decision 2, "coarse, high-tolerance"):
* a mm-vs-m (1000x) unit error at a seam is FLAGGED
* a per-hour-vs-per-day (24x) unit error at a seam is FLAGGED
* a legitimate forecast rain event RprelimD lacks is NOT flagged (never a
  per-day value-agreement / statistical test)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sapphire_flow.diagnostics.seam_gate import (
    SeamGateVerdict,
    SeamWindow,
    check_seam_continuity,
    seam_window_from_forcing_rows,
    seam_window_from_nwp_rows,
)
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import make_raw_historical_forcing

_SID = StationId(uuid4())


class TestUnitScaleErrorsAreFlagged:
    def test_mm_vs_m_1000x_scale_error_is_flagged(self) -> None:
        # RhiresD side in correct mm/day; RprelimD side accidentally stored
        # in METERS (a real day's rainfall divided by 1000).
        rhiresd = SeamWindow(label="rhiresd", values=(4.0, 6.0, 5.0, 3.0, 7.0))
        rprelimd_meters_bug = SeamWindow(
            label="rprelimd", values=(0.004, 0.006, 0.005, 0.003, 0.007)
        )

        result = check_seam_continuity(rhiresd, rprelimd_meters_bug)

        assert result.verdict is SeamGateVerdict.UNIT_ERROR_SUSPECTED

    def test_per_hour_vs_per_day_24x_scale_error_is_flagged(self) -> None:
        # RprelimD side correctly daily; NWP side accidentally left as a
        # single hourly increment instead of the daily-accumulated total.
        rprelimd = SeamWindow(label="rprelimd", values=(2.0, 3.0, 1.0, 4.0))
        nwp_hourly_not_daily_bug = SeamWindow(
            label="nwp", values=(2.0 * 24, 3.0 * 24, 1.0 * 24, 4.0 * 24)
        )

        result = check_seam_continuity(rprelimd, nwp_hourly_not_daily_bug)

        assert result.verdict is SeamGateVerdict.UNIT_ERROR_SUSPECTED

    def test_implausible_absolute_value_is_flagged_directly(self) -> None:
        # A single implausible mm/day value (e.g. a units bug producing a
        # ~5000 mm/day reading) is flagged even without needing a ratio.
        before = SeamWindow(label="rhiresd", values=(4.0, 6.0, 5.0))
        after = SeamWindow(label="rprelimd", values=(5000.0, 6.0, 5.0))

        result = check_seam_continuity(before, after)

        assert result.verdict is SeamGateVerdict.UNIT_ERROR_SUSPECTED


class TestLegitimateMetDifferenceIsNotFlagged:
    def test_forecast_rain_event_absent_from_past_window_is_not_flagged(self) -> None:
        # RprelimD's recent past was mostly dry; the NWP forecast correctly
        # shows an upcoming rain event. A real meteorological difference,
        # not a units/scale bug — must NOT be flagged (owner decision 2).
        rprelimd_dry_tail = SeamWindow(label="rprelimd", values=(0.0, 1.0, 0.5, 1.0))
        nwp_forecast_rain_event = SeamWindow(label="nwp", values=(0.0, 2.0, 5.0, 12.0))

        result = check_seam_continuity(rprelimd_dry_tail, nwp_forecast_rain_event)

        assert result.verdict is SeamGateVerdict.PASS

    def test_all_zero_window_passes_without_a_ratio(self) -> None:
        dry_before = SeamWindow(label="rhiresd", values=(0.0, 0.0, 0.0))
        dry_after = SeamWindow(label="rprelimd", values=(0.0, 0.0, 0.0))

        result = check_seam_continuity(dry_before, dry_after)

        assert result.verdict is SeamGateVerdict.PASS


class TestSeamWindowBuildersReadRawProvenance:
    def test_seam_window_from_forcing_rows_filters_by_source_and_parameter(
        self,
    ) -> None:
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        rows = [
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=ts,
                value=4.0,
            ),
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                parameter="precipitation",
                valid_time=ts,
                value=6.0,
            ),
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="temperature",
                valid_time=ts,
                value=99.0,  # different parameter — must be excluded
            ),
        ]

        window = seam_window_from_forcing_rows(
            rows,
            source=ForcingSource.METEOSWISS_RHIRESD,
            parameter="precipitation",
            label="rhiresd",
        )

        assert window.values == (4.0,)

    def test_seam_window_from_nwp_rows_filters_by_parameter(self) -> None:
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        rows = [
            WeatherForecastRecord(
                id=uuid4(),
                station_id=_SID,
                nwp_source="icon_ch2_eps",
                cycle_time=ts,
                valid_time=ts,
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=3.0,
                created_at=ts,
            ),
            WeatherForecastRecord(
                id=uuid4(),
                station_id=_SID,
                nwp_source="icon_ch2_eps",
                cycle_time=ts,
                valid_time=ts,
                parameter="temperature",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=12.0,
                created_at=ts,
            ),
        ]

        window = seam_window_from_nwp_rows(rows, parameter="precipitation", label="nwp")

        assert window.values == (3.0,)
