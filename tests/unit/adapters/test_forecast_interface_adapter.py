from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest
from forecast_interface.output import (
    DeterministicData,
    ForecastFlag,
    ModelOutput,
    QuantileData,
    TemporalResolution,
    TrajectoryData,
    Unit,
    VariableMetadata,
    VariableOutput,
    VariableStatus,
)

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.ids import ModelId, StationId

_STATION_ID = StationId(uuid4())
_MODEL_ID = ModelId("test-model")
_ISSUE_DT = datetime(2026, 4, 1, 0, tzinfo=UTC)
_NUM_STEPS = 5


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_metadata(
    name: str = "discharge",
    unit: Unit = Unit.M3_PER_S,
    timedelta_val: timedelta = timedelta(hours=1),
    forecast_horizon: int = _NUM_STEPS,
) -> VariableMetadata:
    return VariableMetadata(
        name=name,
        unit=unit,
        resolution=TemporalResolution.HOURLY,
        timedelta=timedelta_val,
        forecast_horizon=forecast_horizon,
        offset=0,
    )


def _make_trajectory_df(num_samples: int, num_steps: int) -> pl.DataFrame:
    datetimes = [_ISSUE_DT + timedelta(hours=i + 1) for i in range(num_steps)]
    data: dict[str, list] = {
        "issue_datetime": [_ISSUE_DT] * num_steps,
        "datetime": datetimes,
    }
    for m in range(1, num_samples + 1):
        data[str(m)] = [float(m * 10 + s) for s in range(num_steps)]
    return pl.DataFrame(data)


def _make_quantile_df(quantile_levels: list[float], num_steps: int) -> pl.DataFrame:
    datetimes = [_ISSUE_DT + timedelta(hours=i + 1) for i in range(num_steps)]
    data: dict[str, list] = {
        "issue_datetime": [_ISSUE_DT] * num_steps,
        "datetime": datetimes,
    }
    for q in quantile_levels:
        data[str(q)] = [q * 100 + s for s in range(num_steps)]
    return pl.DataFrame(data)


def _make_deterministic_df(num_steps: int) -> pl.DataFrame:
    datetimes = [_ISSUE_DT + timedelta(hours=i + 1) for i in range(num_steps)]
    return pl.DataFrame(
        {
            "issue_datetime": [_ISSUE_DT] * num_steps,
            "datetime": datetimes,
            "value": [float(i) for i in range(num_steps)],
        }
    )


def _make_trajectory_output(
    num_samples: int = 3,
    num_steps: int = _NUM_STEPS,
    name: str = "discharge",
    status: VariableStatus = VariableStatus.SUCCESS,
    flags: frozenset[ForecastFlag] = frozenset(),
) -> VariableOutput:
    return VariableOutput(
        metadata=_make_metadata(name=name),
        trajectories=TrajectoryData(
            num_samples=num_samples,
            data=_make_trajectory_df(num_samples, num_steps),
        ),
        status=status,
        flags=flags,
    )


def _make_quantile_output(
    quantile_levels: list[float] | None = None,
    num_steps: int = _NUM_STEPS,
    name: str = "discharge",
    status: VariableStatus = VariableStatus.SUCCESS,
) -> VariableOutput:
    levels = quantile_levels or [0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
    return VariableOutput(
        metadata=_make_metadata(name=name),
        quantiles=QuantileData(
            quantile_levels=levels,
            data=_make_quantile_df(levels, num_steps),
        ),
        status=status,
    )


def _make_deterministic_output(
    num_steps: int = _NUM_STEPS,
    name: str = "discharge",
    status: VariableStatus = VariableStatus.SUCCESS,
) -> VariableOutput:
    return VariableOutput(
        metadata=_make_metadata(name=name),
        deterministic=DeterministicData(data=_make_deterministic_df(num_steps)),
        status=status,
    )


def _make_model_output(
    variables: dict[str, VariableOutput],
    issue_datetime: datetime = _ISSUE_DT,
) -> ModelOutput:
    return ModelOutput(
        model_name="test-model",
        issue_datetime=issue_datetime,
        variables=variables,
    )


_ADAPTER = ForecastInterfaceAdapter()
_DEFAULT_TARGETS: frozenset[str] = frozenset({"discharge", "water_level"})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTrajectoryConversion:
    def test_trajectories_to_members_roundtrip(self) -> None:
        output = _make_model_output({"discharge": _make_trajectory_output(3, 5)})
        forecasts, _ = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            model_id=_MODEL_ID,
            target_parameters=frozenset({"discharge"}),
        )

        ens = forecasts["discharge"]
        assert ens.representation == EnsembleRepresentation.MEMBERS
        assert ens.member_count == 3
        assert ens.values.columns == ["valid_time", "member_id", "value"]
        assert ens.values["member_id"].dtype == pl.Int32
        assert ens.values["value"].dtype == pl.Float64
        assert len(ens.values) == 15  # 3 members × 5 steps
        assert ens.parameter == "discharge"
        assert ens.units == "m³/s"
        assert ens.time_step == timedelta(hours=1)


class TestQuantileConversion:
    def test_quantiles_to_quantiles_roundtrip(self) -> None:
        levels = [0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
        output = _make_model_output(
            {"discharge": _make_quantile_output(quantile_levels=levels, num_steps=5)}
        )
        forecasts, _ = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            model_id=_MODEL_ID,
            target_parameters=frozenset({"discharge"}),
        )

        ens = forecasts["discharge"]
        assert ens.representation == EnsembleRepresentation.QUANTILES
        assert ens.member_count == 9
        assert ens.values.columns == ["valid_time", "quantile", "value"]
        assert ens.values["quantile"].dtype == pl.Float64
        assert len(ens.values) == 45  # 9 levels × 5 steps


class TestDeterministicConversion:
    def test_deterministic_to_single_member(self) -> None:
        output = _make_model_output(
            {"discharge": _make_deterministic_output(num_steps=5)}
        )
        forecasts, _ = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            model_id=_MODEL_ID,
            target_parameters=frozenset({"discharge"}),
        )

        ens = forecasts["discharge"]
        assert ens.representation == EnsembleRepresentation.MEMBERS
        assert ens.member_count == 1
        assert ens.values.columns == ["valid_time", "member_id", "value"]
        assert ens.values["member_id"].to_list() == [1] * 5
        assert len(ens.values) == 5


class TestFailureHandling:
    def test_all_failure_raises_model_output_error(self) -> None:
        output = _make_model_output(
            {
                "discharge": _make_trajectory_output(
                    status=VariableStatus.FAILURE,
                )
            }
        )
        with pytest.raises(ModelOutputError, match="All variables failed"):
            _ADAPTER.convert_output(
                output,
                station_id=_STATION_ID,
                target_parameters=frozenset({"discharge"}),
            )

    def test_partial_status_creates_qc_flag(self) -> None:
        output = _make_model_output(
            {
                "discharge": _make_trajectory_output(
                    status=VariableStatus.PARTIAL,
                )
            }
        )
        forecasts, qc_flags = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            target_parameters=frozenset({"discharge"}),
        )

        assert "discharge" in forecasts
        partial_flags = [f for f in qc_flags if f.rule_id == "fi_partial_output"]
        assert len(partial_flags) == 1
        assert partial_flags[0].status == QcStatus.QC_SUSPECT

    def test_empty_variables_rejected_by_validation(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="at least one entry"):
            _make_model_output(variables={})


class TestEnumBoundaryConversion:
    def test_unit_mapping(self) -> None:
        output = _make_model_output({"discharge": _make_trajectory_output()})
        forecasts, _ = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            target_parameters=frozenset({"discharge"}),
        )
        assert forecasts["discharge"].units == "m³/s"

    def test_forecast_flag_to_qc_flag(self) -> None:
        output = _make_model_output(
            {
                "discharge": _make_trajectory_output(
                    flags=frozenset({ForecastFlag.DATA_AVAILABILITY})
                )
            }
        )
        _, qc_flags = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            target_parameters=frozenset({"discharge"}),
        )

        flag_rule_ids = [f.rule_id for f in qc_flags]
        assert "fi_data_availability" in flag_rule_ids


class TestParameterValidation:
    def test_unknown_parameter_raises(self) -> None:
        snow_output = VariableOutput(
            metadata=_make_metadata(name="snow_depth"),
            trajectories=TrajectoryData(
                num_samples=3,
                data=_make_trajectory_df(3, 5),
            ),
            status=VariableStatus.SUCCESS,
        )
        output = _make_model_output({"snow_depth": snow_output})
        with pytest.raises(ModelOutputError, match="Unknown or untargeted"):
            _ADAPTER.convert_output(
                output,
                station_id=_STATION_ID,
                target_parameters=frozenset({"snow_depth"}),
            )

    def test_untargeted_parameter_raises(self) -> None:
        output = _make_model_output(
            {
                "water_level": VariableOutput(
                    metadata=_make_metadata(name="water_level"),
                    trajectories=TrajectoryData(
                        num_samples=3,
                        data=_make_trajectory_df(3, 5),
                    ),
                    status=VariableStatus.SUCCESS,
                )
            }
        )
        with pytest.raises(ModelOutputError, match="Unknown or untargeted"):
            _ADAPTER.convert_output(
                output,
                station_id=_STATION_ID,
                target_parameters=frozenset({"discharge"}),
            )


class TestTemporalConversion:
    def test_datetime_column_renamed_to_valid_time(self) -> None:
        output = _make_model_output({"discharge": _make_trajectory_output()})
        forecasts, _ = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            target_parameters=frozenset({"discharge"}),
        )

        df = forecasts["discharge"].values
        assert "valid_time" in df.columns
        assert "datetime" not in df.columns

    def test_issue_datetime_becomes_issued_at(self) -> None:
        issue_dt = datetime(2026, 4, 1, 6, 0, tzinfo=UTC)
        output = _make_model_output(
            {"discharge": _make_trajectory_output()},
            issue_datetime=issue_dt,
        )
        forecasts, _ = _ADAPTER.convert_output(
            output,
            station_id=_STATION_ID,
            target_parameters=frozenset({"discharge"}),
        )

        issued_at = forecasts["discharge"].issued_at
        assert issued_at.tzinfo is not None
        assert issued_at == issue_dt
