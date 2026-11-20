from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.operational_inputs import build_future_dynamic_frame
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    FutureWeatherPolicy,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.time_grid import TimeGrid
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.fakes.operational_weather import ISSUE


def radiation_rows() -> list[WeatherForecastRecord]:
    sid = StationId(uuid4())
    return [
        WeatherForecastRecord(
            id=uuid4(),
            station_id=sid,
            nwp_source="ifs_ecmwf",
            cycle_time=ISSUE,
            valid_time=ensure_utc(ISSUE + timedelta(hours=lead)),
            parameter=parameter,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=0,
            value=100.0 if lead < 144 else 200.0,
            created_at=ISSUE,
        )
        for parameter in ("solar_net_radiation", "thermal_net_radiation")
        for lead in (*range(0, 144, 3), *range(144, 360, 6))
    ]


def frame(
    rows: list[WeatherForecastRecord],
    *,
    grid: TimeGrid | None = None,
    first_period_start: UtcDatetime | None = None,
    eligibility_cutoff: UtcDatetime | None = None,
) -> pl.DataFrame:
    return build_future_dynamic_frame(
        rows,
        time_step=timedelta(days=1),
        issue_time=ISSUE,
        forecast_horizon_steps=15,
        future_dynamic_features=frozenset(
            {"solar_net_radiation", "thermal_net_radiation"}
        ),
        ensemble_mode=EnsembleMode.SINGLE,
        weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
        grid=grid,
        first_period_start=first_period_start,
        eligibility_cutoff=eligibility_cutoff,
    )


class TestRadiationFuture:
    def test_complete_fifteen_days_end_at_lead_360(self) -> None:
        result = frame(radiation_rows())
        assert result.height == 15
        assert result["solar_net_radiation"].to_list() == [100.0] * 6 + [200.0] * 9
        assert result["timestamp"][-1] == ISSUE + timedelta(days=14)

    def test_mixed_grid_weights_instead_of_sample_mean(self) -> None:
        start = ensure_utc(ISSUE + timedelta(hours=138))
        result = frame(
            radiation_rows(),
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            first_period_start=start,
            eligibility_cutoff=ISSUE,
        )
        assert result["solar_net_radiation"][0] == 175.0

    def test_missing_interval_never_expands_neighbor(self) -> None:
        rows = [
            row
            for row in radiation_rows()
            if row.valid_time != ISSUE + timedelta(hours=3)
        ]
        assert frame(rows).is_empty()

    def test_previous_day_cycle_has_only_fourteen_supported_days(self) -> None:
        rows = [
            replace(
                row,
                valid_time=ensure_utc(row.valid_time - timedelta(days=1)),
                cycle_time=ensure_utc(ISSUE - timedelta(days=1)),
            )
            for row in radiation_rows()
        ]
        assert frame(rows).height == 14

    def test_unqualified_weather_policy_refuses_radiation(self) -> None:
        with pytest.raises(ValueError, match="qualified complete daily Recap"):
            build_future_dynamic_frame(
                radiation_rows(),
                time_step=timedelta(days=1),
                issue_time=ISSUE,
                forecast_horizon_steps=15,
                future_dynamic_features=frozenset({"solar_net_radiation"}),
                ensemble_mode=EnsembleMode.SINGLE,
            )

    def test_fifteen_period_receipt_proves_same_native_rows(self) -> None:
        from sapphire_flow.services.operational_weather_projection import (
            future_receipt_scope,
        )
        from sapphire_flow.services.operational_weather_proof import (
            WeatherProofError,
            prove_future_receipt,
            verify_future_receipt,
        )
        from sapphire_flow.types.operational_weather import (
            UTC_DAILY_WEATHER_GRID,
            FutureWeatherRequirement,
        )
        from sapphire_flow.types.operational_weather_v2 import (
            FutureWeatherRequestV2,
            PastWeatherRequirementV2,
        )

        rows = radiation_rows()
        past = PastWeatherRequirementV2(
            grid=UTC_DAILY_WEATHER_GRID,
            start=ensure_utc(ISSUE - timedelta(days=365)),
            end=ISSUE,
            parameters=("solar_net_radiation", "thermal_net_radiation"),
        )
        request = FutureWeatherRequestV2(
            station_id=rows[0].station_id,
            source="ifs_ecmwf",
            binding_fingerprint="a" * 64,
            grid=past.grid,
            issue_time=ISSUE,
            eligibility_cutoff=ISSUE,
            first_period_start=past.end,
            requirements=tuple(
                FutureWeatherRequirement(
                    parameter=name,
                    member_ids=(0,),
                    valid_times=tuple(
                        ensure_utc(ISSUE + timedelta(days=i)) for i in range(15)
                    ),
                )
                for name in past.parameters
            ),
        )
        scope = future_receipt_scope(request, cycle_time=ISSUE)
        assert scope is not None
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            fetched=rows,
            readback=rows,
        )
        verify_future_receipt(receipt, rows)
        assert receipt.row_count == len(rows)
        assert rows[-1].valid_time == ISSUE + timedelta(hours=354)
        with pytest.raises(WeatherProofError):
            verify_future_receipt(receipt, rows[:-1])
