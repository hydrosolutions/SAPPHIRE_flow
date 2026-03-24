# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, station_group_members
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.model import ModelArtifactRecord

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgModelArtifactStore:
    def __init__(self, conn: sa.Connection, artifact_dir: Path) -> None:
        self._conn = conn
        self._artifact_dir = artifact_dir

    def store_artifact(
        self,
        model_id: ModelId,
        artifact_bytes: bytes,
        training_period_start: UtcDatetime,
        training_period_end: UtcDatetime,
        trained_at: UtcDatetime,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> ArtifactId:
        aid = ArtifactId(uuid4())
        artifact_path = self._artifact_dir / model_id / f"{aid}.bin"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(artifact_bytes)

        self._conn.execute(
            sa.insert(model_artifacts).values(
                id=aid,
                model_id=model_id,
                station_id=station_id,
                group_id=group_id,
                status=ModelArtifactStatus.ACTIVE.value,
                artifact_path=str(artifact_path),
                training_period_start=training_period_start,
                training_period_end=training_period_end,
                trained_at=trained_at,
                promoted_at=trained_at,
                promoted_by=None,
                superseded_at=None,
            )
        )
        return aid

    def fetch_artifact(
        self, artifact_id: ArtifactId
    ) -> tuple[ArtifactId, bytes] | None:
        row = (
            self._conn.execute(
                sa.select(model_artifacts.c.artifact_path).where(
                    model_artifacts.c.id == artifact_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return (artifact_id, Path(row["artifact_path"]).read_bytes())

    def fetch_active_artifact(
        self,
        model_id: ModelId,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> tuple[ArtifactId, bytes] | None:
        conditions = [
            model_artifacts.c.model_id == model_id,
            model_artifacts.c.status == ModelArtifactStatus.ACTIVE.value,
        ]
        if station_id is not None:
            conditions.append(model_artifacts.c.station_id == station_id)
        if group_id is not None:
            conditions.append(model_artifacts.c.group_id == group_id)

        row = (
            self._conn.execute(
                sa.select(model_artifacts.c.id, model_artifacts.c.artifact_path).where(
                    sa.and_(*conditions)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        aid = ArtifactId(row["id"])
        return (aid, Path(row["artifact_path"]).read_bytes())

    def fetch_active_artifact_for_station(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[ArtifactId, bytes] | None:
        # Branch 1 (priority 0): direct station-scoped match
        direct = sa.select(
            model_artifacts.c.id,
            model_artifacts.c.artifact_path,
            sa.literal(0).label("priority"),
        ).where(
            sa.and_(
                model_artifacts.c.station_id == station_id,
                model_artifacts.c.model_id == model_id,
                model_artifacts.c.status == ModelArtifactStatus.ACTIVE.value,
            )
        )

        # Branch 2 (priority 1): group-scoped match via group membership
        group_subq = sa.select(station_group_members.c.group_id).where(
            station_group_members.c.station_id == station_id
        )
        via_group = sa.select(
            model_artifacts.c.id,
            model_artifacts.c.artifact_path,
            sa.literal(1).label("priority"),
        ).where(
            sa.and_(
                model_artifacts.c.group_id.in_(group_subq),
                model_artifacts.c.model_id == model_id,
                model_artifacts.c.status == ModelArtifactStatus.ACTIVE.value,
            )
        )

        union_stmt = sa.union_all(direct, via_group).subquery()
        stmt = (
            sa.select(union_stmt.c.id, union_stmt.c.artifact_path)
            .order_by(union_stmt.c.priority)
            .limit(1)
        )

        row = self._conn.execute(stmt).mappings().one_or_none()
        if row is None:
            return None
        aid = ArtifactId(row["id"])
        return (aid, Path(row["artifact_path"]).read_bytes())

    def fetch_artifact_record(
        self, artifact_id: ArtifactId
    ) -> ModelArtifactRecord | None:
        row = (
            self._conn.execute(
                sa.select(model_artifacts).where(model_artifacts.c.id == artifact_id)
            )
            .mappings()
            .one_or_none()
        )
        return _row_to_record(row) if row is not None else None

    def fetch_artifacts_by_status(
        self,
        model_id: ModelId,
        status: ModelArtifactStatus,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> list[ArtifactId]:
        conditions = [
            model_artifacts.c.model_id == model_id,
            model_artifacts.c.status == status.value,
        ]
        if station_id is not None:
            conditions.append(model_artifacts.c.station_id == station_id)
        if group_id is not None:
            conditions.append(model_artifacts.c.group_id == group_id)

        rows = self._conn.execute(
            sa.select(model_artifacts.c.id).where(sa.and_(*conditions))
        ).all()
        return [ArtifactId(row[0]) for row in rows]

    def transition_artifact_status(
        self,
        artifact_id: ArtifactId,
        new_status: ModelArtifactStatus,
        promoted_by: UUID | None = None,
    ) -> None:
        now = sa.func.now()
        updates: dict[str, object] = {"status": new_status.value}

        if new_status == ModelArtifactStatus.ACTIVE:
            updates["promoted_at"] = now
            updates["promoted_by"] = promoted_by
        elif new_status == ModelArtifactStatus.SUPERSEDED:
            updates["superseded_at"] = now

        self._conn.execute(
            sa.update(model_artifacts)
            .where(model_artifacts.c.id == artifact_id)
            .values(**updates)
        )


def _row_to_record(row: sa.engine.row.RowMapping) -> ModelArtifactRecord:
    return ModelArtifactRecord(
        id=ArtifactId(row["id"]),
        model_id=ModelId(row["model_id"]),
        station_id=(
            StationId(row["station_id"]) if row["station_id"] is not None else None
        ),
        group_id=(
            StationGroupId(row["group_id"]) if row["group_id"] is not None else None
        ),
        status=ModelArtifactStatus(row["status"]),
        artifact_path=row["artifact_path"],
        training_period_start=utc_from_row(row["training_period_start"]),
        training_period_end=utc_from_row(row["training_period_end"]),
        trained_at=utc_from_row(row["trained_at"]),
        promoted_at=utc_or_none(row["promoted_at"]),
        promoted_by=row["promoted_by"],
        superseded_at=utc_or_none(row["superseded_at"]),
        created_at=utc_from_row(row["created_at"]),
    )
