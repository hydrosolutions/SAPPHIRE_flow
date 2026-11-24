from dataclasses import replace
from datetime import timedelta

import pytest
from polars.testing import assert_frame_equal

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.services.run_group_forecast import assemble_group_operational_inputs
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleMode
from sapphire_flow.types.time_grid import ModelTimeGrid, PeriodLabel, TimeGrid
from tests.conftest import make_observation
from tests.fakes.fake_models import FakeGroupForecastModel
from tests.fakes.weather_periods import FIRST, GRID, ISSUE, SID
from tests.unit.services.test_operational_inputs import _make_stores_and_sources
from tests.unit.services.test_period_weather_evidence import (
    MID,
    assemble,
    entry_and_store,
)
from tests.unit.services.test_run_group_forecast import _make_group


def period_case() -> tuple:
    entry, weather_store = entry_and_store(days=3)
    selected = assemble(entry, weather_store)
    station, basin, obs, _, state, forcing = _make_stores_and_sources(
        SID, with_obs=False, with_nwp=False, with_state=False
    )
    # A real stored value with the DHM reporting boundary must not be relabelled.
    obs.store_observations(
        [
            make_observation(
                station_id=SID,
                timestamp=ensure_utc(FIRST + timedelta(minutes=15)),
                value=123.0,
            )
        ]
    )
    model = FakeGroupForecastModel()
    model.data_requirements = replace(
        model.data_requirements,
        time_grid=ModelTimeGrid(grid=GRID, label=PeriodLabel.START),
        supported_time_steps=frozenset({GRID.step}),
        lookback_steps=5,
        declared_lookbacks=frozenset(
            {("discharge", 5), ("precipitation", 3), ("temperature", 3)}
        ),
        forecast_horizon_steps=2,
        required_past_targets=frozenset({"discharge"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        ensemble_mode=EnsembleMode.SINGLE,
    )
    common = dict(
        model=model,
        model_id=MID,
        issue_time=ISSUE,
        cycle_time=ISSUE,
        forcing_source=forcing,
        weather_forecast_store=weather_store,
        obs_store=obs,
        station_store=station,
        basin_store=basin,
        model_state_store=state,
        clock=lambda: ISSUE,
        forecast_horizon_steps=2,
        time_step=GRID.step,
    )
    return model, selected, common


def test_station_preserves_proved_weather_and_missing_discharge_calendar() -> None:
    model, selected, common = period_case()
    result = assemble_station_operational_inputs(
        station_id=SID, nwp_source="ifs_ecmwf", selected_weather=selected, **common
    )
    assert result is not None
    inputs, metadata = result
    assert inputs.time_grid == model.data_requirements.time_grid
    assert inputs.issue_time == ISSUE
    assert inputs.data.past_targets["timestamp"].to_list() == [
        FIRST - GRID.step * i for i in range(5, 0, -1)
    ]
    assert inputs.data.past_targets["discharge"].is_nan().all()
    assert "available_at_us" not in inputs.data.past_targets.columns
    assert metadata.normalized_discharge_steps == 5
    assert metadata.observation_staleness_hours is None
    assert_frame_equal(inputs.data.past_dynamic, selected.past_dynamic)
    assert_frame_equal(inputs.data.future_dynamic, selected.future_dynamic)
    assert inputs.source_evidence.weather_use == selected.use
    assert inputs.source_evidence.observations == ()
    assert inputs.source_evidence.freshness_observations == ()
    assert metadata.nwp_age_hours == 24


def test_group_preserves_grid_and_distinct_weather_and_discharge_lookbacks() -> None:
    model, selected, common = period_case()
    result = assemble_group_operational_inputs(
        group=_make_group(SID),
        nwp_source_by_station={SID: "ifs_ecmwf"},
        selected_weather_by_station={SID: selected},
        **common,
    )
    assert result is not None
    inputs, metadata = result
    assert inputs.time_grid == model.data_requirements.time_grid
    station = inputs.for_station(SID)
    assert station.past_targets.height == 5
    assert station.past_dynamic.height == 3
    assert station.past_targets["discharge"].is_nan().all()
    assert station.past_dynamic["timestamp"].to_list() == [
        FIRST - GRID.step * i for i in range(3, 0, -1)
    ]
    assert station.future_dynamic["timestamp"].min() == FIRST
    assert metadata[SID].normalized_discharge_steps == 5


@pytest.mark.parametrize("change", ["phase", "end", "missing_weather", "unknown_model"])
def test_incompatible_period_inputs_refuse_before_prediction(change: str) -> None:
    model, selected, common = period_case()
    if change == "phase":
        declaration = ModelTimeGrid(
            grid=TimeGrid(step=GRID.step, phase=timedelta(hours=12)),
            label=PeriodLabel.START,
        )
    elif change == "end":
        declaration = ModelTimeGrid(grid=GRID, label=PeriodLabel.END)
    else:
        declaration = (
            None if change == "unknown_model" else model.data_requirements.time_grid
        )
    model.data_requirements = replace(model.data_requirements, time_grid=declaration)
    with pytest.raises(ConfigurationError, match="model time grid|matching selected"):
        assemble_station_operational_inputs(
            station_id=SID,
            nwp_source="ifs_ecmwf",
            selected_weather=None if change == "missing_weather" else selected,
            **common,
        )
