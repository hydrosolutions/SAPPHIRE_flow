from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.services.forecast_peaks import remaining_forecast_peaks
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    AggregationMethod,
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.ids import ForecastId, ModelId, StationId
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid

AT = ensure_utc(datetime(2026, 11, 26, 18, tzinfo=UTC))
STATION = StationId(UUID(int=1))
LEVELS = (0.01, 0.05, 0.25, 0.5, 0.75, 0.9, 0.99)


def forecast(
    values: list[list[float]],
    *,
    representation: EnsembleRepresentation = EnsembleRepresentation.QUANTILES,
    period: ForecastPeriodDefinition | None = None,
) -> OperationalForecast:
    offset = (
        timedelta(days=1)
        if period and period.label is PeriodLabel.END
        else timedelta(0)
    )
    key = (
        "quantile"
        if representation is EnsembleRepresentation.QUANTILES
        else "member_id"
    )
    coordinates = LEVELS if key == "quantile" else tuple(range(len(values[0])))
    data = pl.DataFrame(
        [
            {
                "valid_time": AT + offset + timedelta(days=day),
                key: coordinate,
                "value": float(value),
            }
            for day, row in enumerate(values)
            for coordinate, value in zip(coordinates, row, strict=True)
        ]
    )
    ensemble = ForecastEnsemble(
        representation=representation,
        values=data,
        station_id=STATION,
        issued_at=AT,
        parameter="discharge",
        units="m3/s",
        forecast_horizon_steps=len(values),
        time_step=timedelta(days=1),
        model_id=ModelId("any_model"),
        period_definition=period,
    )
    return OperationalForecast(
        id=ForecastId(UUID(int=2)),
        station_id=STATION,
        model_id=ModelId("any_model"),
        model_artifact_id=None,
        issued_at=AT,
        nwp_cycle_reference_time=None,
        nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
        representation=representation,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=AT,
        updated_at=AT,
    )


class TestRemainingPeaks:
    def test_finite_large_members_do_not_overflow_type7(self) -> None:
        item = forecast([[1e308] * 5], representation=EnsembleRepresentation.MEMBERS)
        median, upper = remaining_forecast_peaks(item, as_of=AT)
        assert median.discharge == 1e308
        assert upper.discharge == 1e308
        assert upper.reason is None

    @pytest.mark.parametrize("label", list(PeriodLabel))
    def test_daily_physical_support_controls_remaining_window(
        self, label: PeriodLabel
    ) -> None:
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=label,
            aggregation=AggregationMethod.MEAN,
        )
        item = forecast(
            [[1, 2, 3, 40, 50, 60, 70], [1, 2, 3, 4, 5, 6, 7]], period=period
        )
        partial = remaining_forecast_peaks(
            item, as_of=ensure_utc(AT + timedelta(hours=1))
        )[0]
        assert (partial.discharge, partial.support_start, partial.support_end) == (
            40,
            AT,
            AT + timedelta(days=1),
        )
        ended = remaining_forecast_peaks(
            item, as_of=ensure_utc(AT + timedelta(days=1))
        )[0]
        assert ended.discharge == 4
        assert ended.support_start == AT + timedelta(days=1)

    def test_unknown_support_excludes_old_label_without_inventing_daily_period(
        self,
    ) -> None:
        item = forecast([[1, 2, 3, 40, 50, 60, 70]])
        assert (
            remaining_forecast_peaks(item, as_of=ensure_utc(AT + timedelta(seconds=1)))[
                0
            ].reason
            == "no_remaining_support"
        )

    def test_independent_pointwise_quantile_maxima(self) -> None:
        item = forecast([[1, 2, 3, 40, 60, 90, 100], [1, 2, 3, 50, 60, 80, 100]])
        median, upper = remaining_forecast_peaks(item, as_of=AT)
        assert (median.discharge, median.valid_time) == (50, AT + timedelta(days=1))
        assert (upper.discharge, upper.valid_time) == (90, AT)
        assert upper.support_start is None

    def test_member_envelope_is_not_percentile_of_trajectory_maxima(self) -> None:
        item = forecast(
            [[0, 0, 0, 0, 100], [100, 0, 0, 0, 0]],
            representation=EnsembleRepresentation.MEMBERS,
        )
        median, upper = remaining_forecast_peaks(item, as_of=AT)
        assert median.discharge == 0
        assert upper.discharge == pytest.approx(60)  # trajectory-max P90 would be 100
        assert upper.valid_time == AT  # earliest tie
        assert upper.method == "member_type7"

    def test_missing_p90_at_any_time_does_not_underestimate_peak(self) -> None:
        item = forecast([[1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 60, 70]])
        item = replace(
            item,
            ensemble=replace(
                item.ensemble,
                values=item.ensemble.values.filter(
                    ~((pl.col("valid_time") > AT) & (pl.col("quantile") == 0.9))
                ),
            ),
        )
        median, upper = remaining_forecast_peaks(item, as_of=AT)
        assert median.discharge == 4
        assert upper.discharge is None
        assert upper.reason == "percentile_missing"

    @pytest.mark.parametrize("replacement", [0.9000000001, 0.89])
    def test_percentile_identity_is_exact(self, replacement: float) -> None:
        item = forecast([[1, 2, 3, 4, 5, 6, 7]])
        item = replace(
            item,
            ensemble=replace(
                item.ensemble,
                values=item.ensemble.values.with_columns(
                    pl.when(pl.col("quantile") == 0.9)
                    .then(replacement)
                    .otherwise(pl.col("quantile"))
                    .alias("quantile")
                ),
            ),
        )
        assert (
            remaining_forecast_peaks(item, as_of=AT)[1].reason == "percentile_missing"
        )

    @pytest.mark.parametrize(
        "row",
        [
            [1, 2, 3, 4, 5, 2, 7],
            [1, 2, 30, 4, 5, 6, 7],
            [1, 2, 3, 4, 5, float("nan"), 7],
            [-1, 2, 3, 4, 5, 6, 7],
        ],
    )
    def test_invalid_series_refuses_both_peaks(self, row: list[float]) -> None:
        result = remaining_forecast_peaks(forecast([row]), as_of=AT)
        assert all(p.reason == "invalid_series" and p.discharge is None for p in result)

    def test_small_member_ensemble_retains_only_median(self) -> None:
        result = remaining_forecast_peaks(
            forecast([[10, 20, 30]], representation=EnsembleRepresentation.MEMBERS),
            as_of=AT,
        )
        assert result[0].discharge == 20
        assert result[1].reason == "insufficient_members"

    @pytest.mark.parametrize(
        "status", [QcStatus.QC_FAILED, QcStatus.QC_SUSPECT, QcStatus.MISSING]
    )
    def test_bad_qc_is_not_a_numeric_comparison(self, status: QcStatus) -> None:
        item = replace(forecast([[1, 2, 3, 4, 5, 6, 7]]), qc_status=status)
        assert (
            remaining_forecast_peaks(item, as_of=AT)[0].reason == "forecast_qc_rejected"
        )
