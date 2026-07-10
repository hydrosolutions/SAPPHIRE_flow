# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import (
    group_model_assignments,
    station_group_members,
    station_groups,
)
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.enums import ModelAssignmentStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.station import GroupModelAssignment, StationGroup

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager as ContextManager


class PgStationGroupStore:
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

    def store_group(self, group: StationGroup) -> None:
        with self._begin() as txn:
            txn.execute(
                pg_insert(station_groups)
                .values(
                    id=group.id,
                    name=group.name,
                    description=group.description,
                    created_at=group.created_at,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "name": group.name,
                        "description": group.description,
                    },
                )
            )
            if group.station_ids:
                txn.execute(
                    pg_insert(station_group_members)
                    .values(
                        [
                            {"group_id": group.id, "station_id": sid}
                            for sid in group.station_ids
                        ]
                    )
                    .on_conflict_do_nothing()
                )

    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None:
        row = (
            self._conn.execute(
                sa.select(station_groups).where(station_groups.c.id == group_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return _build_group(self._conn, row)

    def fetch_group_by_name(self, name: str) -> StationGroup | None:
        row = (
            self._conn.execute(
                sa.select(station_groups).where(station_groups.c.name == name)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return _build_group(self._conn, row)

    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]:
        rows = (
            self._conn.execute(
                sa.select(station_groups)
                .join(
                    station_group_members,
                    station_groups.c.id == station_group_members.c.group_id,
                )
                .where(station_group_members.c.station_id == station_id)
            )
            .mappings()
            .all()
        )
        return [_build_group(self._conn, row) for row in rows]

    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]:
        rows = (
            self._conn.execute(
                sa.select(station_groups)
                .join(
                    group_model_assignments,
                    station_groups.c.id == group_model_assignments.c.group_id,
                )
                .where(
                    sa.and_(
                        group_model_assignments.c.model_id == model_id,
                        group_model_assignments.c.status
                        == ModelAssignmentStatus.ACTIVE.value,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_build_group(self._conn, row) for row in rows]

    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        self._conn.execute(
            pg_insert(station_group_members)
            .values(group_id=group_id, station_id=station_id)
            .on_conflict_do_nothing()
        )

    def remove_station_from_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        self._conn.execute(
            sa.delete(station_group_members).where(
                sa.and_(
                    station_group_members.c.group_id == group_id,
                    station_group_members.c.station_id == station_id,
                )
            )
        )

    def store_group_model_assignment(self, assignment: GroupModelAssignment) -> None:
        self._conn.execute(
            pg_insert(group_model_assignments)
            .values(
                group_id=assignment.group_id,
                model_id=assignment.model_id,
                time_step=assignment.time_step,
                status=assignment.status.value,
                priority=assignment.priority,
                created_at=assignment.created_at,
            )
            .on_conflict_do_update(
                index_elements=["group_id", "model_id"],
                set_={
                    "time_step": assignment.time_step,
                    "status": assignment.status.value,
                    "priority": assignment.priority,
                    "created_at": assignment.created_at,
                },
            )
        )

    def fetch_group_model_assignments(
        self, group_id: StationGroupId
    ) -> tuple[GroupModelAssignment, ...]:
        rows = (
            self._conn.execute(
                sa.select(group_model_assignments).where(
                    group_model_assignments.c.group_id == group_id
                )
            )
            .mappings()
            .all()
        )
        return tuple(_row_to_group_assignment(row) for row in rows)


def _row_to_group_assignment(
    row: sa.engine.row.RowMapping,
) -> GroupModelAssignment:
    return GroupModelAssignment(
        group_id=StationGroupId(row["group_id"]),
        model_id=ModelId(row["model_id"]),
        time_step=row["time_step"],
        status=ModelAssignmentStatus(row["status"]),
        priority=row["priority"],
        created_at=utc_from_row(row["created_at"]),
    )


def _fetch_member_ids(
    conn: sa.Connection, group_id: StationGroupId
) -> frozenset[StationId]:
    rows = conn.execute(
        sa.select(station_group_members.c.station_id).where(
            station_group_members.c.group_id == group_id
        )
    ).all()
    return frozenset(StationId(row[0]) for row in rows)


def _build_group(conn: sa.Connection, row: sa.engine.row.RowMapping) -> StationGroup:
    group_id = StationGroupId(row["id"])
    return StationGroup(
        id=group_id,
        name=row["name"],
        description=row["description"],
        created_at=utc_from_row(row["created_at"]),
        station_ids=_fetch_member_ids(conn, group_id),
    )
