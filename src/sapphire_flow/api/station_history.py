from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import sqlalchemy as sa
import structlog
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.api.deps import get_connection, get_deployment_config
from sapphire_flow.api.provisional_forecasts import require_provisional_role
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.config.deployment import DeploymentConfig  # noqa: TC001
from sapphire_flow.db.metadata import observations, stations
from sapphire_flow.services.station_history import build_station_history
from sapphire_flow.store.bafu_unit_read_filter import unit_read_state
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station_history import (
    HistoryOutcome,
    HistorySample,
    StationHistoryResponse,
)

if TYPE_CHECKING:
    from sapphire_flow.types.station_history import StationHistoryRequest

_RESPONSE = TypeAdapter(StationHistoryResponse)
log = structlog.get_logger(__name__)


class _OrdinaryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    source: Literal["measured", "supplied"]
    value: float | None
    qc_status: QcStatus
    qc_rule_version: str | None
    flags_count: int = Field(ge=0)
    duplicates: int = Field(ge=1)


class _DerivedProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    station_id: UUID
    candidate_count: int = Field(ge=0, le=10000)
    samples: tuple[HistorySample, ...]


_ORDINARY = TypeAdapter(tuple[_OrdinaryRow, ...])


def history_unavailable() -> HTTPException:
    log.error("station_history_unavailable")
    return HTTPException(503, "Observation history is unavailable")


class StationHistoryReader:
    def __init__(
        self,
        conn: sa.Connection,
        principal: Principal,
        config: DeploymentConfig | None = None,
    ) -> None:
        self.conn = conn
        self.principal = principal
        self.config = config

    def read(self, request: StationHistoryRequest) -> StationHistoryResponse:
        provisional = ForecastDataUse.PROVISIONAL_RATING in request.purposes
        if provisional:
            require_provisional_role(self.principal)
        scope = (
            sa.true()
            if self.principal.is_admin
            else sa.and_(
                stations.c.id.in_(self.principal.station_ids),
                stations.c.tenant_id == self.principal.tenant_id,
            )
        )
        try:
            tenant = self.conn.scalar(
                sa.select(stations.c.tenant_id).where(
                    stations.c.id == request.station_id,
                    scope,
                )
            )
        except SQLAlchemyError:
            raise history_unavailable() from None
        if tenant is None:
            raise HTTPException(404, "Station not found")
        try:
            self.conn.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
            ordinary = self._ordinary(request, TenantId(tenant))
            enabled = self.config is not None and any(
                item.station_id == request.station_id
                for item in self.config.provisional_ratings
            )
            derived = (
                sa.func.station_rating_history(
                    tenant,
                    request.station_id,
                    request.start,
                    request.end,
                    enabled,
                )
                if provisional
                else sa.null()
            )
            # The ordinary rows and stable projection share one statement snapshot.
            ordinary_raw, derived_raw = self.conn.execute(
                sa.select(ordinary, derived)
            ).one()
            samples = self._ordinary_samples(
                _ORDINARY.validate_python(ordinary_raw or [])
            )
            count = len(samples)
            if derived_raw is not None:
                projection = _DerivedProjection.model_validate(derived_raw)
                if (projection.tenant_id, projection.station_id) != (
                    tenant,
                    request.station_id,
                ):
                    raise ValueError("history projection scope mismatch")
                count += projection.candidate_count
                samples += projection.samples
            if count > 10000:
                raise ValueError("history candidate budget exceeded")
            result = build_station_history(request, samples)
            if len(_RESPONSE.dump_json(result)) > 2 * 1024 * 1024:
                raise ValueError("history response budget exceeded")
            return result
        except (SQLAlchemyError, ValueError, OverflowError, KeyError, TypeError):
            raise history_unavailable() from None

    def _ordinary(
        self,
        request: StationHistoryRequest,
        tenant_id: TenantId,
    ) -> sa.ColumnElement[Any]:
        state = unit_read_state.alias("history_unit_state")
        filters = [
            state.c.station_id == request.station_id,
            state.c.parameter == "discharge",
            state.c.timestamp >= request.start,
            state.c.timestamp < request.end,
            state.c.source.in_(("measured", "manual_import")),
            ~state.c.held,
            sa.or_(~state.c.evidence_required, state.c.canonical),
        ]
        if ForecastDataUse.STANDARD not in request.purposes:
            filters.append(sa.false())
        if not self.principal.can_review:
            filters.append(observations.c.delivery_id.is_distinct_from(DELIVERY_ID))
        source = sa.case(
            (observations.c.source == "measured", "measured"), else_="supplied"
        )
        # Materialize one bounded trust-view scan, rather than repeatedly
        # evaluating the security-barrier view for each observation.
        visible = (
            sa.select(observations.c.id)
            .join(
                state,
                state.c.observation_id == observations.c.id,
            )
            .join(stations, stations.c.id == observations.c.station_id)
            .where(stations.c.tenant_id == tenant_id, *filters)
            .limit(10001)
            .cte("history_visible")
            .prefix_with("MATERIALIZED")
        )
        rows = (
            sa.select(
                observations.c.timestamp,
                observations.c.value,
                observations.c.qc_status,
                observations.c.qc_rule_version,
                sa.func.jsonb_array_length(
                    sa.func.coalesce(
                        sa.func.nullif(
                            observations.c.qc_flags, sa.text("'null'::jsonb")
                        ),
                        sa.text("'[]'::jsonb"),
                    )
                ).label("flags_count"),
                source.label("source"),
                sa.func.count()
                .over(partition_by=(observations.c.timestamp, source))
                .label("duplicates"),
            )
            .join(visible, visible.c.id == observations.c.id)
            .order_by(observations.c.timestamp, observations.c.id)
            .limit(10001)
            .subquery()
        )
        return sa.select(
            sa.func.jsonb_agg(
                aggregate_order_by(
                    sa.func.row_to_json(rows.table_valued()),
                    rows.c.timestamp,
                )
            )
        ).scalar_subquery()

    @staticmethod
    def _ordinary_samples(rows: tuple[_OrdinaryRow, ...]) -> tuple[HistorySample, ...]:
        result: list[HistorySample] = []
        for row in rows:
            status = row.qc_status
            value = row.value
            reason = None
            outcome = HistoryOutcome.ACCEPTED
            if row.duplicates > 1:
                outcome, reason = HistoryOutcome.HELD, "ambiguous_source"
            elif status in (QcStatus.QC_FAILED, QcStatus.MISSING):
                outcome, reason = HistoryOutcome.REJECTED, "source_qc_rejected"
            elif value is None or not math.isfinite(float(value)):
                outcome, reason = HistoryOutcome.REJECTED, "nonfinite_value"
            result.append(
                HistorySample(
                    timestamp=ensure_utc(row.timestamp),
                    source=row.source,
                    data_use=ForecastDataUse.STANDARD,
                    outcome=outcome,
                    value=value if outcome is HistoryOutcome.ACCEPTED else None,
                    reason=reason,
                    source_qc_status=status,
                    source_assessment="assessed"
                    if status is QcStatus.QC_PASSED
                    and row.qc_rule_version
                    and row.flags_count
                    else "unassessed",
                )
            )
        return tuple(result)


def get_station_history_reader(
    conn: sa.Connection = Depends(get_connection),
    principal: Principal = Depends(require_principal),
    config: DeploymentConfig | None = Depends(get_deployment_config),
) -> StationHistoryReader:
    return StationHistoryReader(conn, principal, config)
