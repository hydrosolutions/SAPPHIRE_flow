from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.hindcast_inputs import (
    aggregate_historical_periods,
    source_fetch_bounds,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AggregationMethod
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.hindcast_inputs import (
    HistoricalAvailabilityPolicy,
    HistoricalSourcePeriod,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid

_START = ensure_utc(datetime(2026, 1, 1, 18, tzinfo=UTC))
_DAY = timedelta(days=1)
_HOUR = timedelta(hours=1)
_RECORDED = HistoricalAvailabilityPolicy.RECORDED_ONLY
_ASSUMED = HistoricalAvailabilityPolicy.RETROSPECTIVE_CLOSURE


def _source(
    step: timedelta = _HOUR, phase: timedelta = timedelta(0)
) -> HistoricalSourcePeriod:
    return HistoricalSourcePeriod(
        station_id=StationId(uuid4()),
        source="archive",
        parameter="discharge",
        evidence="test source definition",
        period=ForecastPeriodDefinition(
            grid=TimeGrid(step=step, phase=phase),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.MEAN,
        ),
    )


def _target(label: PeriodLabel = PeriodLabel.START) -> ForecastPeriodDefinition:
    return ForecastPeriodDefinition(
        grid=TimeGrid(step=_DAY, phase=18 * _HOUR),
        label=label,
        aggregation=AggregationMethod.MEAN,
    )


def _hourly() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [_START + i * _HOUR for i in range(24)],
            "value": [float(i) for i in range(24)],
        }
    )


class TestAggregateHistoricalPeriods:
    @pytest.mark.parametrize("label", [PeriodLabel.START, PeriodLabel.END])
    def test_complete_day_respects_physical_interval_and_label(
        self, label: PeriodLabel
    ) -> None:
        out = aggregate_historical_periods(
            _hourly(),
            source=_source(),
            target=_target(label),
            availability_policy=_ASSUMED,
        )
        assert out["value"].to_list() == [11.5]
        assert out["timestamp"].to_list() == [
            _START + (_DAY if label is PeriodLabel.END else timedelta(0))
        ]
        assert out["available_at_us"].to_list() == [
            int((_START + _DAY).timestamp() * 1_000_000)
        ]

    def test_missing_hour_does_not_become_partial_mean(self) -> None:
        out = aggregate_historical_periods(
            _hourly().slice(1),
            source=_source(),
            target=_target(),
            availability_policy=_ASSUMED,
        )
        assert out["value"].to_list() == [None]
        assert out["available_at_us"].to_list() == [None]

    def test_unknown_availability_is_not_assumed_by_default(self) -> None:
        out = aggregate_historical_periods(
            _hourly(), source=_source(), target=_target(), availability_policy=_RECORDED
        )
        assert out["value"].to_list() == [11.5]
        assert out["available_at_us"].to_list() == [None]

    def test_retrospective_policy_keeps_known_late_arrival(self) -> None:
        late = int((_START + 2 * _DAY).timestamp() * 1_000_000)
        frame = _hourly().with_columns(
            pl.Series("available_at_us", [late] + [None] * 23, dtype=pl.Int64)
        )
        out = aggregate_historical_periods(
            frame, source=_source(), target=_target(), availability_policy=_ASSUMED
        )
        assert out["available_at_us"].to_list() == [late]

    def test_approximate_day_retains_actual_1815_closure(self) -> None:
        shift = timedelta(minutes=15)
        source = replace(
            _source(_DAY, 18 * _HOUR + shift), input_support_displacement=shift
        )
        frame = pl.DataFrame({"timestamp": [_START + shift], "value": [12.0]})
        out = aggregate_historical_periods(
            frame,
            source=source,
            target=_target(),
            availability_policy=_ASSUMED,
            support_displacement=shift,
        )
        assert out["timestamp"].to_list() == [_START]
        available = out["available_at_us"][0]
        assert available > int(
            (_START + _DAY + timedelta(minutes=5)).timestamp() * 1_000_000
        )
        assert available == int((_START + _DAY + shift).timestamp() * 1_000_000)

    def test_duplicate_native_period_refuses(self) -> None:
        with pytest.raises(ConfigurationError, match="duplicate native"):
            aggregate_historical_periods(
                pl.concat([_hourly(), _hourly().head(1)]),
                source=_source(),
                target=_target(),
                availability_policy=_RECORDED,
            )

    def test_phase_is_not_inferred_from_rows(self) -> None:
        with pytest.raises(ConfigurationError, match="declared native grid"):
            aggregate_historical_periods(
                _hourly().with_columns(pl.col("timestamp") + timedelta(minutes=15)),
                source=_source(),
                target=_target(),
                availability_policy=_RECORDED,
            )

    def test_incompatible_daily_means_are_not_relabelled(self) -> None:
        with pytest.raises(ConfigurationError, match="do not nest"):
            aggregate_historical_periods(
                _hourly(),
                source=_source(_DAY),
                target=_target(),
                availability_policy=_RECORDED,
            )


class TestSourceFetchBounds:
    def test_end_label_includes_the_final_closing_sample(self) -> None:
        source = replace(
            _source(), period=replace(_source().period, label=PeriodLabel.END)
        )
        start, end = source_fetch_bounds(_START, ensure_utc(_START + _DAY), (source,))
        assert start <= _START + _HOUR
        assert end > _START + _DAY
