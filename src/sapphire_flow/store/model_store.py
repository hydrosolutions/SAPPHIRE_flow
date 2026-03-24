from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import models
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.enums import ArtifactScope
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.model import ModelRecord


class PgModelStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def register_model(self, record: ModelRecord) -> None:
        self._conn.execute(
            pg_insert(models)
            .values(
                id=record.id,
                display_name=record.display_name,
                artifact_scope=record.artifact_scope.value,
                description=record.description,
                created_at=record.created_at,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )

    def fetch_model(self, model_id: ModelId) -> ModelRecord | None:
        row = (
            self._conn.execute(sa.select(models).where(models.c.id == model_id))
            .mappings()
            .one_or_none()
        )
        return _row_to_domain(row) if row is not None else None

    def fetch_all_models(self) -> list[ModelRecord]:
        rows = self._conn.execute(sa.select(models)).mappings().all()
        return [_row_to_domain(row) for row in rows]


def _row_to_domain(row: sa.engine.row.RowMapping) -> ModelRecord:
    return ModelRecord(
        id=ModelId(row["id"]),
        display_name=row["display_name"],
        artifact_scope=ArtifactScope(row["artifact_scope"]),
        description=row["description"],
        created_at=utc_from_row(row["created_at"]),
    )
