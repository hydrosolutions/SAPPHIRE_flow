# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import hindcast_forecasts, hindcast_values
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager as ContextManager

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)


class PgHindcastStore:
    def __init__(
        self,
        conn: sa.Connection,
        *,
        transaction_factory: Callable[[], ContextManager[sa.Connection]] | None = None,
    ) -> None:
        self._conn = conn
        self._begin = (
            transaction_factory
            if transaction_factory is not None
            else conn.engine.begin
        )

    def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
        ens = hindcast.ensemble
        df = ens.values
        if df.is_empty():
            raise ValueError("cannot store hindcast with empty ensemble")

        is_members = ens.representation == EnsembleRepresentation.MEMBERS
        hindcast_step_dt = hindcast.hindcast_step
        qc_flags_json = [
            {
                "rule_id": f.rule_id,
                "rule_version": f.rule_version,
                "status": f.status.value,
                "detail": f.detail,
            }
            for f in hindcast.qc_flags
        ]

        with self._begin() as txn:
            header_id = txn.execute(
                pg_insert(hindcast_forecasts)
                .values(
                    id=hindcast.id,
                    station_id=hindcast.station_id,
                    model_id=hindcast.model_id,
                    model_artifact_id=hindcast.model_artifact_id,
                    hindcast_step=hindcast.hindcast_step,
                    forcing_type=hindcast.forcing_type.value,
                    representation=hindcast.representation.value,
                    hindcast_run_id=hindcast.hindcast_run_id,
                    parameter=hindcast.ensemble.parameter,
                    units=hindcast.ensemble.units,
                    created_at=hindcast.created_at,
                    qc_status=hindcast.qc_status.value,
                    qc_flags=qc_flags_json,
                )
                .on_conflict_do_update(
                    index_elements=[
                        "station_id",
                        "model_id",
                        "hindcast_step",
                        "parameter",
                        "hindcast_run_id",
                        "forcing_type",
                    ],
                    set_={
                        "model_artifact_id": hindcast.model_artifact_id,
                        "units": hindcast.ensemble.units,
                        "representation": hindcast.representation.value,
                        "created_at": hindcast.created_at,
                        "qc_status": hindcast.qc_status.value,
                        "qc_flags": qc_flags_json,
                    },
                )
                .returning(hindcast_forecasts.c.id)
            ).scalar_one()

            # Full-replace the value payload keyed to the row actually in the DB.
            # On a clean insert the DELETE is a harmless no-op.
            txn.execute(
                sa.delete(hindcast_values).where(
                    hindcast_values.c.hindcast_forecast_id == header_id
                )
            )

            # Build rows inside the txn, keyed to header_id (NOT hindcast.id).
            rows = [
                {
                    "id": uuid4(),
                    "hindcast_forecast_id": header_id,
                    "hindcast_step": hindcast_step_dt,
                    "valid_time": row["valid_time"],
                    "lead_time_hours": int(
                        (row["valid_time"] - hindcast_step_dt).total_seconds() // 3600
                    ),
                    "member_id": row["member_id"] if is_members else None,
                    "quantile": None if is_members else row["quantile"],
                    "value": row["value"],
                }
                for row in df.to_dicts()
            ]
            if rows:
                txn.execute(sa.insert(hindcast_values), rows)

        return header_id

    def fetch_hindcasts(
        self,
        station_id: StationId,
        model_id: ModelId,
        start: UtcDatetime,
        end: UtcDatetime,
        forcing_type: ForcingType | None = None,
        hindcast_run_id: UUID | None = None,
        parameter: str | None = None,
    ) -> list[HindcastForecast]:
        q = sa.select(hindcast_forecasts).where(
            sa.and_(
                hindcast_forecasts.c.station_id == station_id,
                hindcast_forecasts.c.model_id == model_id,
                hindcast_forecasts.c.hindcast_step >= start,
                hindcast_forecasts.c.hindcast_step < end,
            )
        )
        if forcing_type is not None:
            q = q.where(hindcast_forecasts.c.forcing_type == forcing_type.value)
        if hindcast_run_id is not None:
            q = q.where(hindcast_forecasts.c.hindcast_run_id == hindcast_run_id)
        if parameter is not None:
            q = q.where(hindcast_forecasts.c.parameter == parameter)

        header_rows = self._conn.execute(q).mappings().all()
        if not header_rows:
            return []

        forecast_ids = [row["id"] for row in header_rows]
        vq = sa.select(hindcast_values).where(
            hindcast_values.c.hindcast_forecast_id.in_(forecast_ids)
        )
        value_rows = self._conn.execute(vq).mappings().all()

        values_by_id: dict[UUID, list[dict]] = {}
        for row in value_rows:
            fid = row["hindcast_forecast_id"]
            values_by_id.setdefault(fid, []).append(dict(row))

        result = []
        for header in header_rows:
            fid = header["id"]
            rows_for_id = values_by_id.get(fid, [])
            if not rows_for_id:
                log.warning(
                    "hindcast.orphan_header_skipped",
                    hindcast_forecast_id=fid,
                    station_id=station_id,
                )
                continue
            ensemble = _reconstruct_ensemble(header, rows_for_id, station_id)
            result.append(
                HindcastForecast(
                    id=HindcastForecastId(fid),
                    station_id=StationId(header["station_id"]),
                    model_id=ModelId(header["model_id"]),
                    model_artifact_id=ArtifactId(header["model_artifact_id"]),
                    hindcast_step=utc_from_row(header["hindcast_step"]),
                    forcing_type=ForcingType(header["forcing_type"]),
                    representation=EnsembleRepresentation(header["representation"]),
                    hindcast_run_id=UUID(str(header["hindcast_run_id"])),
                    ensemble=ensemble,
                    created_at=utc_from_row(header["created_at"]),
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
                )
            )
        return result

    def fetch_hindcasts_by_station(
        self,
        station_id: StationId,
        parameter: str,
        period_start: UtcDatetime,
        period_end: UtcDatetime,
    ) -> dict[ModelId, list[HindcastForecast]]:
        q = sa.select(hindcast_forecasts).where(
            sa.and_(
                hindcast_forecasts.c.station_id == station_id,
                hindcast_forecasts.c.parameter == parameter,
                hindcast_forecasts.c.hindcast_step >= period_start,
                hindcast_forecasts.c.hindcast_step < period_end,
            )
        )
        header_rows = self._conn.execute(q).mappings().all()
        if not header_rows:
            return {}

        forecast_ids = [row["id"] for row in header_rows]
        vq = sa.select(hindcast_values).where(
            hindcast_values.c.hindcast_forecast_id.in_(forecast_ids)
        )
        value_rows = self._conn.execute(vq).mappings().all()

        values_by_id: dict[UUID, list[dict]] = {}
        for row in value_rows:
            fid = row["hindcast_forecast_id"]
            values_by_id.setdefault(fid, []).append(dict(row))

        result: dict[ModelId, list[HindcastForecast]] = {}
        for header in header_rows:
            fid = header["id"]
            rows_for_id = values_by_id.get(fid, [])
            if not rows_for_id:
                log.warning(
                    "hindcast.orphan_header_skipped",
                    hindcast_forecast_id=fid,
                    station_id=station_id,
                )
                continue
            ensemble = _reconstruct_ensemble(header, rows_for_id, station_id)
            hindcast = HindcastForecast(
                id=HindcastForecastId(fid),
                station_id=StationId(header["station_id"]),
                model_id=ModelId(header["model_id"]),
                model_artifact_id=ArtifactId(header["model_artifact_id"]),
                hindcast_step=utc_from_row(header["hindcast_step"]),
                forcing_type=ForcingType(header["forcing_type"]),
                representation=EnsembleRepresentation(header["representation"]),
                hindcast_run_id=UUID(str(header["hindcast_run_id"])),
                ensemble=ensemble,
                created_at=utc_from_row(header["created_at"]),
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
            )
            model_id = ModelId(header["model_id"])
            result.setdefault(model_id, []).append(hindcast)
        return result


def _reconstruct_ensemble(
    header: sa.engine.row.RowMapping,
    rows: list[dict],
    station_id: StationId,
) -> ForecastEnsemble:
    representation = EnsembleRepresentation(header["representation"])
    issued_at = utc_from_row(header["hindcast_step"])
    parameter = header["parameter"]
    units = header["units"]

    if not rows:
        raise ValueError(
            f"No hindcast_values rows for hindcast_forecast_id={header['id']}"
        )

    df = pl.DataFrame(
        {
            "valid_time": [
                datetime.fromtimestamp(
                    r["valid_time"].timestamp()
                    if hasattr(r["valid_time"], "timestamp")
                    else r["valid_time"].replace(tzinfo=UTC).timestamp(),
                    tz=UTC,
                )
                for r in rows
            ],
            **(
                {"member_id": [r["member_id"] for r in rows]}
                if representation == EnsembleRepresentation.MEMBERS
                else {"quantile": [r["quantile"] for r in rows]}
            ),
            "value": [r["value"] for r in rows],
        }
    ).with_columns(pl.col("valid_time").cast(pl.Datetime("us", "UTC")))

    if representation == EnsembleRepresentation.MEMBERS:
        df = df.with_columns(pl.col("member_id").cast(pl.Int32))

    sorted_times = sorted({r["valid_time"] for r in rows})
    if len(sorted_times) >= 2:
        delta = sorted_times[1] - sorted_times[0]
        time_step = timedelta(seconds=delta.total_seconds())
    else:
        time_step = timedelta(hours=1)

    if representation == EnsembleRepresentation.MEMBERS:
        return ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
    return ForecastEnsemble.from_quantiles(
        station_id=station_id,
        issued_at=issued_at,
        parameter=parameter,
        units=units,
        time_step=time_step,
        values=df,
    )
