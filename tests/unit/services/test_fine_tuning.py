from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sapphire_flow.config.fine_tuning import parse_fine_tuning
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.fine_tuning import qualify_fine_tuning_scope
from sapphire_flow.types.enums import ModelArtifactStatus, ModelAssignmentStatus
from sapphire_flow.types.ids import ModelId, PrincipalId, TenantId
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeModelArtifactStore,
    FakeStationGroupStore,
    FakeStationStore,
)
from tests.unit.config.test_fine_tuning import request_document


class TestQualifyFineTuningScope:
    @pytest.mark.parametrize(
        "change",
        [
            "none",
            "foreign_tenant",
            "empty",
            "unassigned",
            "foreign_member",
            "wrong_model",
            "rejected_donor",
            "unsupported",
        ],
    )
    def test_scope_is_explicit_and_authorized(self, change: str) -> None:
        request = parse_fine_tuning(request_document())
        station = make_station_config()
        stations = FakeStationStore()
        stations.store_station(station)
        group = StationGroup(
            id=request.group_id,
            name="test",
            station_ids=frozenset({station.id}),
            created_at=request.fitting_start,
            tenant_id=station.tenant_id,
        )
        if change == "empty":
            group = replace(group, station_ids=frozenset())
        if change == "foreign_member":
            stations.store_station(replace(station, tenant_id=TenantId(uuid4())))
        groups = FakeStationGroupStore()
        groups.store_group(group)
        if change != "unassigned":
            groups.store_group_model_assignment(
                GroupModelAssignment(
                    group_id=group.id,
                    model_id=request.model_id,
                    time_step=timedelta(days=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=1,
                    created_at=request.fitting_start,
                )
            )
        artifacts = FakeModelArtifactStore(groups)
        stored = artifacts.store_artifact(
            ModelId("other") if change == "wrong_model" else request.model_id,
            b"donor",
            request.fitting_start,
            request.fitting_end,
            request.fitting_end,
            group_id=group.id,
            status=ModelArtifactStatus.REJECTED
            if change == "rejected_donor"
            else ModelArtifactStatus.ACTIVE,
        )
        request = replace(request, base_artifact_id=stored.artifact_id)
        principal = WritePrincipal(
            id=PrincipalId("admin"),
            tenant_id=TenantId(uuid4())
            if change == "foreign_tenant"
            else group.tenant_id,
        )
        model = SimpleNamespace(supports_warm_start=change != "unsupported")
        if change == "none":
            result = qualify_fine_tuning_scope(
                request,
                principal=principal,
                groups=groups,
                stations=stations,
                artifacts=artifacts,
                model=model,
            )
            assert result.group.station_ids == group.station_ids
            assert result.donor.id == request.base_artifact_id
        else:
            with pytest.raises(ConfigurationError, match="fine-tuning|warm-start"):
                qualify_fine_tuning_scope(
                    request,
                    principal=principal,
                    groups=groups,
                    stations=stations,
                    artifacts=artifacts,
                    model=model,
                )
        assert artifacts.fetch_artifact_record(stored.artifact_id).status == (
            ModelArtifactStatus.REJECTED
            if change == "rejected_donor"
            else ModelArtifactStatus.ACTIVE
        )
