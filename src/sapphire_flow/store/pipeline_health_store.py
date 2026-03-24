# pyright: reportUnknownMemberType=false
from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import pipeline_health
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
from sapphire_flow.types.pipeline import PipelineHealthRecord


class PgPipelineHealthStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def append_health_record(self, record: PipelineHealthRecord) -> None:
        self._conn.execute(
            sa.insert(pipeline_health).values(
                check_type=record.check_type.value,
                checked_at=record.checked_at,
                status=record.status.value,
                subject=record.subject,
                detail=record.detail,
                cycle_time=record.cycle_time,
            )
        )

    def fetch_recent(
        self,
        check_type: PipelineCheckType | None = None,
        limit: int = 100,
    ) -> list[PipelineHealthRecord]:
        stmt = sa.select(pipeline_health).order_by(pipeline_health.c.checked_at.desc())
        if check_type is not None:
            stmt = stmt.where(pipeline_health.c.check_type == check_type.value)
        stmt = stmt.limit(limit)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_domain(row) for row in rows]


def _row_to_domain(row: sa.engine.row.RowMapping) -> PipelineHealthRecord:
    return PipelineHealthRecord(
        check_type=PipelineCheckType(row["check_type"]),
        checked_at=utc_from_row(row["checked_at"]),
        status=PipelineHealthStatus(row["status"]),
        subject=row["subject"],
        detail=row["detail"] if row["detail"] is not None else {},
        cycle_time=utc_or_none(row["cycle_time"]),
        created_at=utc_from_row(row["created_at"]),
    )
