# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import hindcast_forecasts, hindcast_values
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgHindcastStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
        self._conn.execute(
            pg_insert(hindcast_forecasts).values(
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
            )
        )

        ens = hindcast.ensemble
        df = ens.values
        is_members = ens.representation == EnsembleRepresentation.MEMBERS
        hindcast_step_dt = hindcast.hindcast_step

        rows = [
            {
                "id": uuid4(),
                "hindcast_forecast_id": hindcast.id,
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
            self._conn.execute(sa.insert(hindcast_values), rows)

        return hindcast.id

    def fetch_hindcasts(
        self,
        station_id: StationId,
        model_id: ModelId,
        start: UtcDatetime,
        end: UtcDatetime,
        forcing_type: ForcingType | None = None,
        hindcast_run_id: UUID | None = None,
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
                )
            )
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
