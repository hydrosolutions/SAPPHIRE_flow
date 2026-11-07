from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.operational_inputs import build_future_dynamic_frame
from sapphire_flow.services.operational_weather_proof import WeatherProofError
from sapphire_flow.services.track_resolution import resolve_candidate
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    FutureWeatherPolicy,
    NwpCycleSource,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_track import (
    FeatureName,
    ForcingTrackKey,
    FutureSteps,
    RawFetchOutcome,
    RawFetchStatus,
    ResolvedTrackRequest,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import BasinAverageForecast, WeatherForecastRecord
from tests.fakes.operational_weather import ISSUE


def qualified_rows(cycle: UtcDatetime = ISSUE) -> list[WeatherForecastRecord]:
    sid = StationId(uuid4())
    leads = (
        list(range(0, 145, 3)) + list(range(150, 361, 6))
        if cycle.hour == 0
        else list(range(0, 73, 3))
    )
    return [
        WeatherForecastRecord(
            id=uuid4(),
            station_id=sid,
            nwp_source="ifs_ecmwf",
            cycle_time=cycle,
            valid_time=ensure_utc(cycle + timedelta(hours=lead)),
            parameter=parameter,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=0,
            value=1.0 if parameter == "precipitation" else 10.0,
            created_at=ISSUE,
        )
        for parameter in ("precipitation", "temperature")
        for lead in leads
    ]


def frame(
    rows: list[WeatherForecastRecord], issue: UtcDatetime = ISSUE
) -> pl.DataFrame:
    return build_future_dynamic_frame(
        rows,
        time_step=timedelta(days=1),
        issue_time=issue,
        forecast_horizon_steps=10,
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        ensemble_mode=EnsembleMode.SINGLE,
        weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
    )


class TestRecapCompleteDailyWeather:
    def test_native_frame_has_ten_contiguous_start_labelled_days(self) -> None:
        result = frame(qualified_rows())
        assert result.height == 10
        assert result["timestamp"].to_list() == [
            ISSUE + timedelta(days=day) for day in range(10)
        ]
        assert result["precipitation"].to_list() == [8.0] * 6 + [4.0] * 4
        assert result["temperature"].to_list() == [10.0] * 10

    def test_nonmidnight_issue_is_not_shifted_or_silently_dropped(self) -> None:
        assert frame(
            qualified_rows(), ensure_utc(ISSUE + timedelta(hours=6))
        ).is_empty()

    def test_short_cycle_cannot_claim_partial_terminal_day(self) -> None:
        cycle = ensure_utc(ISSUE - timedelta(hours=12))
        assert frame(qualified_rows(cycle)).height == 2

    def test_missing_first_day_interval_cannot_be_interpolated(self) -> None:
        rows = [
            row
            for row in qualified_rows()
            if not (row.parameter == "precipitation" and row.valid_time == ISSUE)
        ]
        result = frame(rows)
        assert "precipitation" not in result.columns

    def test_duplicate_required_identity_refuses(self) -> None:
        rows = qualified_rows()
        with pytest.raises(WeatherProofError, match="duplicate support"):
            frame(rows + rows[:1])

    def test_nonfinite_value_refuses_instead_of_aggregating_zero(self) -> None:
        rows = qualified_rows()
        rows[0] = replace(rows[0], value=float("nan"))
        with pytest.raises(WeatherProofError, match="invalid required samples"):
            frame(rows)

    def test_feature_horizons_remain_independent(self) -> None:
        result = build_future_dynamic_frame(
            qualified_rows(),
            time_step=timedelta(days=1),
            issue_time=ISSUE,
            forecast_horizon_steps=10,
            future_dynamic_features=frozenset({"precipitation", "temperature"}),
            feature_horizons={"precipitation": 2, "temperature": 10},
            ensemble_mode=EnsembleMode.SINGLE,
            weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
        )
        assert result["precipitation"].drop_nulls().to_list() == [8.0, 8.0]
        assert result["temperature"].drop_nulls().to_list() == [10.0] * 10


class TestSelectedNativeCandidateResolution:
    def test_long_model_walks_back_to_one_eligible_00z_cycle(self) -> None:
        sid = StationId(uuid4())
        features = frozenset({FeatureName("precipitation"), FeatureName("temperature")})
        request = ResolvedTrackRequest(
            key=ForcingTrackKey(
                nwp_source="ifs_ecmwf",
                ensemble_mode=EnsembleMode.SINGLE,
                time_step=timedelta(days=1),
                spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
                features=features,
            ),
            fetch_horizons={feature: FutureSteps(value=10) for feature in features},
            assignments=(),
        )
        visited: list[UtcDatetime] = []

        def fetch(cycle: UtcDatetime) -> RawFetchOutcome:
            visited.append(cycle)
            rows = qualified_rows(cycle)
            return RawFetchOutcome(
                status=RawFetchStatus.FETCHED,
                cycle=cycle,
                stations={
                    sid: BasinAverageForecast(
                        nwp_source="ifs_ecmwf",
                        cycle_time=cycle,
                        values=pl.DataFrame(
                            [
                                {
                                    "valid_time": row.valid_time,
                                    "parameter": row.parameter,
                                    "member_id": row.member_id,
                                    "value": row.value,
                                }
                                for row in rows
                            ]
                        ),
                    )
                },
            )

        result = resolve_candidate(
            request,
            fetch_candidate=fetch,
            expected_member_ids=frozenset({0}),
            nominal_cycle_source=lambda cycle, nominal: (
                NwpCycleSource.PRIMARY if cycle == nominal else NwpCycleSource.FALLBACK
            ),
            nominal_now=ensure_utc(ISSUE - timedelta(hours=6)),
            issue_time=ISSUE,
            cycle_cadence_hours=6,
            max_cycle_age_hours=24,
            clock=lambda: ISSUE,
            id_gen=uuid4,
            weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
        )
        assert result is not None
        assert result.resolved_cycle == ISSUE - timedelta(days=1)
        assert [cycle.hour for cycle in visited] == [18, 12, 6, 0]
        assert {row.cycle_time for row in result.complete_station_records[sid]} == {
            result.resolved_cycle
        }
