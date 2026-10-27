from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Never, TypedDict
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import StoreError
from sapphire_flow.protocols.stores import WeatherForecastStore
from sapphire_flow.services.daily_weather_cycle import assemble_scheduled_daily_weather
from sapphire_flow.services.run_group_forecast import (
    GroupInputAssembly,
    GroupMemberInputReason,
    assemble_group_operational_inputs_outcome,
)
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import FutureWeatherPolicy, NwpCycleSource
from sapphire_flow.types.ids import StationId
from tests.fakes.fake_models import FakeGroupForecastModel
from tests.unit.services.test_daily_weather_completeness import _complete_records
from tests.unit.services.test_operational_inputs import (
    _ISSUE,
    _MODEL_ID,
    _make_stores_and_sources,
)
from tests.unit.services.test_run_group_forecast import _make_group


class CycleArgs(TypedDict):
    assemble: Callable[[UtcDatetime], GroupInputAssembly]
    store: WeatherForecastStore
    station_ids: frozenset[StationId]
    sources: frozenset[str]
    future_features: frozenset[str]
    cycle_time: UtcDatetime
    cycle_source: NwpCycleSource
    reference_time: UtcDatetime | None
    issue_time: UtcDatetime
    now: UtcDatetime
    max_age_hours: float


def _case(
    *,
    now_hours: int = 8,
    missing_hour: int | None = None,
    excluded: StationId | None = None,
    missing_member: int | None = None,
) -> tuple[CycleArgs, list[UtcDatetime], StationId]:
    sid = StationId(uuid4())
    station, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_nwp=False
    )
    records = [replace(r, station_id=sid) for r in _complete_records()]
    weather.store_weather_forecasts(
        [
            r
            for r in records
            if (
                missing_hour is None
                or r.valid_time != _ISSUE + timedelta(hours=missing_hour)
            )
            and not (r.member_id == missing_member and r.parameter == "precipitation")
        ]
    )
    later = _ISSUE + timedelta(hours=6)
    weather.store_weather_forecasts(
        [replace(r, cycle_time=later) for r in records if r.valid_time >= later]
    )
    model = FakeGroupForecastModel()
    model.data_requirements = replace(
        model.data_requirements,
        static_features=frozenset(),
        required_past_targets=frozenset(),
        forecast_horizon_steps=2,
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
    )
    calls: list[UtcDatetime] = []

    def assemble(cycle: UtcDatetime) -> GroupInputAssembly:
        calls.append(cycle)
        return assemble_group_operational_inputs_outcome(
            group=_make_group(sid) if excluded is None else _make_group(sid, excluded),
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
            clock=lambda: _ISSUE + timedelta(hours=now_hours),
            forecast_horizon_steps=2,
            time_step=timedelta(days=1),
            weather_policy=FutureWeatherPolicy.COMPLETE_DAILY,
        )

    kwargs = CycleArgs(
        assemble=assemble,
        store=weather,
        station_ids=frozenset({sid} if excluded is None else {sid, excluded}),
        sources=frozenset({"icon_ch2_eps"}),
        future_features=model.data_requirements.future_dynamic_features,
        cycle_time=later,
        cycle_source=NwpCycleSource.PRIMARY,
        reference_time=later,
        issue_time=_ISSUE,
        now=_ISSUE + timedelta(hours=now_hours),
        max_age_hours=12,
    )
    return kwargs, calls, sid


def test_actual_cached_covering_cycle_and_truthful_provenance() -> None:
    kwargs, calls, sid = _case()
    result = assemble_scheduled_daily_weather(**kwargs)
    assert calls == [_ISSUE + timedelta(hours=6), _ISSUE]
    assert result.source is NwpCycleSource.FALLBACK
    assert result.reference_time == _ISSUE
    assert result.assembly.metadata_by_station[sid].nwp_age_hours == 8
    assert result.assembly.inputs is not None
    assert result.assembly.inputs.future_dynamic["precipitation"].to_list() == [
        24.0,
        24.0,
    ]
    assert {
        r.cycle_time
        for _, e in result.assembly.inputs.source_evidence
        for r in e.future_weather
    } == {_ISSUE}


@pytest.mark.parametrize("hour", [0, 1, 12, 24])
def test_cached_cycle_with_missing_native_records_is_not_selected(hour: int) -> None:
    kwargs, _, _ = _case(missing_hour=hour)
    result = assemble_scheduled_daily_weather(**kwargs)
    assert result.assembly.inputs is None
    assert result.source is NwpCycleSource.PRIMARY


@pytest.mark.parametrize("now_hours", [13, 26])
def test_age_bound_uses_actual_clock_even_after_midnight(now_hours: int) -> None:
    kwargs, calls, _ = _case(now_hours=now_hours)
    result = assemble_scheduled_daily_weather(**kwargs)
    assert result.assembly.inputs is None
    assert _ISSUE not in calls
    assert (
        result.assembly.unavailable_members[0].reason
        is GroupMemberInputReason.INSUFFICIENT_FUTURE
    )


def test_unchanged_choice_keeps_original_classification() -> None:
    kwargs, calls, _ = _case()
    kwargs.update(cycle_time=_ISSUE, reference_time=_ISSUE)
    result = assemble_scheduled_daily_weather(**kwargs)
    assert result.assembly.inputs is not None
    assert result.source is NwpCycleSource.PRIMARY
    assert calls == [_ISSUE]


def test_no_future_features_does_not_resolve_weather() -> None:
    kwargs, calls, _ = _case(now_hours=26)
    kwargs.update(
        future_features=frozenset(),
        cycle_source=NwpCycleSource.RUNOFF_ONLY,
        reference_time=None,
    )
    result = assemble_scheduled_daily_weather(**kwargs)
    assert result.source is NwpCycleSource.RUNOFF_ONLY
    assert result.reference_time is None
    assert calls == [_ISSUE + timedelta(hours=6)]


def test_unknown_source_does_not_search_icon_cache() -> None:
    kwargs, calls, _ = _case()
    kwargs.update(sources=frozenset({"ifs_ecmwf"}))
    result = assemble_scheduled_daily_weather(**kwargs)
    assert result.assembly.inputs is None
    assert calls == [_ISSUE + timedelta(hours=6)]


def test_store_failure_propagates_without_using_partial_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _, _ = _case()

    def fail(*args: object) -> Never:
        raise StoreError("local cache unavailable")

    monkeypatch.setattr(kwargs["store"], "fetch_received_cycles", fail)
    with pytest.raises(StoreError, match="local cache unavailable"):
        assemble_scheduled_daily_weather(**kwargs)


def test_future_or_out_of_window_candidates_are_never_assembled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, calls, _ = _case()
    monkeypatch.setattr(
        kwargs["store"],
        "fetch_received_cycles",
        lambda *args: [_ISSUE - timedelta(days=1), _ISSUE + timedelta(days=1)],
    )
    assert assemble_scheduled_daily_weather(**kwargs).assembly.inputs is None
    assert calls == [_ISSUE + timedelta(hours=6)]


def test_mixed_frozen_roster_retains_explicit_exclusion() -> None:
    excluded = StationId(uuid4())
    kwargs, _, sid = _case(excluded=excluded)
    result = assemble_scheduled_daily_weather(**kwargs)
    assert result.source is NwpCycleSource.FALLBACK
    assert result.assembly.inputs is not None
    assert result.assembly.inputs.station_ids == (sid,)
    assert set(result.assembly.expected_station_ids) == {sid, excluded}
    assert [(m.station_id, m.reason) for m in result.assembly.unavailable_members] == [
        (excluded, GroupMemberInputReason.MISSING_FORECAST_BINDING)
    ]


def test_missing_member_feature_is_not_filled_from_later_cycle() -> None:
    kwargs, _, _ = _case(missing_member=0)
    assert assemble_scheduled_daily_weather(**kwargs).assembly.inputs is None


def test_excess_lookup_results_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs, _, _ = _case()

    def excessive(*args: object) -> list[UtcDatetime]:
        return [_ISSUE] * 4

    monkeypatch.setattr(kwargs["store"], "fetch_received_cycles", excessive)
    with pytest.raises(StoreError, match="cadence bound"):
        assemble_scheduled_daily_weather(**kwargs)
