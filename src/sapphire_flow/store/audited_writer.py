"""Plan 147 Slice E — the real (non-AUTOCOMMIT) transaction seam for discrete
audited writes.

`setup_production_stores` hands every flow an AUTOCOMMIT connection, so a
domain mutation commits the instant it runs — a later `audit_log` INSERT
failure could NOT roll it back. The `AuditedWriter` here owns a fresh REAL
transaction per discrete audited write: it builds the mutation store(s) and
`PgAuditLogStore` on ONE transactional connection, so `mutate(); append_entry()`
commit or roll back together (Slice B atomicity, mirroring the Slice C CLI's
`engine.begin()` block). Absent (`None`) for caller-injected fake stores, which
keep the direct AUTOCOMMIT path unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa  # noqa: TCH002 — used at runtime by make_audited_writer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager


def make_audited_stores(conn: sa.Connection) -> dict[str, object]:
    """Build the mutation + audit stores a discrete audited write needs, all
    on ONE caller-owned connection. When `conn` is inside a real (non-
    AUTOCOMMIT) transaction, the mutation store(s) and the `audit_log` INSERT
    share that transaction — a failed audit insert rolls the domain write back
    with it (Plan 147 Slice E success-path atomicity)."""
    from sapphire_flow.config.paths import resolve_artifact_dir
    from sapphire_flow.store.audit_log_store import PgAuditLogStore
    from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore

    artifact_dir = resolve_artifact_dir()
    return {
        "artifact_store": PgModelArtifactStore(conn, artifact_dir),
        "station_store": PgStationStore(conn),
        "group_store": PgStationGroupStore(conn),
        "audit_log_store": PgAuditLogStore(conn),
    }


@dataclass(frozen=True, slots=True)
class AuditedWriter:
    """Owns the `engine.begin()` seam for a Slice-E discrete audited write.
    `transaction()` yields the mutation + audit stores bound to one real
    transactional connection, so the caller's mutation and its audit INSERT
    are atomic. Constructed only on the production DB-backed path (see
    `make_audited_writer`)."""

    begin: Callable[[], AbstractContextManager[sa.Connection]]

    @contextmanager
    def transaction(self) -> Iterator[dict[str, object]]:
        with self.begin() as conn:
            yield make_audited_stores(conn)


def make_audited_writer(conn: object) -> AuditedWriter:
    """Build the production `AuditedWriter` from the AUTOCOMMIT store
    connection returned by `setup_production_stores`: its shared `.engine`
    opens fresh REAL (non-AUTOCOMMIT) transactions via `engine.begin()`."""
    return AuditedWriter(begin=cast("sa.Connection", conn).engine.begin)
