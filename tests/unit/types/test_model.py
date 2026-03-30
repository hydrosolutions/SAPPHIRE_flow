from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
import xarray as xr

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.model import (
    ModelInputs,
    StationInputData,
    stack_model_inputs,
    validate_forcing_provenance,
)

_STEP = timedelta(hours=1)
_ISSUE = ensure_utc(datetime(2022, 6, 1, 12, tzinfo=UTC))

_SID_A = StationId("station-a")
_SID_B = StationId("station-b")
_SID_C = StationId("station-c")
_GROUP_ID = StationGroupId("group-1")


def _utc(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _make_forcing(
    start: UtcDatetime, n_hours: int, *, with_provenance: bool = False
) -> pl.DataFrame:
    timestamps = [
        ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 3600, tz=UTC))
        for i in range(n_hours)
    ]
    data: dict[str, list] = {
        "timestamp": timestamps,
        "temperature": [20.0 + i * 0.1 for i in range(n_hours)],
        "precipitation": [0.5 * i for i in range(n_hours)],
    }
    if with_provenance:
        from sapphire_flow.types.model import PROVENANCE_SUFFIX

        data[f"temperature{PROVENANCE_SUFFIX}"] = ["OBSERVED"] * n_hours
        data[f"precipitation{PROVENANCE_SUFFIX}"] = ["OBSERVED"] * n_hours
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _make_observations(start: UtcDatetime, n_hours: int) -> pl.DataFrame:
    timestamps = [
        ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 3600, tz=UTC))
        for i in range(n_hours)
    ]
    return pl.DataFrame({
        "timestamp": timestamps,
        "value": [10.0 + i for i in range(n_hours)],
    }).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


def _make_static() -> pl.DataFrame:
    return pl.DataFrame({"mean_elev_m": [500.0], "forest_fraction": [0.6]})


def _make_model_inputs(
    station_id: StationId,
    *,
    with_provenance: bool = False,
    static: pl.DataFrame | None = None,
) -> ModelInputs:
    # Forcing covers lookback + forecast horizon (teacher forcing).
    # +1 because range(n) is exclusive and we need the horizon_end timestamp.
    lookback_start = ensure_utc(_ISSUE - 24 * _STEP)
    horizon_end = ensure_utc(_ISSUE + 5 * _STEP)
    total_hours = int((horizon_end - lookback_start).total_seconds() / 3600) + 1
    return ModelInputs(
        station_id=station_id,
        forcing=_make_forcing(
            lookback_start, total_hours, with_provenance=with_provenance
        ),
        observations=_make_observations(lookback_start, 24),
        static_attributes=static,
        issue_time=_ISSUE,
        forecast_horizon_steps=5,
        time_step=_STEP,
        warm_up_steps=None,
    )


class TestGroupModelInputs:
    def test_for_station_returns_correct_slice(self) -> None:
        inputs = {
            _SID_A: _make_model_inputs(_SID_A),
            _SID_B: _make_model_inputs(_SID_B),
        }
        group = stack_model_inputs(
            group_id=_GROUP_ID, inputs=inputs, issue_time=_ISSUE
        )

        sliced = group.for_station(_SID_A)
        assert isinstance(sliced, StationInputData)
        assert "station_id" not in sliced.past_targets.columns
        assert "station_id" not in sliced.past_dynamic.columns
        assert "station_id" not in sliced.future_dynamic.columns
        assert sliced.past_targets.height == inputs[_SID_A].observations.height
        assert sliced.future_dynamic.height == 5  # 5 horizon steps

    def test_for_station_static_none(self) -> None:
        inputs = {_SID_A: _make_model_inputs(_SID_A, static=None)}
        group = stack_model_inputs(
            group_id=_GROUP_ID, inputs=inputs, issue_time=_ISSUE
        )
        assert group.static is None

        sliced = group.for_station(_SID_A)
        assert sliced.static is None

    def test_for_station_unknown_station_raises(self) -> None:
        inputs = {_SID_A: _make_model_inputs(_SID_A)}
        group = stack_model_inputs(
            group_id=_GROUP_ID, inputs=inputs, issue_time=_ISSUE
        )
        unknown = StationId("unknown")
        with pytest.raises(ValueError, match="not in group"):
            group.for_station(unknown)


class TestStackModelInputs:
    def test_stack_two_stations(self) -> None:
        inputs = {
            _SID_A: _make_model_inputs(_SID_A),
            _SID_B: _make_model_inputs(_SID_B),
        }
        group = stack_model_inputs(
            group_id=_GROUP_ID, inputs=inputs, issue_time=_ISSUE
        )

        assert group.group_id == _GROUP_ID
        assert set(group.station_ids) == {_SID_A, _SID_B}
        assert group.past_targets.columns[0] == "station_id"
        assert group.past_dynamic.columns[0] == "station_id"
        assert group.future_dynamic.columns[0] == "station_id"

        # Row counts: 2 stations * per-station rows
        single_obs_rows = inputs[_SID_A].observations.height
        assert group.past_targets.height == 2 * single_obs_rows

        # Future dynamic: 5 hours per station
        assert group.future_dynamic.height == 2 * 5

        # Past dynamic: 24 hours of lookback + issue_time row per station
        past_per_station = group.past_dynamic.filter(
            pl.col("station_id") == str(_SID_A)
        ).height
        # forcing covers lookback (24h) + horizon (5h) = 29 rows
        # past_dynamic is <= issue_time, so 24+1=25 (hour 0 through hour 24 inclusive)
        assert past_per_station == 25

    def test_stack_preserves_provenance_columns(self) -> None:
        inputs = {
            _SID_A: _make_model_inputs(_SID_A, with_provenance=True),
            _SID_B: _make_model_inputs(_SID_B, with_provenance=True),
        }
        group = stack_model_inputs(
            group_id=_GROUP_ID, inputs=inputs, issue_time=_ISSUE
        )

        # Drop station_id before validating provenance
        past_no_sid = group.past_dynamic.drop("station_id")
        validate_forcing_provenance(past_no_sid)

        future_no_sid = group.future_dynamic.drop("station_id")
        validate_forcing_provenance(future_no_sid)

    def test_stack_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot stack empty inputs dict"):
            stack_model_inputs(group_id=_GROUP_ID, inputs={}, issue_time=_ISSUE)

    def test_stack_xr_dataset_raises(self) -> None:
        ds = xr.Dataset({"temp": (["x"], [1.0, 2.0])})
        inp = ModelInputs(
            station_id=_SID_A,
            forcing=ds,
            observations=_make_observations(_utc(2022, 5, 31), 24),
            static_attributes=None,
            issue_time=_ISSUE,
            forecast_horizon_steps=5,
            time_step=_STEP,
            warm_up_steps=None,
        )
        with pytest.raises(TypeError, match="requires pl.DataFrame forcing"):
            stack_model_inputs(
                group_id=_GROUP_ID, inputs={_SID_A: inp}, issue_time=_ISSUE
            )

    def test_stack_inconsistent_issue_time_raises(self) -> None:
        inp_a = _make_model_inputs(_SID_A)
        different_time = ensure_utc(datetime(2022, 7, 1, tzinfo=UTC))
        inp_b = ModelInputs(
            station_id=_SID_B,
            forcing=_make_forcing(ensure_utc(different_time - 24 * _STEP), 29),
            observations=_make_observations(
                ensure_utc(different_time - 24 * _STEP), 24
            ),
            static_attributes=None,
            issue_time=different_time,
            forecast_horizon_steps=5,
            time_step=_STEP,
            warm_up_steps=None,
        )
        with pytest.raises(ValueError, match="Inconsistent issue_time"):
            stack_model_inputs(
                group_id=_GROUP_ID,
                inputs={_SID_A: inp_a, _SID_B: inp_b},
                issue_time=_ISSUE,
            )

    def test_roundtrip_stack_then_slice(self) -> None:
        statics = {
            _SID_A: _make_static(),
            _SID_B: pl.DataFrame({"mean_elev_m": [800.0], "forest_fraction": [0.3]}),
            _SID_C: pl.DataFrame({"mean_elev_m": [200.0], "forest_fraction": [0.9]}),
        }
        inputs = {
            sid: _make_model_inputs(sid, static=statics[sid])
            for sid in (_SID_A, _SID_B, _SID_C)
        }
        group = stack_model_inputs(
            group_id=_GROUP_ID, inputs=inputs, issue_time=_ISSUE
        )

        for sid, original in inputs.items():
            sliced = group.for_station(sid)

            # past_targets rows match original observations
            assert sliced.past_targets.height == original.observations.height
            expected_vals = original.observations["value"].to_list()
            assert sliced.past_targets["value"].to_list() == expected_vals

            # future_dynamic rows match original forcing rows > issue_time
            original_forcing = original.forcing
            assert isinstance(original_forcing, pl.DataFrame)
            expected_future = original_forcing.filter(
                pl.col("timestamp") > _ISSUE
            )
            assert sliced.future_dynamic.height == expected_future.height
            assert (
                sliced.future_dynamic["temperature"].to_list()
                == expected_future["temperature"].to_list()
            )

            # static roundtrips
            assert sliced.static is not None
            expected_elev = statics[sid]["mean_elev_m"].to_list()
            assert sliced.static["mean_elev_m"].to_list() == expected_elev
