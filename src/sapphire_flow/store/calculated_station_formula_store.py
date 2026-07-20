# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import calculated_station_formulas as _csf
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.calculated_station import ComponentWeight
from sapphire_flow.types.ids import FormulaId, StationId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.datetime import UtcDatetime


class PgFormulaStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_formula(self, rows: Sequence[ComponentWeight]) -> None:
        if not rows:
            return
        self._conn.execute(
            sa.insert(_csf),
            [
                {
                    "id": r.id,
                    "calculated_station_id": r.calculated_station_id,
                    "component_station_id": r.component_station_id,
                    "parameter": r.parameter,
                    "weight": r.weight,
                    "effective_from": r.effective_from,
                    "effective_to": r.effective_to,
                    "created_at": r.created_at,
                }
                for r in rows
            ],
        )

    def close_formula(
        self,
        calculated_station_id: StationId,
        parameter: str,
        effective_to: UtcDatetime,
    ) -> int:
        result = self._conn.execute(
            sa.update(_csf)
            .where(_csf.c.calculated_station_id == calculated_station_id)
            .where(_csf.c.parameter == parameter)
            .where(_csf.c.effective_to.is_(None))
            .values(effective_to=effective_to)
        )
        return result.rowcount

    def fetch_current_formula(
        self, calculated_station_id: StationId, parameter: str
    ) -> Sequence[ComponentWeight]:
        rows = (
            self._conn.execute(
                sa.select(_csf)
                .where(_csf.c.calculated_station_id == calculated_station_id)
                .where(_csf.c.parameter == parameter)
                .where(_csf.c.effective_to.is_(None))
                .order_by(_csf.c.component_station_id)
            )
            .mappings()
            .all()
        )
        return [_row_to_weight(row) for row in rows]

    def fetch_formula_at(
        self, calculated_station_id: StationId, parameter: str, at: UtcDatetime
    ) -> Sequence[ComponentWeight]:
        # Per component, the row with the greatest effective_from <= at whose validity
        # covers `at` — deterministic latest-wins even if closed windows ever overlap.
        # Stable tie-breaker (effective_from, created_at, id) since the schema only bars
        # duplicate *current* rows, not two closed rows sharing an effective_from.
        rows = (
            self._conn.execute(
                sa.select(_csf)
                .where(_csf.c.calculated_station_id == calculated_station_id)
                .where(_csf.c.parameter == parameter)
                .where(_csf.c.effective_from <= at)
                .where(sa.or_(_csf.c.effective_to.is_(None), _csf.c.effective_to > at))
                .distinct(_csf.c.component_station_id)
                .order_by(
                    _csf.c.component_station_id,
                    _csf.c.effective_from.desc(),
                    _csf.c.created_at.desc(),
                    _csf.c.id.desc(),
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_weight(row) for row in rows]

    def fetch_formulas_for_stations(
        self, station_ids: list[StationId]
    ) -> dict[tuple[StationId, str], list[ComponentWeight]]:
        if not station_ids:
            return {}
        rows = (
            self._conn.execute(
                sa.select(_csf)
                .where(_csf.c.calculated_station_id.in_(station_ids))
                .where(_csf.c.effective_to.is_(None))
                .order_by(
                    _csf.c.calculated_station_id,
                    _csf.c.parameter,
                    _csf.c.component_station_id,
                )
            )
            .mappings()
            .all()
        )
        grouped: dict[tuple[StationId, str], list[ComponentWeight]] = {}
        for row in rows:
            weight = _row_to_weight(row)
            grouped.setdefault(
                (weight.calculated_station_id, weight.parameter), []
            ).append(weight)
        return grouped


def _row_to_weight(row: sa.engine.row.RowMapping) -> ComponentWeight:
    return ComponentWeight(
        id=FormulaId(row["id"]),
        calculated_station_id=StationId(row["calculated_station_id"]),
        component_station_id=StationId(row["component_station_id"]),
        parameter=row["parameter"],
        weight=row["weight"],
        effective_from=utc_from_row(row["effective_from"]),
        effective_to=utc_or_none(row["effective_to"]),
        created_at=utc_from_row(row["created_at"]),
    )
