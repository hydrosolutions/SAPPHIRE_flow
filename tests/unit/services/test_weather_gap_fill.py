from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from pydantic import ValidationError

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.config.weather_gap import ModelWeatherGapConfig, WeatherGapConfig
from sapphire_flow.services.input_quality import selected_weather_quality_flags
from sapphire_flow.services.weather_gap_fill import (
    fill_daily_weather,
    prepare_group_weather,
    prepare_station_weather,
    stored_weather_donors,
    weather_fill_parameters,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.weather_gap import WeatherGapMethod, WeatherGapMode
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.operational_weather import ISSUE
from tests.fakes.weather_periods import FIRST, GRID
from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
    RecordingFIForecastModel,
)
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _group_model_inputs,
    _station_model_inputs,
)
from tests.unit.services.test_recap_daily_weather import qualified_rows


def day(offset: int) -> UtcDatetime:
    return ensure_utc(ISSUE - timedelta(days=offset))


def frame(values: list[float | None], parameter: str = "temperature") -> pl.DataFrame:
    return pl.DataFrame(
        {"timestamp": [day(i) for i in range(len(values), 0, -1)], parameter: values}
    )


class TestFillPeriodWeather:
    @pytest.mark.parametrize("lookback", [0, 1])
    def test_refuses_off_grid_rows_even_outside_the_requested_lookback(
        self, lookback: int
    ) -> None:
        original = pl.DataFrame(
            {
                "timestamp": [FIRST - 3 * GRID.step + timedelta(hours=6)],
                "temperature": [1.0],
            }
        )
        with pytest.raises(ValueError, match="does not match the declared grid"):
            fill_daily_weather(
                original,
                issue_time=FIRST,
                lookbacks={"temperature": lookback} if lookback else {},
                max_gap_days=1,
                grid=GRID,
                past_end=FIRST,
            )

    @pytest.mark.parametrize("lookback", [0, 3])
    def test_empty_columnless_frame_remains_empty(self, lookback: int) -> None:
        original = pl.DataFrame()
        result = fill_daily_weather(
            original,
            issue_time=FIRST,
            lookbacks={"temperature": lookback} if lookback else {},
            max_gap_days=1,
            grid=GRID,
            past_end=FIRST,
        )
        assert result.frame is original
        assert result.fills == ()

    @pytest.mark.parametrize(
        "columns, message",
        [
            ({"temperature": [1.0]}, "requires a timestamp column"),
            ({"timestamp": [None]}, "requires datetime timestamps"),
        ],
    )
    def test_nonempty_frame_requires_datetime_timestamps(
        self, columns: dict[str, list[float | None]], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            fill_daily_weather(
                pl.DataFrame(columns),
                issue_time=FIRST,
                lookbacks={},
                max_gap_days=1,
                grid=GRID,
                past_end=FIRST,
            )

    def test_aligned_missing_row_is_filled_without_changing_original(self) -> None:
        original = pl.DataFrame(
            {
                "timestamp": [FIRST - 3 * GRID.step, FIRST - GRID.step],
                "temperature": [0.0, 6.0],
            }
        )
        result = fill_daily_weather(
            original,
            issue_time=FIRST,
            lookbacks={"temperature": 3},
            max_gap_days=1,
            grid=GRID,
            past_end=FIRST,
        )
        assert result.frame["timestamp"].to_list() == [
            FIRST - offset * GRID.step for offset in (3, 2, 1)
        ]
        assert result.frame["temperature"].to_list() == [0.0, 3.0, 6.0]
        assert len(result.fills) == 1
        assert original.height == 2

    @pytest.mark.parametrize(
        "stamp, message",
        [
            (FIRST.replace(tzinfo=None), "timezone-aware UTC"),
            (
                FIRST.astimezone(ZoneInfo("Asia/Kathmandu")),
                "timezone-aware UTC",
            ),
            (FIRST.date(), "requires datetime timestamps"),
        ],
        ids=["naive", "non-utc", "date-only"],
    )
    def test_rejects_non_utc_or_date_only_timestamps(
        self, stamp: datetime | date, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            fill_daily_weather(
                pl.DataFrame({"timestamp": [stamp], "temperature": [1.0]}),
                issue_time=FIRST,
                lookbacks={},
                max_gap_days=1,
                grid=GRID,
                past_end=FIRST,
            )


class TestFillDailyWeather:
    @pytest.mark.parametrize(
        "parameter", ["precipitation", "temperature", "global_radiation"]
    )
    def test_single_gap_uses_original_neighbors_without_mutating_input(
        self, parameter: str
    ) -> None:
        original = frame([0.0, None, 6.0], parameter)
        result = fill_daily_weather(
            original, issue_time=ISSUE, lookbacks={parameter: 3}, max_gap_days=1
        )
        assert result.frame[parameter].to_list() == [0.0, 3.0, 6.0]
        assert original[parameter].to_list() == [0.0, None, 6.0]
        assert result.fills[0].value == 3.0
        assert result.fills[0].method is WeatherGapMethod.LINEAR

    @pytest.mark.parametrize(
        "values",
        [
            [0.0, 0.0, 0.0],
            [None, 1.0, None],
            [1.0, None, None, 9.0],
            [None, None, None],
        ],
    )
    def test_complete_zero_edges_long_gaps_and_missing_histories_are_not_filled(
        self, values: list[float | None]
    ) -> None:
        original = frame(values)
        result = fill_daily_weather(
            original,
            issue_time=ISSUE,
            lookbacks={"temperature": len(values)},
            max_gap_days=1,
        )
        assert result.frame is original
        assert not result.fills

    def test_missing_row_and_nan_both_count_as_missing(self) -> None:
        original = frame([0.0, float("nan"), 6.0, None, 10.0]).filter(
            pl.col("timestamp") != day(2)
        )
        result = fill_daily_weather(
            original, issue_time=ISSUE, lookbacks={"temperature": 5}, max_gap_days=1
        )
        assert result.frame["temperature"].to_list() == [0.0, 3.0, 6.0, 8.0, 10.0]

    @pytest.mark.parametrize(
        "parameter,expected",
        [
            ("temperature", [0.0, 3.0, 6.0, 9.0]),
            ("global_radiation", [0.0, 3.0, 6.0, 9.0]),
            ("precipitation", [0.0, None, None, 9.0]),
        ],
    )
    def test_raised_limit_does_not_enable_multi_day_rain_interpolation(
        self, parameter: str, expected: list[float | None]
    ) -> None:
        result = fill_daily_weather(
            frame([0.0, None, None, 9.0], parameter),
            issue_time=ISSUE,
            lookbacks={parameter: 4},
            max_gap_days=2,
        )
        assert result.frame[parameter].to_list() == expected

    def test_partial_rain_total_cannot_be_replaced_by_a_lower_estimate(self) -> None:
        result = fill_daily_weather(
            frame([1.0, None, 3.0], "precipitation"),
            issue_time=ISSUE,
            lookbacks={"precipitation": 3},
            max_gap_days=1,
            precipitation_lower_bounds={day(2): 4.0},
        )
        assert not result.fills

    def test_own_lookback_excludes_older_gaps(self) -> None:
        result = fill_daily_weather(
            frame([0.0, None, 6.0]),
            issue_time=ISSUE,
            lookbacks={"temperature": 1},
            max_gap_days=1,
        )
        assert not result.fills

    def test_duplicate_days_are_not_silently_deduplicated(self) -> None:
        original = frame([1.0, None, 3.0])
        with pytest.raises(ValueError, match="duplicate daily"):
            fill_daily_weather(
                pl.concat([original, original]),
                issue_time=ISSUE,
                lookbacks={"temperature": 3},
                max_gap_days=1,
            )


class TestStoredWeatherDonors:
    def test_complete_same_day_forecast_precedes_interpolation_and_fills_tail(
        self,
    ) -> None:
        rows = qualified_rows(day(3))
        donors = stored_weather_donors(
            rows,
            station_id=rows[0].station_id,
            issue_time=ISSUE,
            parameters=frozenset({"precipitation"}),
        )
        result = fill_daily_weather(
            frame([0.0, None, 4.0, None], "precipitation"),
            issue_time=ISSUE,
            lookbacks={"precipitation": 4},
            max_gap_days=1,
            donors=donors,
        )
        assert result.frame["precipitation"].to_list() == [0.0, 8.0, 4.0, 8.0]
        assert all(
            fill.method is WeatherGapMethod.STORED_FORECAST for fill in result.fills
        )
        assert all(len(fill.donor_records) == 8 for fill in result.fills)

    def test_donors_do_not_shorten_original_long_gap(self) -> None:
        rows = qualified_rows(day(3))
        donors = stored_weather_donors(
            rows,
            station_id=rows[0].station_id,
            issue_time=ISSUE,
            parameters=frozenset({"temperature"}),
        )
        result = fill_daily_weather(
            frame([0.0, None, None]),
            issue_time=ISSUE,
            lookbacks={"temperature": 3},
            max_gap_days=1,
            donors=donors,
        )
        assert not result.fills

    @pytest.mark.parametrize(
        "fault",
        [
            "missing",
            "duplicate",
            "nan",
            "negative",
            "wrong_member",
            "wrong_source",
            "future_cycle",
            "gap",
        ],
    )
    def test_unqualified_daily_support_is_not_a_donor(self, fault: str) -> None:
        rows = [
            r
            for r in qualified_rows(day(1))
            if r.valid_time < ISSUE and r.parameter == "precipitation"
        ]
        sid = rows[0].station_id
        if fault == "missing":
            rows = rows[1:]
        elif fault == "duplicate":
            rows.append(rows[0])
        else:
            changes = {
                "nan": {"value": float("nan")},
                "negative": {"value": -1.0},
                "wrong_member": {"member_id": 1},
                "wrong_source": {"nwp_source": "unknown"},
                "future_cycle": {"cycle_time": ensure_utc(ISSUE + timedelta(days=1))},
                "gap": {"is_gap": True, "gap_status": "unrecoverable"},
            }[fault]
            rows[0] = replace(rows[0], **changes)
        assert not stored_weather_donors(
            rows,
            station_id=sid,
            issue_time=ISSUE,
            parameters=frozenset({"precipitation"}),
        )


class TestGapWarning:
    @pytest.mark.parametrize("age,expected", [(5, 0), (4, 1), (2, 1)])
    def test_five_day_boundary_uses_fixed_issue_day(
        self, age: int, expected: int
    ) -> None:
        values: list[float | None] = [1.0] * 7
        values[7 - age] = None
        result = fill_daily_weather(
            frame(values),
            issue_time=ISSUE,
            lookbacks={"temperature": 7},
            max_gap_days=1,
        )
        assert len(result.fills) == 1
        source = StationSourceEvidence(weather_gap_fills=result.fills)
        for issue in (ISSUE, ensure_utc(ISSUE + timedelta(hours=20))):
            flags = selected_weather_quality_flags(source, issue_time=issue)
            assert len(flags) == expected
            if expected:
                assert "Missing weather data estimated" in flags[0].detail


class TestWeatherGapConfiguration:
    def test_override_fields_inherit_independently(self) -> None:
        config = WeatherGapConfig(
            mode=WeatherGapMode.ALWAYS,
            max_gap_days=2,
            models={
                "strict": ModelWeatherGapConfig(mode=WeatherGapMode.STRICT_MODELS),
                "off": ModelWeatherGapConfig(mode=WeatherGapMode.DISABLED),
                "longer": ModelWeatherGapConfig(max_gap_days=3),
            },
        )
        assert config.for_model("strict").max_gap_days == 2
        assert config.for_model("longer").mode is WeatherGapMode.ALWAYS
        assert config.for_model("longer").max_gap_days == 3
        assert config.for_model("off").mode is WeatherGapMode.DISABLED
        assert WeatherGapConfig().for_model("any").mode is WeatherGapMode.STRICT_MODELS

    @pytest.mark.parametrize("limit", [0, 8, True, 1.5])
    def test_gap_bound_is_validated_at_config_boundary(self, limit: object) -> None:
        with pytest.raises(ValidationError):
            WeatherGapConfig.model_validate({"max_gap_days": limit})


class TestPerModelPreparation:
    @pytest.mark.parametrize(
        "tolerance,mode,expected",
        [
            (0, WeatherGapMode.STRICT_MODELS, True),
            (1, WeatherGapMode.STRICT_MODELS, False),
            (1, WeatherGapMode.ALWAYS, True),
            (0, WeatherGapMode.DISABLED, False),
        ],
    )
    def test_fi_weather_tolerance_controls_default_filling(
        self, tolerance: int, mode: WeatherGapMode, expected: bool
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
                                "reanalysis": {
                                    "temperature": fi.PastKnownVariable(
                                        lookback=3,
                                        max_nan=tolerance,
                                        unit=fi.Unit.DEG_C,
                                    )
                                }
                            },
                            future_known={
                                "nwp": {
                                    "temperature": fi.FutureKnownVariable(
                                        future_steps=1, max_nan=0, unit=fi.Unit.DEG_C
                                    )
                                }
                            },
                        )
                    }
                )
            },
        )
        result = fi.ModelFailure(
            model_name="fixture",
            issue_datetime=ISSUE,
            cause=fi.FailureCause.MODEL_ERROR,
            message="unused",
        )
        model = fi.ForecastInterfaceAdapter(
            RecordingFIForecastModel(result, requirement=requirement),
            station_code_resolver=str,
        )
        parameters = weather_fill_parameters(
            model,
            model_id=ModelId("fixture"),
            config=WeatherGapConfig(mode=mode),
            time_step=step,
        )
        assert parameters == (frozenset({"temperature"}) if expected else frozenset())

    def test_station_assignment_override_does_not_change_shared_inputs(self) -> None:
        model = FakeStationForecastModel()
        model.data_requirements = replace(model.data_requirements, lookback_steps=3)
        inputs = _station_model_inputs()
        inputs = replace(
            inputs,
            issue_time=ISSUE,
            time_step=timedelta(days=1),
            data=replace(inputs.data, past_dynamic=frame([0.0, None, 6.0])),
        )
        config = WeatherGapConfig(
            mode=WeatherGapMode.ALWAYS,
            models={"off": ModelWeatherGapConfig(mode=WeatherGapMode.DISABLED)},
        )
        filled = prepare_station_weather(
            inputs, model=model, model_id=ModelId("on"), config=config
        )
        untouched = prepare_station_weather(
            inputs, model=model, model_id=ModelId("off"), config=config
        )
        assert untouched is inputs
        assert inputs.data.past_dynamic["temperature"].to_list() == [0.0, None, 6.0]
        assert filled.data.past_dynamic["temperature"].to_list() == [0.0, 3.0, 6.0]
        assert (
            filled.source_evidence is not None
            and len(filled.source_evidence.weather_gap_fills) == 1
        )
        assert not weather_fill_parameters(
            model,
            model_id=ModelId("on"),
            config=WeatherGapConfig(),
            time_step=timedelta(days=1),
        )

    def test_group_preparation_keeps_station_values_and_evidence_separate(self) -> None:
        model = FakeGroupForecastModel()
        model.data_requirements = replace(model.data_requirements, lookback_steps=3)
        inputs = _group_model_inputs()
        past = pl.concat(
            [
                frame(values).with_columns(pl.lit(str(sid)).alias("station_id"))
                for sid, values in zip(
                    inputs.station_ids,
                    ([0.0, None, 6.0], [10.0, None, 20.0]),
                    strict=True,
                )
            ]
        )
        inputs = replace(
            inputs, past_dynamic=past, issue_time=ISSUE, time_step=timedelta(days=1)
        )
        filled = prepare_group_weather(
            inputs,
            model=model,
            model_id=ModelId("on"),
            config=WeatherGapConfig(mode=WeatherGapMode.ALWAYS),
        )
        assert inputs.past_dynamic["temperature"].null_count() == 2
        assert filled.past_dynamic["temperature"].to_list() == [
            0.0,
            3.0,
            6.0,
            10.0,
            15.0,
            20.0,
        ]
        assert [
            source.weather_gap_fills[0].value for _, source in filled.source_evidence
        ] == [3.0, 15.0]
