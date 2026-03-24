from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import parameters
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.domain import ParameterDefinition
from sapphire_flow.types.enums import AggregationMethod, ParameterDomain


class PgParameterStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_all(self) -> list[ParameterDefinition]:
        rows = self._conn.execute(sa.select(parameters)).mappings().all()
        return [_row_to_domain(row) for row in rows]

    def fetch_by_name(self, name: str) -> ParameterDefinition | None:
        row = (
            self._conn.execute(sa.select(parameters).where(parameters.c.name == name))
            .mappings()
            .one_or_none()
        )
        return _row_to_domain(row) if row is not None else None


def _row_to_domain(row: sa.engine.row.RowMapping) -> ParameterDefinition:
    return ParameterDefinition(
        name=row["name"],
        display_name=row["display_name"],
        unit=row["unit"],
        parameter_domain=ParameterDomain(row["parameter_domain"]),
        aggregation_method=AggregationMethod(row["aggregation_method"]),
        created_at=utc_from_row(row["created_at"]),
    )
