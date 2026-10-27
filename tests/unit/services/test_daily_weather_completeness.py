from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
    build_future_dynamic_frame,
)
from sapphire_flow.types.enums import EnsembleMode, FutureWeatherPolicy
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.unit.services.test_operational_inputs import (
    _ISSUE,
    _MODEL_ID,
    _make_model,
    _make_nwp_records,
    _make_stores_and_sources,
)


def test_partial_midnight_day_is_not_a_daily_total() -> None:
    records = _make_nwp_records(
        StationId(uuid4()), _ISSUE + timedelta(hours=6), _ISSUE + timedelta(hours=5), 43
    )
    # Real hourly rows starting 06Z; ordinary reduction silently sums 18 hours.
    result = build_future_dynamic_frame(
        records,
        time_step=timedelta(days=1),
        issue_time=_ISSUE,
        forecast_horizon_steps=2,
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        ensemble_mode=EnsembleMode.SINGLE,
        weather_policy=FutureWeatherPolicy.COMPLETE_DAILY,
    )
    assert result.is_empty()


def _frame(
    records: list[WeatherForecastRecord], mode: EnsembleMode = EnsembleMode.ENSEMBLE
) -> pl.DataFrame:
    return build_future_dynamic_frame(
        records,
        time_step=timedelta(days=1),
        issue_time=_ISSUE,
        forecast_horizon_steps=2,
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        ensemble_mode=mode,
        weather_policy=FutureWeatherPolicy.COMPLETE_DAILY,
    )


def _complete_records() -> list[WeatherForecastRecord]:
    records = _make_nwp_records(
        StationId(uuid4()), _ISSUE, _ISSUE - timedelta(hours=1), 49
    )
    return [replace(r, value=1.0) for r in records]


def test_complete_midnight_intervals_and_points() -> None:
    records = _complete_records()
    original_times = [r.valid_time for r in records]
    result = _frame(records)
    assert result["timestamp"].to_list() == [_ISSUE, _ISSUE + timedelta(days=1)]
    assert result["precipitation_0"].to_list() == [24.0, 24.0]
    assert result["temperature_2"].to_list() == [1.0, 1.0]
    assert [r.valid_time for r in records] == original_times


@pytest.mark.parametrize(
    "parameter,hour,member",
    [
        ("precipitation", 1, 0),
        ("temperature", 0, 1),
        ("precipitation", 12, 2),
        ("temperature", 23, 2),
        ("precipitation", 24, 1),
    ],
)
def test_missing_required_native_point_rejects_day_zero(
    parameter: str, hour: int, member: int
) -> None:
    records = [
        r
        for r in _complete_records()
        if not (
            r.parameter == parameter
            and r.member_id == member
            and r.valid_time == _ISSUE + timedelta(hours=hour)
        )
    ]
    assert _frame(records).is_empty()


def test_interior_daily_gap_does_not_shift_days() -> None:
    records = [
        r for r in _complete_records() if r.valid_time != _ISSUE + timedelta(hours=36)
    ]
    result = _frame(records)
    assert result["timestamp"].to_list() == [_ISSUE]


def test_unknown_source_not_inferred_from_dense_rows() -> None:
    assert _frame(
        [replace(r, nwp_source="ifs_ecmwf") for r in _complete_records()]
    ).is_empty()


def test_ordinary_six_hour_issue_unchanged() -> None:
    records = _complete_records()
    result = build_future_dynamic_frame(
        records,
        time_step=timedelta(days=1),
        issue_time=_ISSUE + timedelta(hours=6),
        forecast_horizon_steps=1,
        future_dynamic_features=frozenset({"precipitation"}),
        ensemble_mode=EnsembleMode.SINGLE,
    )
    assert result["timestamp"].to_list() == [_ISSUE + timedelta(days=1)]
    assert result["precipitation"].to_list() == [24.0]


@pytest.mark.parametrize("cycle_hour", [0, 6, 12, 18])
def test_real_station_assembly_preserves_source_age_and_gates_future(
    cycle_hour: int,
) -> None:
    sid = StationId(uuid4())
    station, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_nwp=False
    )
    cycle = _ISSUE + timedelta(hours=cycle_hour)
    records = [
        replace(r, station_id=sid, cycle_time=cycle)
        for r in _complete_records()
        if r.valid_time >= cycle
    ]
    weather.store_weather_forecasts(records)
    result = assemble_station_operational_inputs(
        station_id=sid,
        model=_make_model(),
        model_id=_MODEL_ID,
        issue_time=_ISSUE,
        cycle_time=cycle,
        nwp_source="icon_ch2_eps",
        forcing_source=forcing,
        weather_forecast_store=weather,
        obs_store=obs,
        station_store=station,
        basin_store=basin,
        model_state_store=state,
        clock=lambda: _ISSUE + timedelta(hours=20),
        forecast_horizon_steps=2,
        time_step=timedelta(days=1),
        weather_policy=FutureWeatherPolicy.COMPLETE_DAILY,
    )
    assert result is not None
    inputs, metadata = result
    assert inputs.data.future_dynamic.is_empty() == (cycle_hour != 0)
    assert metadata.nwp_age_hours == 20 - cycle_hour
    assert inputs.source_evidence is not None
    assert {r.cycle_time for r in inputs.source_evidence.future_weather} == {cycle}


@pytest.mark.parametrize("cycle_hour", [0, 6])
def test_real_group_path_excludes_partial_member_before_prediction(
    cycle_hour: int,
) -> None:
    from sapphire_flow.services.run_group_forecast import (
        assemble_group_operational_inputs_outcome,
    )
    from tests.fakes.fake_models import FakeGroupForecastModel
    from tests.unit.services.test_run_group_forecast import _make_group

    sid = StationId(uuid4())
    station, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_nwp=False
    )
    cycle = _ISSUE + timedelta(hours=cycle_hour)
    weather.store_weather_forecasts(
        [
            replace(r, station_id=sid, cycle_time=cycle)
            for r in _complete_records()
            if r.valid_time >= cycle
        ]
    )
    model = FakeGroupForecastModel()
    model.data_requirements = replace(
        model.data_requirements,
        static_features=frozenset(),
        required_past_targets=frozenset(),
        forecast_horizon_steps=2,
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
    )
    result = assemble_group_operational_inputs_outcome(
        group=_make_group(sid),
        model=model,
        model_id=_MODEL_ID,
        issue_time=_ISSUE,
        cycle_time=cycle,
        nwp_source_by_station={sid: "icon_ch2_eps"},
        forcing_source=forcing,
        weather_forecast_store=weather,
        obs_store=obs,
        station_store=station,
        basin_store=basin,
        model_state_store=state,
        clock=lambda: _ISSUE + timedelta(hours=20),
        forecast_horizon_steps=2,
        time_step=timedelta(days=1),
        weather_policy=FutureWeatherPolicy.COMPLETE_DAILY,
    )
    assert (result.inputs is None) == (cycle_hour != 0)
    assert len(result.unavailable_members) == (1 if cycle_hour else 0)
