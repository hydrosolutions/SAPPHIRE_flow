from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.ids import StationId

if TYPE_CHECKING:
    from sapphire_flow.types.ensemble import ForecastEnsemble

_STEP = timedelta(hours=1)
_ISSUE = datetime(2025, 1, 1, 6, tzinfo=UTC)
_STATION_ID = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_QUANTILE_LEVELS = [0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.95]
_TRAJECTORY_MEMBERS = 8


def _valid_times(count: int) -> list[datetime]:
    return [_ISSUE + (step + 1) * _STEP for step in range(count)]


def _metadata(
    *,
    horizon: int,
    unit: fi_boundary.Unit = fi_boundary.Unit.M3_PER_S,
) -> fi_boundary.VariableMetadata:
    return fi_boundary.VariableMetadata(
        unit=unit,
        timedelta=_STEP,
        forecast_horizon=horizon,
        offset=0,
    )


def _output(
    *,
    horizon: int,
    deterministic: fi_boundary.DeterministicData | None = None,
    quantiles: fi_boundary.QuantileData | None = None,
    trajectories: fi_boundary.TrajectoryData | None = None,
) -> fi_boundary.VariableOutput:
    return fi_boundary.VariableOutput(
        metadata=_metadata(horizon=horizon),
        deterministic=deterministic,
        quantiles=quantiles,
        trajectories=trajectories,
        flags=frozenset(),
        status=fi_boundary.VariableStatus.SUCCESS,
    )


def _output_frame(data: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(data).with_columns(
        pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
        pl.col("datetime").cast(pl.Datetime("us", "UTC")),
    )


def _trajectory_data() -> fi_boundary.TrajectoryData:
    data: dict[str, list[object]] = {
        "issue_datetime": [_ISSUE, _ISSUE, _ISSUE],
        "datetime": _valid_times(3),
    }
    for member_id in range(1, _TRAJECTORY_MEMBERS + 1):
        data[str(member_id)] = [float(member_id * 10 + step) for step in range(3)]

    return fi_boundary.TrajectoryData(
        num_samples=_TRAJECTORY_MEMBERS,
        data=_output_frame(data),
    )


def _quantile_data(
    levels: list[float] | None = None,
    *,
    horizon: int = 2,
) -> fi_boundary.QuantileData:
    quantile_levels = levels or _QUANTILE_LEVELS
    data: dict[str, list[object]] = {
        "issue_datetime": [_ISSUE] * horizon,
        "datetime": _valid_times(horizon),
    }
    for level in quantile_levels:
        data[str(level)] = [float((step + 1) * 100) + level for step in range(horizon)]
    return fi_boundary.QuantileData(
        quantile_levels=quantile_levels,
        data=_output_frame(data),
    )


def _deterministic_data(
    values: list[float] | None = None,
) -> fi_boundary.DeterministicData:
    deterministic_values = values or [100.0, 101.0, 102.0]
    horizon = len(deterministic_values)
    return fi_boundary.DeterministicData(
        data=_output_frame(
            {
                "issue_datetime": [_ISSUE] * horizon,
                "datetime": _valid_times(horizon),
                "value": deterministic_values,
            }
        )
    )


def _convert(var_output: fi_boundary.VariableOutput) -> ForecastEnsemble:
    return fi_boundary._ensemble_from_variable_output(
        station_id=_STATION_ID,
        parameter="discharge",
        issue_datetime=_ISSUE,
        var_output=var_output,
    )


def test_trajectory_output_converts_to_members_ensemble() -> None:
    ensemble = _convert(_output(horizon=3, trajectories=_trajectory_data()))

    expected_values = pl.DataFrame(
        [
            {
                "valid_time": valid_time,
                "member_id": member_id,
                "value": float(member_id * 10 + step),
            }
            for step, valid_time in enumerate(_valid_times(3))
            for member_id in range(1, _TRAJECTORY_MEMBERS + 1)
        ]
    ).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
        pl.col("value").cast(pl.Float64),
    )

    assert ensemble.representation is EnsembleRepresentation.MEMBERS
    assert ensemble.member_count == _TRAJECTORY_MEMBERS
    assert ensemble.units == "m³/s"
    assert ensemble.time_step == _STEP
    assert ensemble.issued_at == _ISSUE
    assert ensemble.model_id is None
    assert ensemble.forecast_horizon_steps == 3
    assert ensemble.values.schema["valid_time"] == pl.Datetime("us", "UTC")
    assert_frame_equal(ensemble.values, expected_values)


def test_quantile_output_converts_to_quantile_ensemble() -> None:
    ensemble = _convert(_output(horizon=2, quantiles=_quantile_data()))

    assert ensemble.representation is EnsembleRepresentation.QUANTILES
    assert ensemble.member_count == len(_QUANTILE_LEVELS)
    assert ensemble.values["quantile"].unique().sort().to_list() == _QUANTILE_LEVELS
    assert ensemble.values.schema["valid_time"] == pl.Datetime("us", "UTC")
    assert ensemble.forecast_horizon_steps == 2


def test_quantile_output_below_runtime_floor_raises_from_factory() -> None:
    low_floor_quantiles = _quantile_data(levels=[0.05, 0.25, 0.50, 0.75, 0.95])

    with pytest.raises(ModelOutputError, match="at least 7 quantile levels") as exc:
        _convert(_output(horizon=2, quantiles=low_floor_quantiles))
    assert isinstance(exc.value.__cause__, ValueError)


def test_deterministic_output_converts_to_single_member_ensemble() -> None:
    ensemble = _convert(_output(horizon=3, deterministic=_deterministic_data()))

    assert ensemble.representation is EnsembleRepresentation.MEMBERS
    assert ensemble.member_count == 1
    assert ensemble.values["member_id"].unique().to_list() == [1]
    assert ensemble.values["value"].to_list() == [100.0, 101.0, 102.0]
    assert ensemble.forecast_horizon_steps == 3


def test_trajectory_representation_wins_when_multiple_are_present() -> None:
    ensemble = _convert(
        _output(
            horizon=3,
            deterministic=_deterministic_data(values=[1.0, 1.0, 1.0]),
            quantiles=_quantile_data(horizon=3),
            trajectories=_trajectory_data(),
        )
    )

    assert ensemble.representation is EnsembleRepresentation.MEMBERS
    assert "quantile" not in ensemble.values.columns
    assert ensemble.member_count == _TRAJECTORY_MEMBERS
    assert ensemble.values["value"].head(_TRAJECTORY_MEMBERS).to_list() == [
        10.0,
        20.0,
        30.0,
        40.0,
        50.0,
        60.0,
        70.0,
        80.0,
    ]


def test_valid_time_count_mismatch_raises_model_output_error() -> None:
    deterministic = fi_boundary.DeterministicData(
        data=_output_frame(
            {
                "issue_datetime": [_ISSUE, _ISSUE, _ISSUE],
                "datetime": [
                    _valid_times(2)[0],
                    _valid_times(2)[0],
                    _valid_times(2)[1],
                ],
                "value": [100.0, 101.0, 102.0],
            }
        )
    )

    with pytest.raises(
        ModelOutputError,
        match="2 unique valid_time values; expected forecast_horizon=3",
    ):
        _convert(_output(horizon=3, deterministic=deterministic))
