from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

from sapphire_flow.api.forecast_probabilities import forecast_probabilities
from sapphire_flow.services.threshold_probability import threshold_probability
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import (
    AggregationMethod,
    EnsembleRepresentation,
    QcStatus,
    ThresholdSource,
)
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid
from tests.conftest import make_station_config
from tests.unit.api.test_api_forecasts import _make_operational_forecast

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from sapphire_flow.types.forecast import OperationalForecast


def _forecast(
    representation: EnsembleRepresentation = EnsembleRepresentation.QUANTILES,
) -> OperationalForecast:
    record = _make_operational_forecast(station_id=make_station_config().id)
    label = (
        "quantile"
        if representation is EnsembleRepresentation.QUANTILES
        else "member_id"
    )
    labels = (
        [0.05, 0.5, 0.95]
        if representation is EnsembleRepresentation.QUANTILES
        else [0, 1, 2]
    )
    frame = pl.DataFrame(
        {
            "valid_time": [record.issued_at + timedelta(days=1)] * 3,
            label: labels,
            "value": [0.0, 5.0, 10.0],
        }
    )
    ensemble = replace(
        record.ensemble,
        representation=representation,
        values=frame,
        time_step=timedelta(days=1),
        forecast_horizon_steps=1,
    )
    return replace(
        record,
        ensemble=ensemble,
        representation=representation,
        qc_status=QcStatus.QC_PASSED,
    )


def _threshold(record: OperationalForecast, value: float = 5.0) -> StationThreshold:
    return StationThreshold(
        station_id=record.station_id,
        danger_level="High",
        parameter="discharge",
        value=value,
        source=ThresholdSource.AUTHORITY,
        created_at=record.issued_at,
        updated_at=record.issued_at,
    )


@pytest.mark.parametrize("threshold", [-1.0, 0.0, 5.0, 10.0, 20.0])
def test_quantile_probabilities_reuse_review_rules(threshold: float) -> None:
    record = _forecast()
    result = forecast_probabilities(record, [_threshold(record, threshold)])
    actual = next(iter(result.points[0].threshold_probabilities.values()))
    expected = threshold_probability(
        record.representation, [(0.05, 0.0), (0.5, 5.0), (0.95, 10.0)], threshold
    )
    assert actual.probability == expected.probability
    assert actual.lower == expected.lower
    assert actual.upper == expected.upper
    assert result.window_event_probability == "unsupported"
    assert result.temporal_support == "unknown"
    assert result.points[0].support_start is None


def test_members_use_strict_exceedance_and_single_member_is_unavailable() -> None:
    record = _forecast(EnsembleRepresentation.MEMBERS)
    result = forecast_probabilities(record, [_threshold(record)])
    probability = next(iter(result.points[0].threshold_probabilities.values()))
    assert probability.probability == pytest.approx(1 / 3)
    assert probability.method.value == "empirical_members"
    single = replace(
        record, ensemble=replace(record.ensemble, values=record.ensemble.values.head(1))
    )
    result = forecast_probabilities(single, [_threshold(single)])
    probability = next(iter(result.points[0].threshold_probabilities.values()))
    assert probability.probability is None
    assert probability.reason == "deterministic_forecast"


@pytest.mark.parametrize("label", [PeriodLabel.START, PeriodLabel.END])
def test_declared_nepal_daily_period_preserves_support(label: PeriodLabel) -> None:
    record = _forecast()
    period = ForecastPeriodDefinition(
        grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
        label=label,
        aggregation=AggregationMethod.MEAN,
    )
    start = period.grid.floor(record.issued_at)
    valid = start if label is PeriodLabel.START else start + timedelta(days=1)
    ensemble = replace(
        record.ensemble,
        values=record.ensemble.values.with_columns(pl.lit(valid).alias("valid_time")),
        period_definition=period,
    )
    record = replace(record, ensemble=ensemble)
    result = forecast_probabilities(record, [_threshold(record)])
    assert result.points[0].support_start == start
    assert result.points[0].support_end == start + timedelta(days=1)
    assert result.period_definition.aggregation == "mean"
    assert result.temporal_support == "declared_period"


@pytest.mark.parametrize("qc", [QcStatus.QC_FAILED, QcStatus.QC_SUSPECT])
def test_rejected_quality_never_yields_numeric_probability(qc: QcStatus) -> None:
    record = replace(_forecast(), qc_status=qc)
    result = forecast_probabilities(record, [_threshold(record)])
    assert result.availability == "unavailable"
    assert (
        next(iter(result.points[0].threshold_probabilities.values())).probability
        is None
    )


def test_no_thresholds_or_mismatched_parameter_never_invents_levels() -> None:
    record = _forecast()
    level = replace(_threshold(record), parameter="water_level")
    result = forecast_probabilities(record, [level])
    assert result.availability == "thresholds_not_configured"
    assert result.thresholds == []
    wrong_units = replace(record, ensemble=replace(record.ensemble, units="cfs"))
    result = forecast_probabilities(wrong_units, [_threshold(record)])
    assert result.reason == "forecast_units_not_canonical_discharge"
    assert (
        next(iter(result.points[0].threshold_probabilities.values())).probability
        is None
    )


def test_forecast_detail_serves_identity_and_current_threshold_snapshot(
    client: TestClient, fake_stores: dict[str, Any]
) -> None:
    record = _forecast()
    fake_stores["forecast_store"].store_forecast(record)
    threshold = _threshold(record)
    inferred = replace(
        threshold, danger_level="Extreme", source=ThresholdSource.INFERRED, value=6.0
    )
    fake_stores["station_store"].store_thresholds([threshold, inferred])
    response = client.get(f"/api/v1/forecasts/{record.id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == str(record.id)
    assert payload["model_id"] == record.model_id
    probabilities = payload["threshold_probabilities"]
    assert probabilities["kind"] == "operational"
    assert probabilities["availability"] == "available"
    assert {t["source"] for t in probabilities["thresholds"]} == {
        "authority",
        "inferred",
    }
    assert len(probabilities["points"][0]["threshold_probabilities"]) == 2
    assert response.headers["cache-control"] == "no-store"


def test_out_of_scope_does_not_read_thresholds(
    client: TestClient, fake_stores: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sapphire_flow.api import app
    from sapphire_flow.api.security import require_principal
    from sapphire_flow.types.enums import AccessTokenRole
    from tests.unit.api.conftest import ADMIN_PRINCIPAL

    record = _forecast()
    fake_stores["forecast_store"].store_forecast(record)
    app.dependency_overrides[require_principal] = lambda: replace(
        ADMIN_PRINCIPAL, role=AccessTokenRole.REVIEWER, station_ids=frozenset()
    )

    def fail_read(*args: object) -> None:
        raise AssertionError("Out-of-scope forecast must not read thresholds")

    monkeypatch.setattr(fake_stores["station_store"], "fetch_thresholds", fail_read)
    response = client.get(f"/api/v1/forecasts/{record.id}")
    assert response.status_code == 404
    assert "threshold_probabilities" not in response.json()


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_invalid_threshold_keeps_forecast_detail_available(bad_value: float) -> None:
    record = _forecast()
    result = forecast_probabilities(record, [_threshold(record, bad_value)])
    assert result.availability == "unavailable"
    assert result.reason == "nonfinite_threshold"
    assert "NaN" not in result.model_dump_json()


def test_duplicate_thresholds_are_explicitly_unavailable() -> None:
    record = _forecast()
    threshold = _threshold(record)
    result = forecast_probabilities(record, [threshold, threshold])
    assert result.reason == "duplicate_threshold_keys"


def test_string_member_ids_preserve_empirical_probabilities() -> None:
    record = _forecast(EnsembleRepresentation.MEMBERS)
    record = replace(
        record,
        ensemble=replace(
            record.ensemble,
            values=record.ensemble.values.with_columns(
                pl.Series("member_id", ["a", "b", "c"])
            ),
        ),
    )
    result = forecast_probabilities(record, [_threshold(record)])
    assert next(
        iter(result.points[0].threshold_probabilities.values())
    ).probability == pytest.approx(1 / 3)
