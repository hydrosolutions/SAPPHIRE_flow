from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.training import supports_warm_start
from sapphire_flow.types.enums import ModelArtifactStatus

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import (
        ModelArtifactStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.fine_tuning import FineTuningRequest
    from sapphire_flow.types.model import ModelArtifactRecord
    from sapphire_flow.types.station import StationGroup
    from sapphire_flow.types.write_principal import WritePrincipal


@dataclass(frozen=True, kw_only=True, slots=True)
class FineTuningScope:
    group: StationGroup
    donor: ModelArtifactRecord


def qualify_fine_tuning_scope(
    request: FineTuningRequest,
    *,
    principal: WritePrincipal,
    groups: StationGroupStore,
    stations: StationStore,
    artifacts: ModelArtifactStore,
    model: object,
) -> FineTuningScope:
    if principal.tenant_id is None:
        raise ConfigurationError("fine-tuning requires an explicit authorized tenant")
    group = groups.fetch_group(request.group_id)
    if group is None or not group.station_ids:
        raise ConfigurationError("fine-tuning group is unknown or empty")
    if group.tenant_id != principal.tenant_id:
        raise ConfigurationError("fine-tuning group is outside the authorized tenant")
    assigned = groups.fetch_groups_for_model(request.model_id)
    if not any(candidate.id == group.id for candidate in assigned):
        raise ConfigurationError(
            "fine-tuning requires an existing model-group assignment"
        )
    for station_id in group.station_ids:
        station = stations.fetch_station(station_id)
        if station is None or station.tenant_id != group.tenant_id:
            raise ConfigurationError(
                "fine-tuning group has missing or foreign-tenant members"
            )
    donor = artifacts.fetch_artifact_record(request.base_artifact_id)
    if donor is None:
        raise ConfigurationError("fine-tuning donor artifact is unknown")
    if donor.model_id != request.model_id or donor.group_id != group.id:
        raise ConfigurationError(
            "fine-tuning donor must match the requested model and group"
        )
    if donor.status not in (ModelArtifactStatus.ACTIVE, ModelArtifactStatus.SUPERSEDED):
        raise ConfigurationError("fine-tuning donor is not an approved artifact")
    if not supports_warm_start(model):
        raise ConfigurationError(
            "selected model does not support warm-start retraining"
        )
    # Reading through the artifact store verifies its retained bytes before fit.
    if artifacts.fetch_artifact(request.base_artifact_id) is None:
        raise ConfigurationError("fine-tuning donor bytes are unavailable")
    return FineTuningScope(group=group, donor=donor)
