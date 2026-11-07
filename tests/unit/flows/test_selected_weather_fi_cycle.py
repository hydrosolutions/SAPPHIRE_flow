from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import polars as pl
import pytest
from forecast_interface.input.variable import HorizonSemantics

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.types.enums import AlertEligibility, ForecastDataUse
from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
    RecordingFIForecastModel,
)
from tests.unit.adapters.test_recap_operational import ISSUE
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401
)
from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle


@pytest.mark.parametrize(
    "max_nan,expected,data_use,horizon_policy,support_days",
    [
        (0, 1, ForecastDataUse.STANDARD, HorizonSemantics.EXACT, None),
        (0, 0, ForecastDataUse.PROVISIONAL_RATING, HorizonSemantics.EXACT, None),
        (1, 1, ForecastDataUse.PROVISIONAL_RATING, HorizonSemantics.EXACT, None),
        (0, 1, ForecastDataUse.STANDARD, HorizonSemantics.AT_MOST, None),
        (0, 0, ForecastDataUse.STANDARD, HorizonSemantics.AT_MOST, 3),
    ],
)
def test_selected_native_fi_preserves_q7_p30_nan_gate_and_unequal_horizons(
    monkeypatch: pytest.MonkeyPatch,
    max_nan: int,
    expected: int,
    data_use: ForecastDataUse,
    horizon_policy: HorizonSemantics,
    support_days: int | None,
) -> None:
    step = timedelta(days=1)
    requirement = fi.InputRequirement(
        targets={
            "discharge": fi.TargetSpec(
                unit=fi.Unit.M3_PER_S,
                representations=frozenset({fi.OutputRepresentation.DETERMINISTIC}),
            )
        },
        dynamic={
            step: fi.SpatialInputSpec(
                data={
                    fi.FISpatialRepresentation.BASIN_AVERAGE: fi.DynamicInputSpec(
                        past_known={
                            "obs": {
                                "discharge": fi.PastKnownVariable(
                                    lookback=7, max_nan=max_nan, unit=fi.Unit.M3_PER_S
                                )
                            },
                            "reanalysis": {
                                "precipitation": fi.PastKnownVariable(
                                    lookback=30, max_nan=0, unit=fi.Unit.MM
                                ),
                                "temperature": fi.PastKnownVariable(
                                    lookback=30, max_nan=0, unit=fi.Unit.DEG_C
                                ),
                            },
                        },
                        future_known={
                            "nwp": {
                                "precipitation": fi.FutureKnownVariable(
                                    future_steps=10,
                                    max_nan=0,
                                    unit=fi.Unit.MM,
                                    horizon_semantics=horizon_policy,
                                    min_future_steps=1
                                    if horizon_policy is HorizonSemantics.AT_MOST
                                    else None,
                                ),
                                "temperature": fi.FutureKnownVariable(
                                    future_steps=3,
                                    max_nan=0,
                                    unit=fi.Unit.DEG_C,
                                    horizon_semantics=horizon_policy,
                                    min_future_steps=1
                                    if horizon_policy is HorizonSemantics.AT_MOST
                                    else None,
                                ),
                            }
                        },
                    )
                }
            )
        },
    )
    sid = str(UUID(int=1))
    result = fi.ModelSuccess(
        output=fi.ModelOutput(
            model_name="fixture",
            issue_datetime=ISSUE,
            variables={
                sid: {
                    "discharge": fi.VariableOutput(
                        metadata=fi.VariableMetadata(
                            unit=fi.Unit.M3_PER_S,
                            timedelta=step,
                            forecast_horizon=10,
                            offset=0,
                        ),
                        deterministic=fi.DeterministicData(
                            data=pl.DataFrame(
                                {
                                    "issue_datetime": [ISSUE] * 10,
                                    "datetime": [ISSUE + step * i for i in range(10)],
                                    "value": [30.0] * 10,
                                }
                            )
                        ),
                        flags=frozenset(),
                        status=fi.VariableStatus.SUCCESS,
                    )
                }
            },
        )
    )
    recording = RecordingFIForecastModel(
        result, artifact_scope=fi.FIArtifactScope.GROUP, requirement=requirement
    )
    adapter = fi.ForecastInterfaceAdapter(
        recording, station_code_resolver=lambda station_id: str(station_id)
    )
    monkeypatch.setattr(
        adapter, "alert_eligibility", AlertEligibility.SKILL_FORECAST, raising=False
    )
    forecasts = _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        lookback_days=30,
        model_override=adapter,
        missing_observation_days=frozenset({1})
        if data_use is ForecastDataUse.PROVISIONAL_RATING
        else frozenset(),
        data_use=data_use,
        expected_forecasts=expected,
        consumed_future_count=88,
        future_support_days=support_days,
    )
    assert forecasts is not None and len(forecasts) == expected
    assert (recording.predict_inputs is not None) == bool(expected)
    if recording.predict_inputs is not None:
        delivered = (
            recording.predict_inputs.stations[sid]
            .dynamic[step]
            .data[fi.FISpatialRepresentation.BASIN_AVERAGE]
        )
        assert delivered.past_known["obs"]["discharge"].data.height == 30
        assert delivered.past_known["reanalysis"]["precipitation"].data.height == 30
        assert delivered.future_known["nwp"]["precipitation"].data.height == 10
        assert delivered.future_known["nwp"]["temperature"].data.height == 3
