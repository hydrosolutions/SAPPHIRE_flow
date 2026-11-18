from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.cli import assign_group_model as agm
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ArtifactScope,
    AuditEventType,
    ModelAssignmentStatus,
)
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    ModelId,
    StationGroupId,
)
from sapphire_flow.types.model import ModelRecord
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeModelStore,
    FakeStationGroupStore,
    FakeTenantStore,
)

if TYPE_CHECKING:
    import pytest

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
MODEL = ModelId("cmal_small")
SCOPED = DeploymentIdentityConfig(
    writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
)
BASE = [
    "--tenant", DEFAULT_TENANT_CODE, "--group-name", "g1",
    "--model", "cmal_small", "--time-step-hours", "24",
]  # fmt: skip


class Rig:
    def __init__(self) -> None:
        self.groups = FakeStationGroupStore()
        self.models = FakeModelStore()
        self.audit = FakeAuditLogStore()
        self.group = StationGroup(
            id=StationGroupId(uuid4()),
            name="g1",
            station_ids=frozenset(),
            created_at=NOW,
            tenant_id=DEFAULT_TENANT_ID,
        )
        self.groups.store_group(self.group)

    def seed(self, status: ModelAssignmentStatus, hours: int = 24) -> None:
        self.groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=self.group.id,
                model_id=MODEL,
                time_step=timedelta(hours=hours),
                status=status,
                priority=1,
                created_at=NOW - timedelta(days=3),
            )
        )

    def run(
        self,
        extra: list[str] | None = None,
        *,
        discovered: frozenset[ModelId] = frozenset({MODEL}),
        identity: DeploymentIdentityConfig = SCOPED,
        base: list[str] | None = None,
    ) -> int:
        args = agm._build_parser().parse_args((base or BASE) + (extra or []))  # pyright: ignore[reportPrivateUsage]
        return agm.run_assign(
            args,
            identity=identity,
            tenants=FakeTenantStore(),
            groups=self.groups,
            models=self.models,
            audit=self.audit,
            discovered_models=discovered,
            clock=lambda: NOW,
        )

    def assignments(self) -> list[GroupModelAssignment]:
        return list(self.groups.fetch_group_model_assignments(self.group.id))


class TestAssignGroupModel:
    def test_dry_run_prints_change_and_writes_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig = Rig()
        assert rig.run() == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "cmal_small" in out and "'g1'" in out
        assert rig.assignments() == []
        assert rig.audit.entries == []

    def test_apply_writes_assignment_and_one_audit_row(self) -> None:
        rig = Rig()
        assert rig.run(["--apply"]) == 0
        (assignment,) = rig.assignments()
        assert assignment.status is ModelAssignmentStatus.ACTIVE
        assert (assignment.time_step, assignment.priority) == (timedelta(days=1), 1)
        (entry,) = rig.audit.entries
        assert entry.event_type is AuditEventType.MODEL_ASSIGNED

    def test_identical_active_rerun_is_noop_and_keeps_created_at(self) -> None:
        rig = Rig()
        rig.seed(ModelAssignmentStatus.ACTIVE)
        assert rig.run(["--apply"]) == 0
        assert rig.assignments()[0].created_at == NOW - timedelta(days=3)
        assert rig.audit.entries == []

    def test_inactive_assignment_is_refused(self) -> None:
        rig = Rig()
        rig.seed(ModelAssignmentStatus.INACTIVE)
        assert rig.run(["--apply"]) == 3
        assert rig.assignments()[0].status is ModelAssignmentStatus.INACTIVE

    def test_unknown_model_is_refused(self) -> None:
        rig = Rig()
        assert rig.run(["--apply"], discovered=frozenset()) == 3
        assert rig.assignments() == []

    def test_model_row_without_installed_class_is_accepted(self) -> None:
        rig = Rig()
        rig.models.register_model(
            ModelRecord(
                id=MODEL,
                display_name="x",
                artifact_scope=ArtifactScope.GROUP,
                description="",
                created_at=NOW,
            )
        )
        assert rig.run(["--apply"], discovered=frozenset()) == 0

    def test_unknown_group_is_refused(self) -> None:
        rig = Rig()
        base = [*BASE]
        base[3] = "nope"
        assert rig.run(base=base) == 3

    def test_tenant_not_writable_is_usage_error(self) -> None:
        rig = Rig()
        other = DeploymentIdentityConfig(
            writable_tenants=frozenset({"other"}), global_admin=False
        )
        assert rig.run(identity=other) == 2

    def test_fallback_priority_invariant_is_usage_error(self) -> None:
        rig = Rig()
        base = [*BASE]
        base[5] = str(CLIMATOLOGY_FALLBACK_MODEL_ID)
        code = rig.run(base=base, discovered=frozenset({CLIMATOLOGY_FALLBACK_MODEL_ID}))
        assert code == 2
        assert rig.assignments() == []
