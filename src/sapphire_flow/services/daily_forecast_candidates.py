from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.daily_model_contracts import validate_daily_model_contracts
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberState,
)
from sapphire_flow.types.enums import ModelArtifactStatus, ModelAssignmentStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.stores import ModelArtifactStore, StationGroupStore
    from sapphire_flow.types.daily_evaluation import DailyEvaluationSettings
    from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
    from sapphire_flow.types.ids import StationGroupId, StationId
    from sapphire_flow.types.model import ModelDataRequirements


def _identity_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (set, frozenset)):
        return sorted(
            cast("set[object] | frozenset[object]", value),
            key=lambda item: json.dumps(item, sort_keys=True, default=_identity_value),
        )
    raise TypeError(f"unsupported daily identity value: {type(value).__name__}")


def prepare_daily_candidates(
    *,
    settings: DailyEvaluationSettings,
    context: ScheduledDailyForecast,
    requirements: ModelDataRequirements,
    group_store: StationGroupStore,
    artifact_store: ModelArtifactStore,
    configuration_sha256: str,
    on_error: Callable[[StationGroupId, Exception], None] | None = None,
) -> dict[DailyClaimKey, FrozenDailyAssignment]:
    return {
        DailyClaimKey(
            tenant_id=settings.tenant_id,
            deployment=settings.deployment,
            group_id=group_id,
            model_id=settings.model_id,
            context=context,
        ): frozen
        for group_id, frozen in prepare_daily_assignments(
            settings=settings,
            requirements=requirements,
            group_store=group_store,
            artifact_store=artifact_store,
            configuration_sha256=configuration_sha256,
            on_error=on_error,
        ).items()
    }


def prepare_daily_assignments(
    *,
    settings: DailyEvaluationSettings,
    requirements: ModelDataRequirements,
    group_store: StationGroupStore,
    artifact_store: ModelArtifactStore,
    configuration_sha256: str,
    on_error: Callable[[StationGroupId, Exception], None] | None = None,
) -> dict[StationGroupId, FrozenDailyAssignment]:
    validate_daily_model_contracts(requirements, settings.output_contracts)
    if re.fullmatch(r"[0-9a-f]{64}", configuration_sha256) is None:
        raise ValueError("daily configuration requires a SHA256 fingerprint")
    candidates: dict[StationGroupId, FrozenDailyAssignment] = {}
    assigned_members: set[StationId] = set()
    for group_id in sorted(settings.group_ids, key=str):
        try:
            group = group_store.fetch_group(group_id)
            if group is None:
                raise ConfigurationError("configured daily group was not found")
            if group.tenant_id != settings.tenant_id:
                raise ConfigurationError(
                    "daily group must belong to the configured tenant"
                )
            assignments = sorted(
                (
                    assignment
                    for assignment in group_store.fetch_group_model_assignments(
                        group_id
                    )
                    if assignment.model_id == settings.model_id
                    and assignment.status is ModelAssignmentStatus.ACTIVE
                ),
                key=lambda assignment: (assignment.priority, assignment.time_step),
            )
            if not assignments:
                continue
            assignment = assignments[0]
            artifacts = artifact_store.fetch_artifacts_by_status(
                settings.model_id, ModelArtifactStatus.ACTIVE, group_id=group_id
            )
            artifact_id = (
                artifacts[0]
                if len(artifacts) == 1 and assignment.time_step == timedelta(days=1)
                else None
            )
            identity = json.dumps(
                {
                    "configuration_sha256": configuration_sha256,
                    "assignment": {
                        "group_id": assignment.group_id,
                        "model_id": assignment.model_id,
                        "time_step": assignment.time_step,
                        "status": assignment.status,
                        "priority": assignment.priority,
                    },
                    "requirements": asdict(requirements),
                },
                sort_keys=True,
                separators=(",", ":"),
                default=_identity_value,
            )
            candidates[group_id] = FrozenDailyAssignment(
                artifact_id=artifact_id,
                configuration_sha256=hashlib.sha256(identity.encode()).hexdigest(),
                output_contracts=settings.output_contracts,
                members=tuple(
                    DailyMemberOutcome(
                        station_id=station_id,
                        required_parameters=tuple(
                            sorted(requirements.target_parameters)
                        ),
                        state=DailyMemberState.PENDING,
                    )
                    for station_id in sorted(group.station_ids, key=str)
                ),
            )
        except Exception as exc:
            if on_error is None:
                raise
            on_error(group_id, exc)
    for candidate in candidates.values():
        members = {member.station_id for member in candidate.members}
        if assigned_members & members:
            raise ConfigurationError(
                "daily groups have overlapping station/model outputs"
            )
        assigned_members.update(members)
    return candidates
