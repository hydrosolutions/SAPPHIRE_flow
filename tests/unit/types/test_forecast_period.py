from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest
from pydantic import ValidationError

from sapphire_flow.store.forecast_period_codec import parse_forecast_period
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import AggregationMethod
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid


@pytest.mark.parametrize("label", [PeriodLabel.START, PeriodLabel.END])
def test_daily_period_keeps_real_issue_and_physical_labels(label: PeriodLabel) -> None:
    issue = ensure_utc(datetime(2026, 11, 24, tzinfo=UTC))
    grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
    period = ForecastPeriodDefinition(
        grid=grid, label=label, aggregation=AggregationMethod.MEAN
    )
    first = grid.floor(issue) + (
        grid.step if label is PeriodLabel.END else timedelta(0)
    )
    values = pl.DataFrame(
        {
            "valid_time": [first, first + grid.step],
            "member_id": [0, 0],
            "value": [10.0, 11.0],
        }
    )
    ensemble = ForecastEnsemble.from_members(
        station_id=StationId(uuid4()),
        issued_at=issue,
        parameter="discharge",
        units="m³/s",
        time_step=grid.step,
        values=values,
        period_definition=period,
    )
    assert ensemble.issued_at == issue
    assert ensemble.values["valid_time"][0] == first
    assert parse_forecast_period(period.content) == period


@pytest.mark.parametrize("shift", [timedelta(minutes=15), timedelta(days=1)])
def test_shifted_period_labels_refused(shift: timedelta) -> None:
    issue = ensure_utc(datetime(2026, 11, 24, tzinfo=UTC))
    grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
    period = ForecastPeriodDefinition(
        grid=grid, label=PeriodLabel.START, aggregation=AggregationMethod.MEAN
    )
    values = pl.DataFrame(
        {"valid_time": [grid.floor(issue) + shift], "member_id": [0], "value": [10.0]}
    )
    with pytest.raises(ValueError, match="declared period grid"):
        ForecastEnsemble.from_members(
            station_id=StationId(uuid4()),
            issued_at=issue,
            parameter="discharge",
            units="m³/s",
            time_step=grid.step,
            values=values,
            period_definition=period,
        )


def test_legacy_period_remains_unknown() -> None:
    assert parse_forecast_period(None) is None


@pytest.mark.parametrize(
    "change",
    [
        {"label": "unknown"},
        {"origin_seconds": 86400},
        {"aggregation": "point"},
        {"step_seconds": "86400"},
    ],
)
def test_invalid_stored_period_refused(change: dict[str, object]) -> None:
    payload = {
        "step_seconds": 86400,
        "origin_seconds": 64800,
        "label": "start",
        "aggregation": "mean",
        **change,
    }
    with pytest.raises((ValueError, ValidationError)):
        parse_forecast_period(payload)
