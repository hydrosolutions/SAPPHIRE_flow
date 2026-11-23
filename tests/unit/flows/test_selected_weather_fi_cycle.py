from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

import pandas as pd
import polars as pl
import pytest
from forecast_interface.input.variable import HorizonSemantics

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.services.forecast_evidence import restore_frame, restore_snapshot
from sapphire_flow.types.enums import AlertEligibility, ForecastDataUse
from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
    RecordingFIForecastModel,
)
from tests.unit.adapters.test_recap_operational import ISSUE
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401
)
from tests.unit.flows.test_selected_weather_cycle import (
    FixtureOperationalApi,
    _run_selected_cycle,
)


@pytest.mark.parametrize(
    "max_nan,expected,data_use,horizon_policy,support_days,operational_support_days,gap_age",
    [
        (0, 1, ForecastDataUse.STANDARD, HorizonSemantics.EXACT, None, None, None),
        (
            0,
            0,
            ForecastDataUse.PROVISIONAL_RATING,
            HorizonSemantics.EXACT,
            None,
            None,
            None,
        ),
        (
            1,
            1,
            ForecastDataUse.PROVISIONAL_RATING,
            HorizonSemantics.EXACT,
            None,
            None,
            None,
        ),
        (0, 1, ForecastDataUse.STANDARD, HorizonSemantics.AT_MOST, None, None, None),
        (0, 0, ForecastDataUse.STANDARD, HorizonSemantics.AT_MOST, 3, 3, None),
        (0, 1, ForecastDataUse.STANDARD, HorizonSemantics.AT_MOST, 3, None, None),
        (0, 1, ForecastDataUse.STANDARD, HorizonSemantics.EXACT, None, None, 5),
        (
            1,
            1,
            ForecastDataUse.PROVISIONAL_RATING,
            HorizonSemantics.EXACT,
            None,
            None,
            4,
        ),
    ],
)
def test_selected_native_fi_preserves_q7_p30_nan_gate_and_unequal_horizons(
    monkeypatch: pytest.MonkeyPatch,
    max_nan: int,
    expected: int,
    data_use: ForecastDataUse,
    horizon_policy: HorizonSemantics,
    support_days: int | None,
    operational_support_days: int | None,
    gap_age: int | None,
    uniform_horizon: bool = False,
    refresh_case: Literal["none", "future_fatal"] = "none",
) -> None:
    if gap_age is not None or operational_support_days is not None:
        original_fetch = FixtureOperationalApi.operational

        def limited_support(api: FixtureOperationalApi, **kwargs: object) -> object:
            response = original_fetch(api, **kwargs)
            assert isinstance(response, pd.DataFrame)
            if gap_age is not None:
                missing = ISSUE - timedelta(days=gap_age)
                response.loc[
                    (response.index >= missing)
                    & (response.index < missing + timedelta(days=1)),
                    "g_225",
                ] = float("nan")
            if operational_support_days is not None:
                response = response.loc[
                    response.index < ISSUE + timedelta(days=operational_support_days)
                ].copy()
            return response

        monkeypatch.setattr(FixtureOperationalApi, "operational", limited_support)
    delivered_steps = (support_days or 15) if uniform_horizon else 10
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
                                    future_steps=15 if uniform_horizon else 10,
                                    max_nan=0,
                                    unit=fi.Unit.MM,
                                    horizon_semantics=horizon_policy,
                                    min_future_steps=1
                                    if horizon_policy is HorizonSemantics.AT_MOST
                                    else None,
                                ),
                                "temperature": fi.FutureKnownVariable(
                                    future_steps=15 if uniform_horizon else 3,
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
                            forecast_horizon=delivered_steps,
                            offset=0,
                        ),
                        deterministic=fi.DeterministicData(
                            data=pl.DataFrame(
                                {
                                    "issue_datetime": [ISSUE] * delivered_steps,
                                    "datetime": [
                                        ISSUE + step * i for i in range(delivered_steps)
                                    ],
                                    "value": [30.0] * delivered_steps,
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
        refresh_case=refresh_case,
        capture_fault="none",
        lookback_days=30,
        model_override=adapter,
        missing_observation_days=frozenset({1})
        if data_use is ForecastDataUse.PROVISIONAL_RATING
        else frozenset(),
        data_use=data_use,
        expected_forecasts=expected,
        expected_weather_error="past weather is blocked: missing_support"
        if operational_support_days is not None
        else None,
        consumed_future_count=(
            min(delivered_steps, 6) * 8 + max(delivered_steps - 6, 0) * 4
        )
        * 2
        if uniform_horizon
        else 88,
        negotiated_future=uniform_horizon,
        future_support_days=support_days,
    )
    assert forecasts is not None and len(forecasts) == expected
    if refresh_case == "future_fatal":
        return
    assert (recording.predict_inputs is not None) == bool(expected)
    if recording.predict_inputs is not None:
        delivered = (
            recording.predict_inputs.stations[sid]
            .dynamic[step]
            .data[fi.FISpatialRepresentation.BASIN_AVERAGE]
        )
        assert delivered.past_known["obs"]["discharge"].data.height == 30
        assert delivered.past_known["reanalysis"]["precipitation"].data.height == 30
        assert (
            delivered.future_known["nwp"]["precipitation"].data.height
            == delivered_steps
        )
        assert delivered.future_known["nwp"]["temperature"].data.height == (
            delivered_steps if uniform_horizon else 3
        )
    if gap_age is not None:
        forecast = forecasts[0]
        assert forecast.evidence is not None and forecast.evidence.snapshot is not None
        snapshot = restore_snapshot(forecast.evidence.snapshot)
        fills = snapshot["source_records"][0][1]["weather_use"]["past"]["gap_fills"]
        assert len(fills) == 2
        actual = restore_frame(snapshot["frames"]["past_dynamic"])
        for fill in fills:
            value = actual.filter(
                pl.col("timestamp") == ISSUE - timedelta(days=gap_age)
            )[fill["parameter"]].item()
            assert value == fill["value"]
        warnings = [
            flag
            for flag in forecast.input_quality_flags
            if "Missing weather data estimated" in flag.detail
        ]
        assert bool(warnings) == (gap_age < 5)


@pytest.mark.parametrize("days", [1, 3, 11, 15])
def test_selected_group_uses_bounded_prefix_without_padding(
    monkeypatch: pytest.MonkeyPatch, days: int
) -> None:
    test_selected_native_fi_preserves_q7_p30_nan_gate_and_unequal_horizons(
        monkeypatch,
        0,
        1,
        ForecastDataUse.STANDARD,
        HorizonSemantics.AT_MOST,
        days,
        None,
        None,
        uniform_horizon=True,
    )


def test_negotiated_fatal_refresh_records_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_selected_native_fi_preserves_q7_p30_nan_gate_and_unequal_horizons(
        monkeypatch,
        0,
        0,
        ForecastDataUse.STANDARD,
        HorizonSemantics.AT_MOST,
        None,
        None,
        None,
        uniform_horizon=True,
        refresh_case="future_fatal",
    )


def test_selected_handoff_rejects_changed_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from sapphire_flow.flows import operational_weather_horizon as horizon
    from sapphire_flow.services.operational_weather_proof import WeatherProofError
    from sapphire_flow.services.track_resolution import AcceptedCandidate

    original = horizon.negotiate_weather_horizons

    def changed_cycle(**kwargs: Any) -> horizon.NegotiatedWeather:
        result = original(**kwargs)
        return replace(
            result,
            candidates={
                key: replace(candidate, resolved_cycle=ISSUE + timedelta(days=1))
                if isinstance(candidate, AcceptedCandidate)
                else candidate
                for key, candidate in result.candidates.items()
            },
        )

    monkeypatch.setattr(horizon, "negotiate_weather_horizons", changed_cycle)
    with pytest.raises(WeatherProofError, match="negotiated candidate differs"):
        test_selected_native_fi_preserves_q7_p30_nan_gate_and_unequal_horizons(
            monkeypatch,
            0,
            1,
            ForecastDataUse.STANDARD,
            HorizonSemantics.AT_MOST,
            11,
            None,
            None,
            uniform_horizon=True,
        )
