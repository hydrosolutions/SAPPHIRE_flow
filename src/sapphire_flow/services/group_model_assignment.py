"""Plan 540 M5-assign: plan and apply a station-group model assignment.

The plan reads only; the apply calls ``create_group_assignment`` (which writes the
assignment and its ``MODEL_ASSIGNED`` audit row) and only when the plan says CREATE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.model_onboarding import (
    _assert_assignment_priority_invariant,  # pyright: ignore[reportPrivateUsage]
    create_group_assignment,
)
from sapphire_flow.services.write_principal import enforce_tenant_isolation
from sapphire_flow.types.enums import AuditEventType, ModelAssignmentStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ModelStore,
        StationGroupStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationGroupId, TenantId
    from sapphire_flow.types.write_principal import WritePrincipal


class AssignmentRefusedError(ConfigurationError):
    """The assignment cannot be made as asked (a refusal, not a usage error)."""


class AssignmentAction(Enum):
    CREATE = auto()
    UPDATE = auto()
    NOOP = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentPlan:
    group_id: StationGroupId
    group_name: str
    tenant_id: TenantId
    model_id: ModelId
    time_step: timedelta
    priority: int
    action: AssignmentAction


def plan_group_assignment(
    *,
    tenant_id: TenantId,
    group_name: str,
    model_id: ModelId,
    time_step: timedelta,
    priority: int,
    groups: StationGroupStore,
    models: ModelStore,
    discovered_models: Collection[ModelId],
    principal: WritePrincipal,
    clock: Callable[[], UtcDatetime],
) -> AssignmentPlan:
    if time_step <= timedelta(0):
        raise ConfigurationError("time step must be positive")
    _assert_assignment_priority_invariant(model_id, priority)
    group = groups.fetch_group_by_name(tenant_id, group_name)
    if group is None:
        raise AssignmentRefusedError(
            f"no group {group_name!r} in this tenant (create it first)"
        )
    enforce_tenant_isolation(
        principal=principal,
        target_tenant_id=group.tenant_id,
        audit_log_store=None,
        event_type=AuditEventType.MODEL_ASSIGNED,
        target_type="station_group",
        target_id=str(group.id),
        detail={"model_id": str(model_id)},
        now=clock(),
    )
    if model_id not in discovered_models and models.fetch_model(model_id) is None:
        raise AssignmentRefusedError(
            f"unknown model {model_id!r}: not installed and no models row "
            "(import the model artifact first)"
        )
    existing = next(
        (
            a
            for a in groups.fetch_group_model_assignments(group.id)
            if a.model_id == model_id
        ),
        None,
    )
    action = AssignmentAction.CREATE
    if existing is not None:
        if existing.status is ModelAssignmentStatus.INACTIVE:
            raise AssignmentRefusedError(
                f"model {model_id!r} has an INACTIVE assignment on this group; "
                "reactivating is not done by this command"
            )
        same = (existing.time_step, existing.priority) == (time_step, priority)
        action = AssignmentAction.NOOP if same else AssignmentAction.UPDATE
    return AssignmentPlan(
        group_id=group.id,
        group_name=group.name,
        tenant_id=tenant_id,
        model_id=model_id,
        time_step=time_step,
        priority=priority,
        action=action,
    )


def apply_group_assignment(
    plan: AssignmentPlan,
    *,
    groups: StationGroupStore,
    audit: AuditLogStore,
    principal: WritePrincipal,
    clock: Callable[[], UtcDatetime],
) -> None:
    if plan.action is AssignmentAction.NOOP:
        return
    create_group_assignment(
        plan.group_id,
        plan.model_id,
        plan.time_step,
        plan.priority,
        groups,
        clock,
        principal=principal,
        audit_log_store=audit,
    )
