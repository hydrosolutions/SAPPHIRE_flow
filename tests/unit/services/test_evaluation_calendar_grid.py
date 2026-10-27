import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.daily_output_integrity import assess_daily_output
from sapphire_flow.store.daily_forecast_documents import contracts_json, parse_contracts
from sapphire_flow.types.daily_output import (
    DailyGridAnchor,
    DailyOutputContract,
    DailyOutputEligibility,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ModelId, StationId


def contract() -> DailyOutputContract:
    return DailyOutputContract(
        parameter="discharge",
        units="m³/s",
        representation=EnsembleRepresentation.MEMBERS,
        time_step=timedelta(days=1),
        first_lead=timedelta(0),
        horizon_steps=2,
        series_keys=(0.0, 1.0),
        grid_anchor=DailyGridAnchor.NEXT_UTC_MIDNIGHT,
    )


class TestDeclaredCalendarGrid:
    @pytest.mark.parametrize("hour", [0, 6, 12, 18])
    def test_calendar_grid_uses_issue_identity_not_observed_lead(
        self, hour: int
    ) -> None:
        issue = ensure_utc(datetime(2026, 10, 26, hour, tzinfo=UTC))
        anchor = ensure_utc(datetime(2026, 10, 26 if hour == 0 else 27, tzinfo=UTC))
        key = ForecastOutputKey(
            station_id=StationId(uuid4()),
            model_id=ModelId("nwp_regression"),
            issued_at=issue,
            parameter="discharge",
        )
        frame = pl.DataFrame(
            [
                {
                    "valid_time": anchor + step * timedelta(days=1),
                    "member_id": member,
                    "value": 10.0,
                }
                for step in range(2)
                for member in range(2)
            ]
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=key.station_id,
            model_id=key.model_id,
            issued_at=issue,
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(days=1),
            values=frame,
        )
        assert (
            assess_daily_output(ensemble, key, contract(), QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.USABLE
        )
        shifted = replace(
            ensemble,
            values=frame.with_columns(pl.col("valid_time") + timedelta(hours=1)),
        )
        assert (
            assess_daily_output(shifted, key, contract(), QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.INTEGRITY_REJECTED
        )

    def test_anchor_roundtrips_and_legacy_contract_stays_issue_relative(self) -> None:
        raw = json.loads(contracts_json((contract(),)))
        assert parse_contracts(raw) == (contract(),)
        del raw[0]["grid_anchor"]
        assert parse_contracts(raw)[0].grid_anchor is DailyGridAnchor.ISSUE_TIME

    def test_calendar_anchor_cannot_relax_hourly_cadence(self) -> None:
        with pytest.raises(ValueError, match="requires daily cadence"):
            replace(contract(), time_step=timedelta(hours=1))
