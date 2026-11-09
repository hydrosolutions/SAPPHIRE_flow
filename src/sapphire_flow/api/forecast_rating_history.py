from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from fastapi import Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.provisional_forecasts import (
    require_provisional_role,
    unavailable,
)
from sapphire_flow.api.security import Principal, require_reviewer
from sapphire_flow.db.metadata import forecasts, stations
from sapphire_flow.services.forecast_rating_history import build_forecast_rating_history
from sapphire_flow.types.forecast_rating_history import (
    ForecastRatingHistory,
    RatingHistoryEvidence,
)

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ForecastId

_EVIDENCE = TypeAdapter(RatingHistoryEvidence)


class ForecastRatingHistoryReader:
    def __init__(self, conn: sa.Connection, principal: Principal) -> None:
        self.conn = conn
        self.principal = principal

    def read(self, forecast_id: ForecastId) -> ForecastRatingHistory:
        require_provisional_role(self.principal)
        scope = (
            sa.true()
            if self.principal.is_admin
            else sa.and_(
                stations.c.id.in_(self.principal.station_ids),
                stations.c.tenant_id == self.principal.tenant_id,
            )
        )
        query = (
            sa.select(
                forecasts.c.id,
                forecasts.c.station_id,
                stations.c.tenant_id,
                forecasts.c.issued_at,
                forecasts.c.model_artifact_id,
            )
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(
                scope,
                forecasts.c.id == forecast_id,
                forecasts.c.data_use == "provisional_rating",
                forecasts.c.rating_curve_id.is_(None),
            )
        )
        try:
            header = self.conn.execute(query).one_or_none()
        except SQLAlchemyError as exc:
            raise unavailable("header", exc) from None
        if header is None:
            raise HTTPException(404, "Forecast not found")
        try:
            self.conn.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
            raw = self.conn.scalar(
                sa.select(sa.func.forecast_rating_input_history(forecast_id))
            )
            evidence = _EVIDENCE.validate_python(raw)
            actual = (
                evidence.forecast_id,
                evidence.station_id,
                evidence.tenant_id,
                evidence.issued_at,
                evidence.artifact_id,
            )
            if actual != tuple(header):
                raise ValueError("rating history parent disagrees")
            return build_forecast_rating_history(evidence)
        except (SQLAlchemyError, ValueError, OverflowError) as exc:
            raise unavailable("lineage", exc) from None


def get_rating_history_reader(
    conn: sa.Connection = Depends(get_connection),
    principal: Principal = Depends(require_reviewer),
) -> ForecastRatingHistoryReader:
    return ForecastRatingHistoryReader(conn, principal)
