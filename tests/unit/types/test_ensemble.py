from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.ids import StationId


def _sid() -> StationId:
    return StationId(uuid4())


def _issued() -> UtcDatetime:
    return ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _make_member_df(n_members: int = 3, n_steps: int = 5) -> pl.DataFrame:
    issued = _issued()
    rows = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(issued.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for m in range(n_members):
            rows.append({"valid_time": vt, "member_id": m, "value": float(m + step)})
    return pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )


def _make_quantile_df(
    quantiles: list[float] | None = None, n_steps: int = 5
) -> pl.DataFrame:
    issued = _issued()
    qs = quantiles or [0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.98]
    rows = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(issued.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for q in qs:
            rows.append({"valid_time": vt, "quantile": q, "value": q * 100.0})
    return pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC"))
    )


class TestForecastEnsembleFromMembers:
    def test_valid(self) -> None:
        df = _make_member_df()
        ens = ForecastEnsemble.from_members(
            station_id=_sid(),
            issued_at=_issued(),
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(hours=1),
            values=df,
        )
        assert ens.representation == EnsembleRepresentation.MEMBERS
        assert ens.forecast_horizon_steps == 5

    def test_missing_member_id(self) -> None:
        df = _make_member_df().drop("member_id")
        with pytest.raises(ValueError, match="member_id"):
            ForecastEnsemble.from_members(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_has_quantile_column(self) -> None:
        df = _make_member_df().with_columns(pl.lit(0.5).alias("quantile"))
        with pytest.raises(ValueError, match="quantile"):
            ForecastEnsemble.from_members(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_missing_valid_time(self) -> None:
        df = _make_member_df().drop("valid_time")
        with pytest.raises(ValueError, match="valid_time"):
            ForecastEnsemble.from_members(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_missing_value(self) -> None:
        df = _make_member_df().drop("value")
        with pytest.raises(ValueError, match="value"):
            ForecastEnsemble.from_members(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_empty_df(self) -> None:
        df = pl.DataFrame({"valid_time": [], "member_id": [], "value": []}).cast(
            {
                "valid_time": pl.Datetime("us", "UTC"),
                "member_id": pl.Int32,
                "value": pl.Float64,
            }
        )
        with pytest.raises(ValueError, match="empty"):
            ForecastEnsemble.from_members(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )


class TestForecastEnsembleFromQuantiles:
    def test_valid(self) -> None:
        df = _make_quantile_df()
        ens = ForecastEnsemble.from_quantiles(
            station_id=_sid(),
            issued_at=_issued(),
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(hours=1),
            values=df,
        )
        assert ens.representation == EnsembleRepresentation.QUANTILES
        assert ens.forecast_horizon_steps == 5

    def test_missing_quantile(self) -> None:
        df = _make_quantile_df().drop("quantile")
        with pytest.raises(ValueError, match="quantile"):
            ForecastEnsemble.from_quantiles(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_has_member_id(self) -> None:
        df = _make_quantile_df().with_columns(pl.lit(0).alias("member_id"))
        with pytest.raises(ValueError, match="member_id"):
            ForecastEnsemble.from_quantiles(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_too_few_quantiles(self) -> None:
        df = _make_quantile_df(quantiles=[0.1, 0.5, 0.9])
        with pytest.raises(ValueError, match="at least 7"):
            ForecastEnsemble.from_quantiles(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_no_tail_coverage_low(self) -> None:
        df = _make_quantile_df(quantiles=[0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.95])
        with pytest.raises(ValueError, match="<= 0.05"):
            ForecastEnsemble.from_quantiles(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )

    def test_no_tail_coverage_high(self) -> None:
        df = _make_quantile_df(quantiles=[0.02, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60])
        with pytest.raises(ValueError, match=">= 0.95"):
            ForecastEnsemble.from_quantiles(
                station_id=_sid(),
                issued_at=_issued(),
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(hours=1),
                values=df,
            )
