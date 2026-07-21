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

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.diagnostics.seam_gate import (
    SeamEdge,
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
_DAY = timedelta(days=1)


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

    def test_nan_value_is_flagged_not_silently_passed(self) -> None:
        # NaN comparisons are always False (`nan < 0` and `nan > MAX` are both
        # False), so a naive range check lets it through; the ratio path can
        # then also fall through to PASS since `nan >= threshold` is False
        # too. A NaN reading must be flagged directly, not silently accepted.
        before = SeamWindow(label="rhiresd", values=(4.0, 6.0, 5.0))
        after = SeamWindow(label="rprelimd", values=(math.nan, 6.0, 5.0))

        result = check_seam_continuity(before, after)

        assert result.verdict is SeamGateVerdict.UNIT_ERROR_SUSPECTED

    def test_infinite_value_is_flagged_not_silently_passed(self) -> None:
        before = SeamWindow(label="rhiresd", values=(4.0, 6.0, 5.0))
        after = SeamWindow(label="rprelimd", values=(math.inf, 6.0, 5.0))

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


_NWP_CYCLE = datetime(2026, 5, 1, tzinfo=UTC)


def _nwp_row(
    *,
    ts: datetime,
    parameter: str,
    member_id: int,
    value: float,
    station_id: StationId = _SID,
    nwp_source: str = "icon_ch2_eps",
    cycle_time: datetime = _NWP_CYCLE,
) -> WeatherForecastRecord:
    return WeatherForecastRecord(
        id=uuid4(),
        station_id=station_id,
        nwp_source=nwp_source,
        cycle_time=cycle_time,
        valid_time=ts,
        parameter=parameter,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        member_id=member_id,
        value=value,
        created_at=ts,
    )


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
            station_id=_SID,
            source=ForcingSource.METEOSWISS_RHIRESD,
            parameter="precipitation",
            label="rhiresd",
            edge=SeamEdge.BEFORE,
            window_size=5,
            seam_time=ts + _DAY,
        )

        assert window.values == (4.0,)

    def test_seam_window_from_forcing_rows_filters_by_station_id(self) -> None:
        # A raw multi-station fetch (e.g. a T1 query over both staging
        # stations) must not interleave another station's rows into this
        # station's seam window.
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        other_sid = StationId(uuid4())
        rows = [
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=ts,
                value=4.0,
            ),
            make_raw_historical_forcing(
                station_id=other_sid,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=ts,
                value=9999.0,  # other station's value — must be excluded
            ),
        ]

        window = seam_window_from_forcing_rows(
            rows,
            station_id=_SID,
            source=ForcingSource.METEOSWISS_RHIRESD,
            parameter="precipitation",
            label="rhiresd",
            edge=SeamEdge.BEFORE,
            window_size=5,
            seam_time=ts + _DAY,
        )

        assert window.values == (4.0,)

    def test_seam_window_from_nwp_rows_filters_by_parameter(self) -> None:
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        rows = [
            _nwp_row(ts=ts, parameter="precipitation", member_id=0, value=3.0),
            _nwp_row(ts=ts, parameter="temperature", member_id=0, value=12.0),
        ]

        window = seam_window_from_nwp_rows(
            rows,
            station_id=_SID,
            nwp_source="icon_ch2_eps",
            cycle_time=_NWP_CYCLE,
            parameter="precipitation",
            label="nwp",
            edge=SeamEdge.AFTER,
            window_size=5,
            seam_time=ts,
        )

        assert window.values == (3.0,)

    def test_seam_window_from_nwp_rows_filters_by_station_source_and_cycle(
        self,
    ) -> None:
        # A raw fetch spanning another station, another NWP source, or an
        # earlier/later cycle must not leak into this seam window.
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        other_sid = StationId(uuid4())
        rows = [
            _nwp_row(ts=ts, parameter="precipitation", member_id=0, value=3.0),
            _nwp_row(
                ts=ts,
                parameter="precipitation",
                member_id=0,
                value=9999.0,  # other station — must be excluded
                station_id=other_sid,
            ),
            _nwp_row(
                ts=ts,
                parameter="precipitation",
                member_id=0,
                value=8888.0,  # other nwp_source — must be excluded
                nwp_source="icon_eu",
            ),
            _nwp_row(
                ts=ts,
                parameter="precipitation",
                member_id=0,
                value=7777.0,  # other cycle_time — must be excluded
                cycle_time=_NWP_CYCLE - _DAY,
            ),
        ]

        window = seam_window_from_nwp_rows(
            rows,
            station_id=_SID,
            nwp_source="icon_ch2_eps",
            cycle_time=_NWP_CYCLE,
            parameter="precipitation",
            label="nwp",
            edge=SeamEdge.AFTER,
            window_size=5,
            seam_time=ts,
        )

        assert window.values == (3.0,)

    def test_seam_window_from_forcing_rows_selects_only_rows_local_to_the_seam(
        self,
    ) -> None:
        # A raw fetch spanning far MORE history than the seam window must not
        # change the verdict: only the last `window_size` rows before the
        # seam (by valid_time) are selected, distant history is excluded.
        base = datetime(2026, 6, 1, tzinfo=UTC)
        far_history = [
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=base - (100 + i) * _DAY,
                value=9999.0,  # would blow up any ratio if it leaked in
            )
            for i in range(20)
        ]
        seam_local = [
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RHIRESD.value,
                parameter="precipitation",
                valid_time=base - i * _DAY,
                value=5.0,
            )
            for i in range(3)
        ]

        window = seam_window_from_forcing_rows(
            far_history + seam_local,
            station_id=_SID,
            source=ForcingSource.METEOSWISS_RHIRESD,
            parameter="precipitation",
            label="rhiresd",
            edge=SeamEdge.BEFORE,
            window_size=3,
            seam_time=base + _DAY,
        )

        assert window.values == (5.0, 5.0, 5.0)

    def test_seam_window_from_nwp_rows_selects_only_rows_local_to_the_seam(
        self,
    ) -> None:
        base = datetime(2026, 6, 1, tzinfo=UTC)
        far_future = [
            _nwp_row(
                ts=base + (100 + i) * _DAY,
                parameter="precipitation",
                member_id=0,
                value=9999.0,
            )
            for i in range(20)
        ]
        seam_local = [
            _nwp_row(
                ts=base + i * _DAY, parameter="precipitation", member_id=0, value=3.0
            )
            for i in range(3)
        ]

        window = seam_window_from_nwp_rows(
            far_future + seam_local,
            station_id=_SID,
            nwp_source="icon_ch2_eps",
            cycle_time=_NWP_CYCLE,
            parameter="precipitation",
            label="nwp",
            edge=SeamEdge.AFTER,
            window_size=3,
            seam_time=base,
        )

        assert window.values == (3.0, 3.0, 3.0)

    def test_seam_window_from_nwp_rows_collapses_ensemble_members_to_one_value(
        self,
    ) -> None:
        # 21 same-scale members at one valid_time must collapse to ONE value
        # (the member mean) — summing all 21 into the window magnitude would
        # inflate the NWP side ~21x versus a single deterministic RprelimD
        # row and falsely flag a correctly-scaled seam.
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        rows = [
            _nwp_row(ts=ts, parameter="precipitation", member_id=member, value=5.0)
            for member in range(21)
        ]

        window = seam_window_from_nwp_rows(
            rows,
            station_id=_SID,
            nwp_source="icon_ch2_eps",
            cycle_time=_NWP_CYCLE,
            parameter="precipitation",
            label="nwp",
            edge=SeamEdge.AFTER,
            window_size=5,
            seam_time=ts,
        )

        assert window.values == (5.0,)

    def test_21_member_nwp_window_passes_against_one_deterministic_row(self) -> None:
        # The end-to-end soundness case: a correctly-scaled 21-member NWP
        # window at the RprelimD -> NWP seam must PASS against a single
        # deterministic RprelimD row of the same order of magnitude.
        rprelimd = SeamWindow(label="rprelimd", values=(5.0,))
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        nwp_rows = [
            _nwp_row(
                ts=ts,
                parameter="precipitation",
                member_id=member,
                value=5.0 + member * 0.1,
            )
            for member in range(21)
        ]
        nwp = seam_window_from_nwp_rows(
            nwp_rows,
            station_id=_SID,
            nwp_source="icon_ch2_eps",
            cycle_time=_NWP_CYCLE,
            parameter="precipitation",
            label="nwp",
            edge=SeamEdge.AFTER,
            window_size=5,
            seam_time=ts,
        )

        result = check_seam_continuity(rprelimd, nwp)

        assert result.verdict is SeamGateVerdict.PASS


class TestAfterWindowAnchoredOnSeamTime:
    def test_older_overlapping_rprelimd_rows_excluded_from_after_window(self) -> None:
        # BUG 2. RprelimD supersession is DEFERRED, so a raw RprelimD fetch can
        # carry rows whose valid_time overlaps the RhiresD period (BEFORE the
        # RhiresD -> RprelimD handoff). The AFTER window must be anchored on the
        # explicit seam_time and draw ONLY post-seam rows — a bare
        # ordered[:window_size] would pull these earlier overlapping rows and
        # silently check the wrong handoff.
        seam = datetime(2026, 6, 15, tzinfo=UTC)
        overlapping_pre_seam = [
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                parameter="precipitation",
                valid_time=seam - (5 - i) * _DAY,  # seam-5 .. seam-1
                value=99.0,  # must NOT appear in the AFTER window
            )
            for i in range(5)
        ]
        post_seam = [
            make_raw_historical_forcing(
                station_id=_SID,
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                parameter="precipitation",
                valid_time=seam + i * _DAY,  # seam, seam+1, seam+2
                value=5.0,
            )
            for i in range(3)
        ]

        window = seam_window_from_forcing_rows(
            overlapping_pre_seam + post_seam,
            station_id=_SID,
            source=ForcingSource.METEOSWISS_RPRELIMD,
            parameter="precipitation",
            label="rprelimd",
            edge=SeamEdge.AFTER,
            window_size=3,
            seam_time=seam,
        )

        assert window.values == (5.0, 5.0, 5.0)
        assert 99.0 not in window.values
