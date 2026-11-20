import random
from datetime import timedelta
from uuid import uuid4

import polars as pl

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs
from tests.fakes.operational_weather import ISSUE
from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
    RecordingFIForecastModel,
)


def test_daily_net_radiation_names_units_and_signed_values_reach_predict() -> None:
    step = timedelta(days=1)
    names = ("solar_net_radiation", "thermal_net_radiation")
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
                            "reanalysis": {
                                name: fi.PastKnownVariable(
                                    lookback=1, max_nan=0, unit=fi.Unit.W_PER_M2
                                )
                                for name in names
                            }
                        },
                        future_known={
                            "nwp": {
                                name: fi.FutureKnownVariable(
                                    future_steps=1, max_nan=0, unit=fi.Unit.W_PER_M2
                                )
                                for name in names
                            }
                        },
                    )
                }
            )
        },
    )
    result = fi.ModelSuccess(
        output=fi.ModelOutput(
            model_name="fixture",
            issue_datetime=ISSUE,
            variables={
                "station": {
                    "discharge": fi.VariableOutput(
                        metadata=fi.VariableMetadata(
                            unit=fi.Unit.M3_PER_S,
                            timedelta=step,
                            forecast_horizon=1,
                            offset=0,
                        ),
                        deterministic=fi.DeterministicData(
                            data=pl.DataFrame(
                                {
                                    "issue_datetime": [ISSUE],
                                    "datetime": [ISSUE],
                                    "value": [1.0],
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
    recorder = RecordingFIForecastModel(result, requirement=requirement)
    adapter = fi.ForecastInterfaceAdapter(recorder)
    inputs = StationModelInputs(
        station_id=StationId(uuid4()),
        issue_time=ISSUE,
        time_step=step,
        forecast_horizon_steps=1,
        data=StationInputData(
            past_targets=pl.DataFrame(),
            static=None,
            past_dynamic=pl.DataFrame(
                {"timestamp": [ISSUE - step], names[0]: [100.0], names[1]: [-50.0]}
            ),
            future_dynamic=pl.DataFrame(
                {"timestamp": [ISSUE], names[0]: [175.0], names[1]: [-75.0]}
            ),
        ),
    )
    adapter.predict(object(), inputs, random.Random(1))
    assert recorder.predict_inputs is not None
    delivered = (
        recorder.predict_inputs.stations["station"]
        .dynamic[step]
        .data[fi.FISpatialRepresentation.BASIN_AVERAGE]
    )
    assert set(delivered.past_known["reanalysis"]) == set(names)
    assert set(delivered.future_known["nwp"]) == set(names)
    assert delivered.past_known["reanalysis"][names[1]].data[names[1]].to_list() == [
        -50.0
    ]
    assert delivered.future_known["nwp"][names[0]].data[names[0]].to_list() == [175.0]
