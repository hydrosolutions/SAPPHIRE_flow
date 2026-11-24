# ruff: noqa: TC001, TC003 -- Pydantic resolves database boundary annotations.
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import (
    forecast_publication_selections,
    forecast_values,
    forecasts,
    hindcast_forecasts,
    hindcast_values,
    model_artifacts,
    models,
    stations,
)
from sapphire_flow.store.forecast_publication_store import PgForecastPublicationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
from sapphire_flow.types.event_review import (
    MAX_REVIEW_HEADERS,
    MAX_REVIEW_POINTS,
    MAX_REVIEW_SOURCES,
    MAX_REVIEW_VALUES,
    ReviewCatalogLimitError,
    ReviewCompleteness,
    ReviewKind,
    ReviewOptionsQuery,
    ReviewPolicy,
    ReviewQueryTimeoutError,
    ReviewSeriesError,
    ReviewSeriesQuery,
    ReviewSeriesRecord,
    ReviewSeriesSnapshot,
    ReviewSource,
    ReviewSourceMode,
)
from sapphire_flow.types.ids import ArtifactId, ModelId

if TYPE_CHECKING:
    from typing import Any


class _SourceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ReviewKind
    model_id: str
    model_name: str
    artifact_id: UUID | None
    artifact_sha256: str | None
    training_period_start: datetime | None
    training_period_end: datetime | None
    hindcast_run_id: UUID | None
    forcing_type: ForcingType | None
    time_step_seconds: int | None = Field(gt=0)
    units: str
    lead_hours: tuple[int, ...]
    first_valid_time: datetime | None
    last_valid_time: datetime | None
    orphan_headers: int = Field(ge=0)
    invalid_leads: int = Field(ge=0)


_ROWS = TypeAdapter(tuple[_SourceRow, ...])


class _PointRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ReviewKind
    id: UUID
    model_id: str
    artifact_id: UUID | None
    issue_time: datetime
    hindcast_run_id: UUID | None
    forcing_type: ForcingType | None
    time_step_seconds: int | None
    units: str
    representation: EnsembleRepresentation
    qc_status: QcStatus
    created_at: datetime
    valid_time: datetime
    values: tuple[tuple[float | None, float], ...] | None


_POINT_ROWS = TypeAdapter(tuple[_PointRow, ...])


def _identity(source: ReviewSource | _PointRow) -> tuple[object, ...]:
    return (
        source.kind,
        source.model_id,
        source.artifact_id,
        source.hindcast_run_id,
        source.forcing_type,
        source.time_step_seconds,
        source.units,
    )


def _series_header_columns(
    table: sa.Table,
    value_table: sa.Table,
    foreign_key: sa.Column[Any],
    issue: sa.Column[Any],
    query: ReviewSeriesQuery | None,
) -> list[sa.ColumnElement[Any]]:
    if query is None:
        return []
    stored = (
        sa.select(1)
        .select_from(value_table)
        .where(foreign_key == table.c.id)
        .correlate(table)
    )
    offered = stored.where(
        value_table.c.lead_time_hours == query.lead_hours,
        value_table.c.valid_time
        == issue
        + sa.literal_column("INTERVAL '1 hour'", type_=sa.Interval())
        * query.lead_hours,
    )
    return [
        table.c.representation,
        table.c.qc_status,
        table.c.created_at,
        stored.exists().label("has_values"),
        offered.exists().label("lead_offered"),
    ]


def _catalog_statement(
    query: ReviewOptionsQuery, series: ReviewSeriesQuery | None = None
) -> sa.Select[Any]:
    f, h = forecasts.c, hindcast_forecasts.c
    operational: sa.Select[Any] = (
        sa.select(
            sa.literal("operational").label("kind"),
            f.id,
            f.model_id,
            f.model_artifact_id.label("artifact_id"),
            f.issued_at.label("issue_time"),
            sa.cast(sa.null(), sa.Uuid(as_uuid=True)).label("hindcast_run_id"),
            sa.cast(sa.null(), sa.Text).label("forcing_type"),
            f.time_step_seconds,
            f.units,
            *_series_header_columns(
                forecasts,
                forecast_values,
                forecast_values.c.forecast_id,
                f.issued_at,
                series,
            ),
        )
        .join(stations, stations.c.id == f.station_id)
        .where(
            f.station_id == query.station_id,
            stations.c.tenant_id == query.tenant_id,
            f.parameter == "discharge",
            f.data_use == "standard",
            f.period_definition.is_(None),
        )
    )
    if series:
        operational = operational.where(f.model_id == series.model_id)
    if query.policy is ReviewPolicy.ORDINARY:
        operational = operational.where(f.status != "superseded")
    if query.policy is ReviewPolicy.PUBLISHED_ONLY:
        selection = forecast_publication_selections.c
        operational = operational.join(
            forecast_publication_selections, selection.selected_forecast_id == f.id
        ).where(selection.tenant_id == query.tenant_id)

    header_selects: list[sa.Select[Any]] = (
        [operational.limit(MAX_REVIEW_HEADERS + 1)]
        if not series or series.source_mode is not ReviewSourceMode.HINDCAST
        else []
    )
    if query.policy is ReviewPolicy.ORDINARY and (
        not series or series.source_mode is not ReviewSourceMode.OPERATIONAL
    ):
        header_selects.append(
            sa.select(
                sa.literal("hindcast").label("kind"),
                h.id,
                h.model_id,
                h.model_artifact_id.label("artifact_id"),
                h.hindcast_step.label("issue_time"),
                h.hindcast_run_id,
                h.forcing_type,
                h.time_step_seconds,
                h.units,
                *_series_header_columns(
                    hindcast_forecasts,
                    hindcast_values,
                    hindcast_values.c.hindcast_forecast_id,
                    h.hindcast_step,
                    series,
                ),
            )
            .join(stations, stations.c.id == h.station_id)
            .where(
                h.station_id == query.station_id,
                stations.c.tenant_id == query.tenant_id,
                h.parameter == "discharge",
                *(
                    [
                        h.model_id == series.model_id,
                        h.hindcast_run_id == series.hindcast_run_id,
                        h.model_artifact_id == series.hindcast_artifact_id,
                        h.forcing_type == series.forcing_type.value,
                        h.time_step_seconds == series.time_step_seconds,
                    ]
                    if series and series.forcing_type
                    else []
                ),
            )
            .limit(MAX_REVIEW_HEADERS + 1)
        )
    headers = (
        (
            sa.union_all(*header_selects)
            if len(header_selects) > 1
            else header_selects[0]
        )
        .limit(MAX_REVIEW_HEADERS + 1)
        .cte("review_headers")
        .prefix_with("MATERIALIZED")
    )
    header_count = sa.select(sa.func.count()).select_from(headers).scalar_subquery()
    value_selects: list[sa.Select[Any]] = []
    for kind, table, fk in (
        (ReviewKind.OPERATIONAL, forecast_values, forecast_values.c.forecast_id),
        (ReviewKind.HINDCAST, hindcast_values, hindcast_values.c.hindcast_forecast_id),
    ):
        if kind is ReviewKind.HINDCAST and query.policy is ReviewPolicy.PUBLISHED_ONLY:
            continue
        if series and (
            (
                kind is ReviewKind.HINDCAST
                and series.source_mode is ReviewSourceMode.OPERATIONAL
            )
            or (
                kind is ReviewKind.OPERATIONAL
                and series.source_mode is ReviewSourceMode.HINDCAST
            )
        ):
            continue
        value_selects.append(
            sa.select(
                headers.c.kind,
                headers.c.id.label("header_id"),
                table.c.valid_time,
                table.c.lead_time_hours,
                (
                    sa.extract("epoch", table.c.valid_time - headers.c.issue_time)
                    == table.c.lead_time_hours * 3600
                ).label("exact_lead"),
                *(
                    [table.c.member_id, table.c.quantile, table.c.value]
                    if series
                    else []
                ),
            )
            .join(headers, sa.and_(fk == headers.c.id, headers.c.kind == kind.value))
            .where(
                header_count <= MAX_REVIEW_HEADERS,
                *(
                    [
                        table.c.valid_time >= series.start,
                        table.c.valid_time < series.end,
                        table.c.lead_time_hours == series.lead_hours,
                        table.c.valid_time
                        == headers.c.issue_time
                        + sa.literal_column("INTERVAL '1 hour'", type_=sa.Interval())
                        * series.lead_hours,
                    ]
                    if series
                    else []
                ),
            )
            .limit(MAX_REVIEW_VALUES + 1)
        )
    values = (
        (sa.union_all(*value_selects) if len(value_selects) > 1 else value_selects[0])
        .limit(MAX_REVIEW_VALUES + 1)
        .cte("review_values")
        .prefix_with("MATERIALIZED")
    )
    value_count = sa.select(sa.func.count()).select_from(values).scalar_subquery()
    valid_lead = sa.and_(values.c.exact_lead, values.c.lead_time_hours >= 0)
    identity = [
        headers.c.kind,
        headers.c.model_id,
        headers.c.artifact_id,
        headers.c.hindcast_run_id,
        headers.c.forcing_type,
        headers.c.time_step_seconds,
        headers.c.units,
    ]
    grouped = (
        sa.select(
            *identity,
            sa.func.array_agg(
                sa.distinct(
                    sa.literal(series.lead_hours)
                    if series
                    else values.c.lead_time_hours
                )
            )
            .filter(headers.c.lead_offered if series else valid_lead)
            .label("lead_hours"),
            sa.func.min(values.c.valid_time)
            .filter(valid_lead)
            .label("first_valid_time"),
            sa.func.max(values.c.valid_time)
            .filter(valid_lead)
            .label("last_valid_time"),
            sa.func.count(sa.distinct(headers.c.id))
            .filter(~headers.c.has_values if series else values.c.header_id.is_(None))
            .label("orphan_headers"),
            sa.func.count()
            .filter(sa.and_(values.c.header_id.is_not(None), ~valid_lead))
            .label("invalid_leads"),
        )
        .select_from(
            headers.outerjoin(
                values,
                sa.and_(
                    values.c.header_id == headers.c.id, values.c.kind == headers.c.kind
                ),
            )
        )
        .where(header_count <= MAX_REVIEW_HEADERS, value_count <= MAX_REVIEW_VALUES)
        .group_by(*identity)
        .limit(MAX_REVIEW_SOURCES + 1)
        .cte("review_sources")
    )
    rows = (
        sa.select(
            *grouped.c,
            models.c.display_name.label("model_name"),
            model_artifacts.c.sha256_hash.label("artifact_sha256"),
            model_artifacts.c.training_period_start,
            model_artifacts.c.training_period_end,
        )
        .join(models, models.c.id == grouped.c.model_id)
        .outerjoin(
            model_artifacts,
            sa.and_(
                model_artifacts.c.id == grouped.c.artifact_id,
                model_artifacts.c.model_id == grouped.c.model_id,
            ),
        )
        .subquery()
    )
    statement = sa.select(
        header_count.label("header_count"),
        value_count.label("value_count"),
        sa.select(sa.func.jsonb_agg(sa.func.row_to_json(rows.table_valued())))
        .scalar_subquery()
        .label("sources"),
    )
    if series is None:
        return statement
    valid_time = (
        headers.c.issue_time
        + sa.literal_column("INTERVAL '1 hour'", type_=sa.Interval())
        * series.lead_hours
    )
    selected = (
        sa.select(*headers.c, valid_time.label("valid_time"))
        .where(
            valid_time >= series.start,
            valid_time < series.end,
            header_count <= MAX_REVIEW_HEADERS,
            value_count <= MAX_REVIEW_VALUES,
        )
        .limit(MAX_REVIEW_POINTS + 1)
        .cte("review_selected")
        .prefix_with("MATERIALIZED")
    )
    point_count = sa.select(sa.func.count()).select_from(selected).scalar_subquery()
    # Only exact requested-lead values enter the payload, never all leads/members
    # of unrelated issues. The same materialized snapshot also validates options.
    point_rows = (
        sa.select(
            *(
                column
                for column in selected.c
                if column.name not in {"has_values", "lead_offered"}
            ),
            sa.func.jsonb_agg(
                sa.func.jsonb_build_array(
                    sa.case(
                        (selected.c.representation == "quantiles", values.c.quantile),
                        else_=values.c.member_id,
                    ),
                    values.c.value,
                )
            )
            .filter(values.c.header_id.is_not(None))
            .label("values"),
        )
        .select_from(
            selected.outerjoin(
                values,
                sa.and_(
                    values.c.header_id == selected.c.id,
                    values.c.kind == selected.c.kind,
                    values.c.valid_time == selected.c.valid_time,
                    values.c.lead_time_hours == series.lead_hours,
                    values.c.exact_lead,
                ),
            )
        )
        .where(point_count <= MAX_REVIEW_POINTS)
        .group_by(*selected.c)
        .subquery()
    )
    return statement.add_columns(
        point_count.label("point_count"),
        sa.select(sa.func.jsonb_agg(sa.func.row_to_json(point_rows.table_valued())))
        .scalar_subquery()
        .label("points"),
    )


def _source(row: _SourceRow) -> ReviewSource:
    reasons = tuple(
        reason
        for count, reason in (
            (row.orphan_headers, "orphan_headers"),
            (row.invalid_leads, "invalid_lead_timestamps"),
        )
        if count
    )
    return ReviewSource(
        kind=row.kind,
        model_id=ModelId(row.model_id),
        model_name=row.model_name,
        artifact_id=ArtifactId(row.artifact_id) if row.artifact_id else None,
        artifact_sha256=row.artifact_sha256 or None,
        training_period_start=ensure_utc(row.training_period_start)
        if row.training_period_start
        else None,
        training_period_end=ensure_utc(row.training_period_end)
        if row.training_period_end
        else None,
        hindcast_run_id=row.hindcast_run_id,
        forcing_type=row.forcing_type,
        time_step_seconds=row.time_step_seconds,
        units=row.units,
        lead_hours=tuple(sorted(row.lead_hours)),
        first_valid_time=ensure_utc(row.first_valid_time)
        if row.first_valid_time
        else None,
        last_valid_time=ensure_utc(row.last_valid_time)
        if row.last_valid_time
        else None,
        completeness=ReviewCompleteness.INCOMPLETE
        if reasons
        else ReviewCompleteness.UNKNOWN,
        reasons=reasons or ("expected_coverage_unknown",),
    )


class PgEventReviewStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def _fetch(
        self, query: ReviewOptionsQuery, series: ReviewSeriesQuery | None = None
    ) -> sa.RowMapping:
        try:
            # A cancelled statement must not abort the request's auth transaction.
            with self._conn.begin_nested():
                previous_timeout = self._conn.scalar(sa.text("SHOW statement_timeout"))
                self._conn.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
                if query.policy is ReviewPolicy.PUBLISHED_ONLY:
                    PgForecastPublicationStore(self._conn).lock_read_snapshot()
                statement = (
                    _catalog_statement(query, series)
                    if series
                    else _catalog_statement(query)
                )
                result = self._conn.execute(statement).mappings().one()
                self._conn.execute(
                    sa.text("SELECT set_config('statement_timeout', :value, true)"),
                    {"value": previous_timeout},
                )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "57014":
                raise ReviewQueryTimeoutError("Review query timed out") from exc
            raise
        return result

    def fetch_options(self, query: ReviewOptionsQuery) -> tuple[ReviewSource, ...]:
        return self._sources(self._fetch(query))

    @staticmethod
    def _sources(result: sa.RowMapping) -> tuple[ReviewSource, ...]:
        raw: list[dict[str, Any]] = result["sources"] or []
        if (
            result["header_count"] > MAX_REVIEW_HEADERS
            or result["value_count"] > MAX_REVIEW_VALUES
            or len(raw) > MAX_REVIEW_SOURCES
        ):
            raise ReviewCatalogLimitError("Review catalog exceeds its work limit")
        for row in raw:
            row["lead_hours"] = row["lead_hours"] or []
        return tuple(
            sorted(
                (_source(row) for row in _ROWS.validate_python(raw)),
                key=lambda source: (
                    source.model_id,
                    source.kind,
                    str(source.hindcast_run_id),
                    str(source.artifact_id),
                    str(source.forcing_type),
                    source.time_step_seconds or 0,
                    source.units,
                ),
            )
        )

    def fetch_series(self, query: ReviewSeriesQuery) -> ReviewSeriesSnapshot:
        if query.scope.policy is ReviewPolicy.PUBLISHED_ONLY and (
            query.source_mode is not ReviewSourceMode.OPERATIONAL
        ):
            raise ReviewSeriesError(
                "review_source_unsupported",
                "Hindcasts unavailable under publication policy.",
            )
        if query.source_mode is ReviewSourceMode.OPERATIONAL_WITH_HINDCAST:
            raise ReviewSeriesError(
                "review_source_unsupported",
                "Temporal support is unknown; automatic fallback is unavailable.",
            )
        result = self._fetch(query.scope, query)
        try:
            sources = self._sources(result)
        except ReviewCatalogLimitError as exc:
            raise ReviewSeriesError(
                "review_series_limit_exceeded",
                "Review exceeds work limits; narrow the selection.",
            ) from exc
        if result["point_count"] > MAX_REVIEW_POINTS:
            raise ReviewSeriesError(
                "review_series_limit_exceeded",
                "Too many review points; narrow the period.",
            )
        source_map = {_identity(source): source for source in sources}
        records = tuple(
            ReviewSeriesRecord(
                forecast_id=row.id,
                source=source_map[_identity(row)],
                issued_at=ensure_utc(row.issue_time),
                valid_time=ensure_utc(row.valid_time),
                stored_at=ensure_utc(row.created_at),
                representation=row.representation,
                qc_status=row.qc_status,
                values=row.values or (),
            )
            for row in _POINT_ROWS.validate_python(result["points"] or [])
        )
        return ReviewSeriesSnapshot(sources=sources, records=records)
