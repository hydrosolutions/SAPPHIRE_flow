from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.cli import assign_group_model as agm
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ArtifactScope, ModelAssignmentStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, TenantId
from sapphire_flow.types.model import ModelRecord
from sapphire_flow.types.station import StationGroup

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
MODEL = ModelId("cmal_small")
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"sandbox"}), global_admin=False
)
ARGS = [
    "--tenant", "sandbox", "--group-name", "g1", "--model", "cmal_small",
    "--time-step-hours", "24", "--apply",
]  # fmt: skip


def _run(conn: sa.Connection, args: list[str]) -> int:
    return agm.run_assign(
        agm._build_parser().parse_args(args),  # pyright: ignore[reportPrivateUsage]
        identity=IDENTITY,
        tenants=PgTenantStore(conn),
        groups=PgStationGroupStore(conn, transaction_factory=lambda: nullcontext(conn)),
        models=PgModelStore(conn),
        audit=PgAuditLogStore(conn),
        clock=lambda: NOW,
    )


def _rows(conn: sa.Connection, table: str) -> int:
    return int(conn.scalar(sa.text(f"SELECT count(*) FROM {table}")) or 0)  # noqa: S608


class TestAssignGroupModelPg:
    def test_assign_noop_inactive_unknown_model_and_tenant(
        self, db_connection: sa.Connection
    ) -> None:
        tenants = PgTenantStore(db_connection)
        tenant_id = TenantId(uuid4())
        tenants.ensure_tenant(tenant_id=tenant_id, code="sandbox", name="Sandbox")
        tenants.ensure_tenant(tenant_id=TenantId(uuid4()), code="other", name="Other")
        groups = PgStationGroupStore(
            db_connection, transaction_factory=lambda: nullcontext(db_connection)
        )
        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="g1",
            station_ids=frozenset(),
            created_at=NOW,
            tenant_id=tenant_id,
        )
        groups.store_group(group)
        PgModelStore(db_connection).register_model(
            ModelRecord(
                id=MODEL,
                display_name="cmal",
                artifact_scope=ArtifactScope.GROUP,
                description="",
                created_at=NOW,
            )
        )

        assert _run(db_connection, ARGS) == 0
        (assignment,) = groups.fetch_group_model_assignments(group.id)
        assert assignment.status is ModelAssignmentStatus.ACTIVE
        assert _rows(db_connection, "audit_log") == 1

        assert _run(db_connection, ARGS) == 0  # identical re-run: no-op
        assert _rows(db_connection, "audit_log") == 1

        db_connection.execute(
            sa.text("UPDATE group_model_assignments SET status = 'inactive'")
        )
        assert _run(db_connection, ARGS) == 3

        unknown = [*ARGS]
        unknown[unknown.index("cmal_small")] = "no_such_model"
        assert _run(db_connection, unknown) == 3

        other_tenant = [*ARGS]
        other_tenant[1] = "other"
        assert _run(db_connection, other_tenant) == 2
        assert _rows(db_connection, "audit_log") == 1
