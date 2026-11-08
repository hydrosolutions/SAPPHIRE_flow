from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa
import structlog
from fastapi import Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.db.metadata import forecasts, stations
from sapphire_flow.exceptions import StoreError
from sapphire_flow.store.forecast_store import (
    PgForecastStore,
    forecast_summary_from_row,
)
from sapphire_flow.types.enums import ForecastDataUse, ForecastStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.forecast_summary import ForecastSummaryRow

_PURPOSE = ForecastDataUse.PROVISIONAL_RATING
_HEADER_NAMES = (
    "id",
    "station_id",
    "model_id",
    "issued_at",
    "parameter",
    "representation",
    "status",
    "qc_status",
    "nwp_cycle_source",
    "created_at",
    "input_quality",
    "input_quality_flags",
    "qc_flags",
    "data_use",
    "model_artifact_id",
    "rating_curve_id",
)


log = structlog.get_logger(__name__)


def unavailable(
    operation: Literal["availability", "header", "detail", "page", "lineage"],
    error: Exception | None = None,
) -> HTTPException:
    category = "invalid_evidence"
    error_class = "ValueError"
    sqlstate: str | None = None
    if isinstance(error, SQLAlchemyError):
        category = "database"
        name = type(error).__name__
        error_class = (
            name
            if name
            in {
                "ProgrammingError",
                "OperationalError",
                "IntegrityError",
                "DataError",
                "InterfaceError",
                "InternalError",
                "DatabaseError",
                "DBAPIError",
            }
            else "SQLAlchemyError"
        )
        candidate = getattr(getattr(error, "orig", None), "sqlstate", None)
        if (
            isinstance(candidate, str)
            and len(candidate) == 5
            and all(
                char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for char in candidate
            )
        ):
            sqlstate = candidate
    elif isinstance(error, StoreError):
        category = "store"
        error_class = "StoreError"
    log.error(
        "provisional_read_unavailable",
        operation=operation,
        failure_category=category,
        error_class=error_class,
        sqlstate=sqlstate,
    )
    return HTTPException(503, "Provisional rating evidence is unavailable")


def require_provisional_role(principal: Principal) -> None:
    if not principal.can_review:
        raise HTTPException(403, "reviewer role required")


@dataclass(frozen=True, kw_only=True, slots=True)
class ProvisionalHeader:
    summary: ForecastSummaryRow
    artifact_id: ArtifactId

    @classmethod
    def from_row(cls, row: sa.RowMapping) -> ProvisionalHeader:
        summary = forecast_summary_from_row(row)
        if (
            summary.data_use is not _PURPOSE
            or row["model_artifact_id"] is None
            or row["rating_curve_id"] is not None
        ):
            raise ValueError("invalid provisional parent")
        return cls(summary=summary, artifact_id=ArtifactId(row["model_artifact_id"]))


def rating_metadata(
    header: ProvisionalHeader, lineage: ForecastInputLineage
) -> dict[str, Any]:
    rating = lineage.rating_input
    if (
        rating is None
        or rating.artifact_id is None
        or rating.issue_time != header.summary.issued_at
        or rating.artifact_id != header.artifact_id
        or lineage.snapshots
        or lineage.static_attributes
        or lineage.provisional_discharge_fingerprints
        or lineage.contributor_forecast_ids
    ):
        raise ValueError("provisional lineage disagrees with parent")
    return {
        "data_use": _PURPOSE.value,
        "badge": "PROVISIONAL",
        "warnings": sorted(
            {warning for day in rating.days for warning in day.warnings}
        ),
        "rating_curves": [],
        "rating_provenance_status": "not_exposed",
        "publication_applicability": "not_applicable",
        "publication_state": None,
        "publication_decision_id": None,
        "selection_version": None,
        "published_at": None,
        "preservation_at_publish": None,
    }


class ProvisionalForecastReader:
    def __init__(self, conn: sa.Connection, principal: Principal) -> None:
        self.conn = conn
        self.principal = principal

    @property
    def store(self) -> PgForecastStore:
        return PgForecastStore(self.conn, data_use=_PURPOSE)

    def _scope(self) -> sa.ColumnElement[bool]:
        if self.principal.is_admin:
            return sa.true()
        return sa.and_(
            stations.c.id.in_(self.principal.station_ids),
            stations.c.tenant_id == self.principal.tenant_id,
        )

    def availability(
        self, station_ids: Sequence[StationId]
    ) -> dict[StationId, bool | None]:
        if not self.principal.can_review:
            return dict.fromkeys(station_ids, None)
        if not station_ids:
            return {}
        query = (
            sa.select(forecasts.c.station_id)
            .distinct()
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(
                self._scope(),
                stations.c.id.in_(station_ids),
                forecasts.c.data_use == _PURPOSE.value,
                forecasts.c.status != ForecastStatus.SUPERSEDED.value,
            )
        )
        try:
            present = set(self.conn.execute(query).scalars())
        except SQLAlchemyError as exc:
            raise unavailable("availability", exc) from None
        return {sid: sid in present for sid in station_ids}

    def _headers(self) -> sa.Select[Any]:
        return (
            sa.select(*(forecasts.c[name] for name in _HEADER_NAMES))
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(self._scope(), forecasts.c.data_use == _PURPOSE.value)
        )

    def detail(
        self, forecast_id: ForecastId
    ) -> tuple[OperationalForecast, dict[str, Any]]:
        require_provisional_role(self.principal)
        query = self._headers().where(forecasts.c.id == forecast_id)
        try:
            row = self.conn.execute(query).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise unavailable("header", exc) from None
        if row is None:
            raise HTTPException(404, "Forecast not found")
        try:
            header = ProvisionalHeader.from_row(row)
        except ValueError as exc:
            raise unavailable("header", exc) from None
        store = self.store
        try:
            forecast = store.fetch_forecast(header.summary.id)
        except (StoreError, ValueError) as exc:
            raise unavailable("detail", exc) from None
        if forecast is None:
            raise HTTPException(404, "Forecast not found")
        expected = (
            header.summary.id,
            header.summary.station_id,
            header.summary.model_id,
            header.summary.issued_at,
            header.artifact_id,
            _PURPOSE,
        )
        actual = (
            forecast.id,
            forecast.station_id,
            forecast.model_id,
            forecast.issued_at,
            forecast.model_artifact_id,
            forecast.data_use,
        )
        if actual != expected or forecast.input_lineage is None:
            raise unavailable("detail")
        try:
            metadata = rating_metadata(header, forecast.input_lineage)
        except ValueError as exc:
            raise unavailable("lineage", exc) from None
        return forecast, metadata

    def summaries(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        model_id: ModelId | None,
        parameter: str | None,
        degraded_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[ForecastSummaryRow, dict[str, Any]]], int]:
        require_provisional_role(self.principal)
        filters = [
            forecasts.c.station_id == station_id,
            forecasts.c.issued_at >= start,
            forecasts.c.issued_at < end,
        ]
        if model_id is not None:
            filters.append(forecasts.c.model_id == model_id)
        if parameter is not None:
            filters.append(forecasts.c.parameter == parameter)
        if degraded_only:
            filters.append(forecasts.c.input_quality.in_(("partial", "degraded")))
        query = self._headers().where(*filters)
        try:
            total = self.conn.execute(
                sa.select(sa.func.count()).select_from(query.subquery())
            ).scalar_one()
            rows = (
                self.conn.execute(
                    query.order_by(forecasts.c.issued_at.desc(), forecasts.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            raise unavailable("page", exc) from None
        headers: list[ProvisionalHeader] = []
        for row in rows:
            try:
                header = ProvisionalHeader.from_row(row)
            except ValueError as exc:
                raise unavailable("header", exc) from None
            headers.append(header)
        if not headers:
            return [], total
        ids = tuple(header.summary.id for header in headers)
        lineage_query = (
            sa.select(
                forecasts.c.id,
                sa.func.native_rating_lineage(forecasts.c.id, False).label("content"),
            )
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(
                self._scope(),
                forecasts.c.data_use == _PURPOSE.value,
                forecasts.c.station_id == station_id,
                forecasts.c.id.in_(ids),
            )
        )
        try:
            lineage_rows = self.conn.execute(lineage_query).tuples().all()
        except SQLAlchemyError as exc:
            raise unavailable("lineage", exc) from None
        contents = dict(lineage_rows)
        if set(contents) != set(ids):
            raise unavailable("page")
        result: list[tuple[ForecastSummaryRow, dict[str, Any]]] = []
        for header in headers:
            content = contents[header.summary.id]
            try:
                lineage = ForecastInputLineage.from_content(content)
            except ValueError as exc:
                raise unavailable("lineage", exc) from None
            try:
                metadata = rating_metadata(header, lineage)
            except ValueError as exc:
                raise unavailable("lineage", exc) from None
            result.append((header.summary, metadata))
        return result, total


def get_provisional_reader(
    conn: sa.Connection = Depends(get_connection),
    principal: Principal = Depends(require_principal),
) -> ProvisionalForecastReader:
    return ProvisionalForecastReader(conn, principal)
