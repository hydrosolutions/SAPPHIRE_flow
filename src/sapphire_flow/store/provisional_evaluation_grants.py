from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import provisional_evaluation_grants
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.provisional_evaluation import (
    EvaluationGrant,
    EvaluationScope,
    EvaluationSubject,
    EvaluationSubjectKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID


_ACTIVE_GRANTS = sa.table(
    "provisional_evaluation_active_grants",
    sa.column("id", sa.Uuid),
    sa.column("tenant_id", sa.Uuid),
    sa.column("station_id", sa.Uuid),
    sa.column("access_token_id", sa.Uuid),
    sa.column("human_user_id", sa.Uuid),
    schema="public",
)


class PgEvaluationGrantReader:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def find_active_grant(
        self, scope: EvaluationScope, subject: EvaluationSubject
    ) -> UUID | None:
        column = (
            _ACTIVE_GRANTS.c.access_token_id
            if subject.kind is EvaluationSubjectKind.SERVICE_TOKEN
            else _ACTIVE_GRANTS.c.human_user_id
        )
        return self._conn.execute(
            sa.select(_ACTIVE_GRANTS.c.id).where(
                _ACTIVE_GRANTS.c.tenant_id == scope.tenant_id,
                _ACTIVE_GRANTS.c.station_id == scope.station_id,
                column == subject.id,
            )
        ).scalar_one_or_none()


class PgEvaluationGrantProvisioner:
    """Maintenance connection only; caller owns the transaction."""

    def __init__(self, conn: sa.Connection, *, id_gen: Callable[[], UUID]) -> None:
        self._conn = conn
        self._id_gen = id_gen

    def grant(
        self, scope: EvaluationScope, subject: EvaluationSubject, *, operator: str
    ) -> EvaluationGrant:
        if not operator.strip():
            raise ValueError("evaluation grant requires operator attribution")
        row = (
            self._conn.execute(
                sa.insert(provisional_evaluation_grants)
                .values(
                    id=self._id_gen(),
                    tenant_id=scope.tenant_id,
                    station_id=scope.station_id,
                    access_token_id=subject.id
                    if subject.kind is EvaluationSubjectKind.SERVICE_TOKEN
                    else None,
                    human_user_id=subject.id
                    if subject.kind is EvaluationSubjectKind.HUMAN_USER
                    else None,
                    granted_by=operator,
                )
                .returning(provisional_evaluation_grants)
            )
            .mappings()
            .one()
        )
        return _grant_from_row(row)

    def revoke(self, grant_id: UUID, *, operator: str) -> EvaluationGrant | None:
        if not operator.strip():
            raise ValueError("evaluation revocation requires operator attribution")
        row = (
            self._conn.execute(
                sa.update(provisional_evaluation_grants)
                .where(
                    provisional_evaluation_grants.c.id == grant_id,
                    provisional_evaluation_grants.c.revoked_at.is_(None),
                )
                .values(revoked_at=sa.func.statement_timestamp(), revoked_by=operator)
                .returning(provisional_evaluation_grants)
            )
            .mappings()
            .one_or_none()
        )
        return _grant_from_row(row) if row is not None else None


def _grant_from_row(row: sa.RowMapping) -> EvaluationGrant:
    token_id = row["access_token_id"]
    return EvaluationGrant(
        id=row["id"],
        scope=EvaluationScope(
            tenant_id=TenantId(row["tenant_id"]),
            station_id=StationId(row["station_id"]),
        ),
        subject=EvaluationSubject(
            kind=EvaluationSubjectKind.SERVICE_TOKEN
            if token_id is not None
            else EvaluationSubjectKind.HUMAN_USER,
            id=token_id if token_id is not None else row["human_user_id"],
        ),
        granted_at=utc_from_row(row["granted_at"]),
        granted_by=row["granted_by"],
        revoked_at=utc_or_none(row["revoked_at"]),
        revoked_by=row["revoked_by"],
    )
