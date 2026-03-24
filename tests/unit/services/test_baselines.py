from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.services.baselines import compute_clim_baselines
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation

_STATION = StationId(uuid4())
_OTHER_STATION = StationId(uuid4())
_PARAM = "discharge"
_CREATED_AT = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))


def _obs(
    timestamp: UtcDatetime,
    value: float | None,
    *,
    station_id: StationId = _STATION,
    parameter: str = _PARAM,
    qc_status: QcStatus = QcStatus.QC_PASSED,
) -> Observation:
    return Observation(
        id=ObservationId(uuid4()),
        station_id=station_id,
        timestamp=timestamp,
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=qc_status if value is not None else QcStatus.MISSING,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_CREATED_AT,
    )


def _daily_obs(
    year: int,
    value_fn: object = None,
    *,
    station_id: StationId = _STATION,
    parameter: str = _PARAM,
) -> list[Observation]:
    result = []
    base = datetime(year, 1, 1, tzinfo=UTC)
    for day in range(365):
        ts = ensure_utc(base + timedelta(days=day))
        val = float(day + 1) if value_fn is None else float(value_fn)  # type: ignore[arg-type]
        result.append(_obs(ts, val, station_id=station_id, parameter=parameter))
    return result


class TestComputeClimBaselines:
    def test_basic_computation(self) -> None:
        obs: list[Observation] = []
        for year in range(2021, 2024):
            obs.extend(_daily_obs(year))

        baselines = compute_clim_baselines(obs, _STATION, _PARAM, min_samples=3)

        assert len(baselines) >= 365
        assert len(baselines) <= 366
        for b in baselines:
            assert b.station_id == _STATION
            assert b.parameter == _PARAM
            assert 1 <= b.day_of_year <= 366
            assert b.sample_count >= 3
            assert b.rolling_std > 0

    def test_window_wrapping(self) -> None:
        obs: list[Observation] = []
        # Create observations only on days 360-365 and day 1-5 across 20 years
        for year in range(2000, 2020):
            base = datetime(year, 1, 1, tzinfo=UTC)
            for day_offset in list(range(359, 365)) + list(range(0, 5)):
                if day_offset >= 365:
                    continue
                ts = ensure_utc(base + timedelta(days=day_offset))
                obs.append(_obs(ts, 42.0))

        baselines = compute_clim_baselines(
            obs, _STATION, _PARAM, window_half_width=5, min_samples=10
        )

        doy_map = {b.day_of_year: b for b in baselines}
        # day 1 baseline should include late-December data (wrapping)
        assert 1 in doy_map
        assert doy_map[1].sample_count > 20

    def test_insufficient_samples_skipped(self) -> None:
        # Only 3 observations on day 100 across 3 different years — window of ±15
        # yields at most 3 samples, well below min_samples=10
        obs = [
            _obs(ensure_utc(datetime(year, 4, 10, tzinfo=UTC)), float(year))
            for year in range(2021, 2024)
        ]

        baselines = compute_clim_baselines(
            obs, _STATION, _PARAM, window_half_width=15, min_samples=10
        )

        assert len(baselines) == 0

    def test_filters_by_station_and_parameter(self) -> None:
        target_obs = _daily_obs(2020, station_id=_STATION)
        other_obs = _daily_obs(2020, station_id=_OTHER_STATION)
        for year in range(2021, 2030):
            target_obs.extend(_daily_obs(year, station_id=_STATION))
            other_obs.extend(_daily_obs(year, station_id=_OTHER_STATION))

        all_obs = target_obs + other_obs
        baselines = compute_clim_baselines(all_obs, _STATION, _PARAM, min_samples=5)

        assert all(b.station_id == _STATION for b in baselines)
        assert len(baselines) > 0

    def test_none_values_excluded(self) -> None:
        obs: list[Observation] = []
        base = datetime(2020, 6, 15, tzinfo=UTC)
        for i in range(20):
            ts = ensure_utc(base + timedelta(days=i * 365))
            # Alternate between real values and None
            if i % 2 == 0:
                obs.append(_obs(ts, 10.0))
            else:
                obs.append(_obs(ts, None))

        baselines = compute_clim_baselines(
            obs, _STATION, _PARAM, window_half_width=15, min_samples=3
        )

        doy_167 = next((b for b in baselines if b.day_of_year == 167), None)
        if doy_167 is not None:
            assert doy_167.sample_count <= 10  # only non-None values counted

    def test_constant_values_nonzero_std(self) -> None:
        obs: list[Observation] = []
        base = datetime(2020, 7, 1, tzinfo=UTC)
        for i in range(20):
            ts = ensure_utc(base + timedelta(days=i * 365))
            obs.append(_obs(ts, 5.0))

        baselines = compute_clim_baselines(
            obs, _STATION, _PARAM, window_half_width=15, min_samples=5
        )

        assert len(baselines) > 0
        for b in baselines:
            assert b.rolling_std == pytest.approx(1e-6)
            assert b.rolling_mean == pytest.approx(5.0)
