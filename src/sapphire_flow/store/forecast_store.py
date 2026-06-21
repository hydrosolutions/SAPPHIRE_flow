# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import sqlalchemy as sa

from sapphire_flow.db.metadata import forecast_values, forecasts
from sapphire_flow.exceptions import ConflictError
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
    QcStatus,
    WarmUpSource,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_summary import ForecastSummaryRow
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import RowMapping

    from sapphire_flow.types.datetime import UtcDatetime


class PgForecastStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        self._conn.execute(
            sa.insert(forecasts).values(
                id=forecast.id,
                station_id=forecast.station_id,
                model_id=forecast.model_id,
                model_artifact_id=forecast.model_artifact_id,
                issued_at=forecast.issued_at,
                nwp_cycle_reference_time=forecast.nwp_cycle_reference_time,
                nwp_cycle_source=forecast.nwp_cycle_source.value,
                representation=forecast.representation.value,
                status=forecast.status.value,
                version=forecast.version,
                warm_up_source=(
                    forecast.warm_up_source.value
                    if forecast.warm_up_source is not None
                    else None
                ),
                warm_up_state_age_hours=forecast.warm_up_state_age_hours,
                observation_staleness_hours=forecast.observation_staleness_hours,
                parameter=forecast.ensemble.parameter,
                units=forecast.ensemble.units,
                created_at=forecast.created_at,
                updated_at=forecast.updated_at,
                qc_status=forecast.qc_status.value,
                qc_flags=[
                    {
                        "rule_id": f.rule_id,
                        "rule_version": f.rule_version,
                        "status": f.status.value,
                        "detail": f.detail,
                    }
                    for f in forecast.qc_flags
                ],
                combination_strategy=forecast.combination_strategy,
                source_model_ids=(
                    [str(mid) for mid in forecast.source_model_ids]
                    if forecast.source_model_ids is not None
                    else None
                ),
            )
        )
        rows = _build_value_rows(forecast)
        if rows:
            self._conn.execute(sa.insert(forecast_values), rows)
        return forecast.id

    def fetch_forecast(self, forecast_id: ForecastId) -> OperationalForecast | None:
        rows = (
            self._conn.execute(
                sa.select(forecasts, forecast_values)
                .join(
                    forecast_values,
                    forecast_values.c.forecast_id == forecasts.c.id,
                )
                .where(forecasts.c.id == forecast_id)
                .order_by(forecast_values.c.valid_time)
            )
            .mappings()
            .all()
        )
        if not rows:
            return None
        return _rows_to_domain(rows)

    def fetch_latest_forecast(
        self,
        station_id: StationId,
        model_id: ModelId | None = None,
        parameter: str | None = None,
    ) -> OperationalForecast | None:
        sub = sa.select(forecasts.c.id).where(forecasts.c.station_id == station_id)
        if model_id is not None:
            sub = sub.where(forecasts.c.model_id == model_id)
        if parameter is not None:
            sub = sub.where(forecasts.c.parameter == parameter)
        sub = sub.order_by(forecasts.c.issued_at.desc()).limit(1).scalar_subquery()
        fid_row = self._conn.execute(sa.select(sub)).scalar_one_or_none()
        if fid_row is None:
            return None
        return self.fetch_forecast(ForecastId(fid_row))

    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        stmt = sa.select(forecasts.c.id).where(forecasts.c.issued_at == issued_at)
        if station_id is not None:
            stmt = stmt.where(forecasts.c.station_id == station_id)
        if parameter is not None:
            stmt = stmt.where(forecasts.c.parameter == parameter)
        fids = [ForecastId(r[0]) for r in self._conn.execute(stmt).fetchall()]
        return self._fetch_by_ids(fids)

    def transition_status(
        self,
        forecast_id: ForecastId,
        expected_version: int,
        new_status: ForecastStatus,
    ) -> int:
        result = self._conn.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == forecast_id)
            .where(forecasts.c.version == expected_version)
            .values(
                status=new_status.value,
                version=expected_version + 1,
                updated_at=sa.func.now(),
            )
        )
        if result.rowcount == 0:
            raise ConflictError(f"Version mismatch for forecast {forecast_id}")
        return expected_version + 1

    def fetch_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        status: ForecastStatus | None = None,
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        stmt = (
            sa.select(forecasts.c.id)
            .where(forecasts.c.station_id == station_id)
            .where(forecasts.c.issued_at >= start)
            .where(forecasts.c.issued_at < end)
        )
        if model_id is not None:
            stmt = stmt.where(forecasts.c.model_id == model_id)
        if status is not None:
            stmt = stmt.where(forecasts.c.status == status.value)
        if parameter is not None:
            stmt = stmt.where(forecasts.c.parameter == parameter)
        fids = [ForecastId(r[0]) for r in self._conn.execute(stmt).fetchall()]
        return self._fetch_by_ids(fids)

    def fetch_forecast_summaries(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        model_id: ModelId | None = None,
        parameter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ForecastSummaryRow], int]:
        filters = [
            forecasts.c.station_id == station_id,
            forecasts.c.issued_at >= start,
            forecasts.c.issued_at < end,
        ]
        if model_id is not None:
            filters.append(forecasts.c.model_id == model_id)
        if parameter is not None:
            filters.append(forecasts.c.parameter == parameter)

        where = sa.and_(*filters)

        total: int = self._conn.execute(
            sa.select(sa.func.count()).select_from(forecasts).where(where)
        ).scalar_one()

        rows = (
            self._conn.execute(
                sa.select(forecasts)
                .where(where)
                .order_by(forecasts.c.issued_at.desc(), forecasts.c.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )

        return [_row_to_summary(row) for row in rows], total

    def _fetch_by_ids(self, fids: list[ForecastId]) -> list[OperationalForecast]:
        if not fids:
            return []
        rows = (
            self._conn.execute(
                sa.select(forecasts, forecast_values)
                .join(
                    forecast_values,
                    forecast_values.c.forecast_id == forecasts.c.id,
                )
                .where(forecasts.c.id.in_(fids))
                .order_by(forecasts.c.issued_at, forecast_values.c.valid_time)
            )
            .mappings()
            .all()
        )
        grouped: dict[ForecastId, list] = defaultdict(list)
        for row in rows:
            grouped[ForecastId(row["id"])].append(row)
        return [_rows_to_domain(group) for group in grouped.values()]


def _build_value_rows(forecast: OperationalForecast) -> list[dict]:  # type: ignore[type-arg]
    df = forecast.ensemble.values
    issued_at = forecast.issued_at
    is_members = forecast.representation == EnsembleRepresentation.MEMBERS
    rows = []
    for row in df.iter_rows(named=True):
        vt = row["valid_time"]
        lead = int((vt.timestamp() - issued_at.timestamp()) // 3600)
        rows.append(
            {
                "id": uuid4(),
                "forecast_id": forecast.id,
                "issued_at": issued_at,
                "valid_time": vt,
                "lead_time_hours": lead,
                "member_id": row["member_id"] if is_members else None,
                "quantile": None if is_members else row["quantile"],
                "value": row["value"],
            }
        )
    return rows


def _rows_to_domain(rows: Sequence[RowMapping]) -> OperationalForecast:
    header = rows[0]
    representation = EnsembleRepresentation(header["representation"])
    is_members = representation == EnsembleRepresentation.MEMBERS

    if is_members:
        value_rows = [
            {
                "valid_time": row["valid_time"],
                "member_id": row["member_id"],
                "value": row["value"],
            }
            for row in rows
        ]
        df = pl.DataFrame(value_rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
    else:
        value_rows = [
            {
                "valid_time": row["valid_time"],
                "quantile": row["quantile"],
                "value": row["value"],
            }
            for row in rows
        ]
        df = pl.DataFrame(value_rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        )

    valid_times = df["valid_time"].sort().unique().sort()
    time_step = (
        timedelta(seconds=int(valid_times[1].timestamp() - valid_times[0].timestamp()))
        if len(valid_times) >= 2
        else timedelta(hours=1)
    )

    station_id = StationId(header["station_id"])
    issued_at = utc_from_row(header["issued_at"])
    parameter = header["parameter"]
    units = header["units"]

    ensemble = (
        ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
        if is_members
        else ForecastEnsemble.from_quantiles(
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
    )

    warm_up_raw = header["warm_up_source"]
    return OperationalForecast(
        id=ForecastId(header["id"]),
        station_id=station_id,
        model_id=ModelId(header["model_id"]),
        model_artifact_id=(
            ArtifactId(header["model_artifact_id"])
            if header["model_artifact_id"] is not None
            else None
        ),
        issued_at=issued_at,
        nwp_cycle_reference_time=utc_from_row(header["nwp_cycle_reference_time"]),
        nwp_cycle_source=NwpCycleSource(header["nwp_cycle_source"]),
        representation=representation,
        status=ForecastStatus(header["status"]),
        version=header["version"],
        warm_up_source=WarmUpSource(warm_up_raw) if warm_up_raw is not None else None,
        warm_up_state_age_hours=header["warm_up_state_age_hours"],
        observation_staleness_hours=header["observation_staleness_hours"],
        ensemble=ensemble,
        created_at=utc_from_row(header["created_at"]),
        updated_at=utc_from_row(header["updated_at"]),
        qc_status=QcStatus(header["qc_status"]),
        qc_flags=tuple(
            QcFlag(
                rule_id=f["rule_id"],
                rule_version=f["rule_version"],
                status=QcStatus(f["status"]),
                detail=f.get("detail"),
            )
            for f in (header["qc_flags"] or [])
        ),
        combination_strategy=header.get("combination_strategy"),
        source_model_ids=(
            [ModelId(mid) for mid in header["source_model_ids"]]
            if header.get("source_model_ids") is not None
            else None
        ),
    )


def _row_to_summary(row: sa.engine.row.RowMapping) -> ForecastSummaryRow:
    return ForecastSummaryRow(
        id=ForecastId(row["id"]),
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        issued_at=utc_from_row(row["issued_at"]),
        parameter=row["parameter"],
        representation=EnsembleRepresentation(row["representation"]),
        status=ForecastStatus(row["status"]),
        qc_status=QcStatus(row["qc_status"]),
        nwp_cycle_source=NwpCycleSource(row["nwp_cycle_source"]),
        created_at=utc_from_row(row["created_at"]),
    )
