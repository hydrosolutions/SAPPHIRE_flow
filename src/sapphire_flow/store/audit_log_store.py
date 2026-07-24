# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import audit_log

if TYPE_CHECKING:
    from sapphire_flow.types.auth import AuditEntry


class PgAuditLogStore:
    """Plan 147 Slice B: the append-only audit writer. Exposes ONLY an
    insert — no update/delete code path anywhere in this class, matching the
    role-independent DB guard (migration 0046).

    Deliberately takes no `transaction_factory` and does not open its own
    transaction: it executes on the SAME `sa.Connection` the caller passes
    in, so a caller that wraps a domain mutation store + this store in one
    externally-owned `conn.begin()` gets atomicity "for free" from
    SQLAlchemy's one-transaction-per-Connection semantics — no repo-wide
    connection refactor (see the plan's F17/F5 scope-cut)."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def append_entry(self, entry: AuditEntry) -> None:
        self._conn.execute(
            sa.insert(audit_log).values(
                event_type=entry.event_type.value,
                actor_id=entry.actor_id,
                actor_type=entry.actor_type.value,
                target_type=entry.target_type,
                target_id=entry.target_id,
                detail=entry.detail,
                ip_address=entry.ip_address,
                created_at=entry.created_at,
            )
        )
