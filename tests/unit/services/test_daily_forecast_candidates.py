from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.daily_forecast_candidates import prepare_daily_candidates
from sapphire_flow.types.daily_evaluation import DailyEvaluationSettings
from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
from sapphire_flow.types.daily_output import DailyOutputContract
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus, ModelAssignmentStatus
from sapphire_flow.types.ids import StationGroupId, StationId, TenantId
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from tests.fakes.fake_stores import FakeModelArtifactStore, FakeStationGroupStore
from tests.unit.services.test_daily_model_contracts import (
    declaration as declaration,  # noqa: F401
)

Configured = tuple[
    DailyEvaluationSettings,
    ModelDataRequirements,
    FakeStationGroupStore,
    FakeModelArtifactStore,
]

NOW = ensure_utc(datetime(2026, 10, 26, tzinfo=UTC))


class TestPrepareDailyCandidates:
    @pytest.fixture
    def configured(
        self, declaration: tuple[ModelDataRequirements, DailyOutputContract]
    ) -> tuple[
        DailyEvaluationSettings,
        ModelDataRequirements,
        FakeStationGroupStore,
        FakeModelArtifactStore,
    ]:
        requirements, contract = declaration
        group_id = StationGroupId(uuid4())
        settings = DailyEvaluationSettings(
            tenant_id=TenantId(uuid4()),
            deployment="test",
            prefect_deployment_id=uuid4(),
            group_ids=frozenset({group_id}),
            output_contracts=(contract,),
        )
        groups = FakeStationGroupStore()
        groups.store_group(
            StationGroup(
                id=group_id,
                name="test",
                tenant_id=settings.tenant_id,
                station_ids=frozenset({StationId(uuid4()), StationId(uuid4())}),
                created_at=NOW,
            )
        )
        groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group_id,
                model_id=settings.model_id,
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=10,
                created_at=NOW,
            )
        )
        return settings, requirements, groups, FakeModelArtifactStore()

    def test_missing_artifact_keeps_full_roster(
        self,
        configured: tuple[
            DailyEvaluationSettings,
            ModelDataRequirements,
            FakeStationGroupStore,
            FakeModelArtifactStore,
        ],
    ) -> None:
        settings, requirements, groups, artifacts = configured
        result = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=ScheduledDailyForecast(scheduled_tick=NOW),
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        assert len(result) == 1
        frozen = next(iter(result.values()))
        assert frozen.artifact_id is None
        group = groups.fetch_group(next(iter(settings.group_ids)))
        assert group is not None
        assert {member.station_id for member in frozen.members} == group.station_ids

    def test_foreign_tenant_group_is_rejected(
        self,
        configured: tuple[
            DailyEvaluationSettings,
            ModelDataRequirements,
            FakeStationGroupStore,
            FakeModelArtifactStore,
        ],
    ) -> None:
        settings, requirements, groups, artifacts = configured
        group = groups.fetch_group(next(iter(settings.group_ids)))
        assert group is not None
        groups.store_group(replace(group, tenant_id=TenantId(uuid4())))
        with pytest.raises(ConfigurationError, match="tenant"):
            prepare_daily_candidates(
                settings=settings,
                requirements=requirements,
                context=ScheduledDailyForecast(scheduled_tick=NOW),
                group_store=groups,
                artifact_store=artifacts,
                configuration_sha256="a" * 64,
            )

    def test_assignment_priority_changes_fingerprint(
        self,
        configured: tuple[
            DailyEvaluationSettings,
            ModelDataRequirements,
            FakeStationGroupStore,
            FakeModelArtifactStore,
        ],
    ) -> None:
        settings, requirements, groups, artifacts = configured
        context = ScheduledDailyForecast(scheduled_tick=NOW)
        first = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=context,
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        assignment = groups.fetch_group_model_assignments(
            next(iter(settings.group_ids))
        )[0]
        groups.store_group_model_assignment(replace(assignment, priority=20))
        second = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=context,
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        assert (
            next(iter(first.values())).configuration_sha256
            != next(iter(second.values())).configuration_sha256
        )

    def test_assignment_reregistration_preserves_fingerprint(
        self,
        configured: tuple[
            DailyEvaluationSettings,
            ModelDataRequirements,
            FakeStationGroupStore,
            FakeModelArtifactStore,
        ],
    ) -> None:
        settings, requirements, groups, artifacts = configured
        context = ScheduledDailyForecast(scheduled_tick=NOW)
        first = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=context,
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        assignment = groups.fetch_group_model_assignments(
            next(iter(settings.group_ids))
        )[0]
        groups.store_group_model_assignment(
            replace(assignment, created_at=NOW + timedelta(hours=1))
        )
        second = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=context,
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        assert (
            next(iter(first.values())).configuration_sha256
            == next(iter(second.values())).configuration_sha256
        )

    def test_roster_change_does_not_change_configuration_fingerprint(
        self,
        configured: tuple[
            DailyEvaluationSettings,
            ModelDataRequirements,
            FakeStationGroupStore,
            FakeModelArtifactStore,
        ],
    ) -> None:
        settings, requirements, groups, artifacts = configured
        context = ScheduledDailyForecast(scheduled_tick=NOW)
        first = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=context,
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        groups.add_station_to_group(next(iter(settings.group_ids)), StationId(uuid4()))
        second = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=context,
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        assert (
            next(iter(first.values())).configuration_sha256
            == next(iter(second.values())).configuration_sha256
        )

    @pytest.mark.parametrize("case", ["active", "ambiguous", "subdaily", "inactive"])
    def test_assignment_and_artifact_eligibility(
        self, configured: Configured, case: str
    ) -> None:
        settings, requirements, groups, artifacts = configured
        group_id = next(iter(settings.group_ids))
        stored = artifacts.store_artifact(
            settings.model_id,
            b"fixture",
            NOW,
            NOW,
            NOW,
            group_id=group_id,
            status=ModelArtifactStatus.ACTIVE,
        )
        assignment = groups.fetch_group_model_assignments(group_id)[0]
        if case == "ambiguous":
            artifacts.store_artifact(
                settings.model_id,
                b"other",
                NOW,
                NOW,
                NOW,
                group_id=group_id,
                status=ModelArtifactStatus.ACTIVE,
            )
        elif case == "subdaily":
            groups.store_group_model_assignment(
                replace(assignment, time_step=timedelta(hours=6))
            )
        elif case == "inactive":
            groups.store_group_model_assignment(
                replace(assignment, status=ModelAssignmentStatus.INACTIVE)
            )
        result = prepare_daily_candidates(
            settings=settings,
            requirements=requirements,
            context=ScheduledDailyForecast(scheduled_tick=NOW),
            group_store=groups,
            artifact_store=artifacts,
            configuration_sha256="a" * 64,
        )
        if case == "inactive":
            assert result == {}
        else:
            assert next(iter(result.values())).artifact_id == (
                stored[0] if case == "active" else None
            )

    def test_overlapping_groups_are_rejected(self, configured: Configured) -> None:
        settings, requirements, groups, artifacts = configured
        group_id = next(iter(settings.group_ids))
        group = groups.fetch_group(group_id)
        assert group is not None
        other_id = StationGroupId(uuid4())
        groups.store_group(replace(group, id=other_id, name="overlap"))
        groups.store_group_model_assignment(
            replace(
                groups.fetch_group_model_assignments(group_id)[0], group_id=other_id
            )
        )
        settings = replace(settings, group_ids=settings.group_ids | {other_id})
        with pytest.raises(ConfigurationError, match="overlapping"):
            prepare_daily_candidates(
                settings=settings,
                requirements=requirements,
                context=ScheduledDailyForecast(scheduled_tick=NOW),
                group_store=groups,
                artifact_store=artifacts,
                configuration_sha256="a" * 64,
            )
