from __future__ import annotations

import math
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import pytest
import sqlalchemy as sa

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.db.metadata import skill_diagrams, skill_generations, skill_scores
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.services.skill.service import compute_generation_fingerprint

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import SkillStore
    from sapphire_flow.store.skill_store import PgSkillStore
    from sapphire_flow.types.skill import SkillDiagram, SkillScore
    from tests.integration.services.skill_persistence_fixture import (
        SkillPersistenceCase,
    )

pytest_plugins = ("tests.integration.services.skill_persistence_fixture",)


class TestSkillPersistence:
    @pytest.mark.parametrize("generation_mode", ["OFF", "ON"])
    def test_threshold_task_persists_complete_results(
        self,
        skill_persistence_case: SkillPersistenceCase,
        generation_mode: Literal["OFF", "ON"],
    ) -> None:
        from tests.integration.services.skill_persistence_fixture import (
            AID,
            MID,
            RUN,
            SID,
            C,
        )

        case = skill_persistence_case
        enabled = generation_mode == "ON"

        def run_task() -> tuple[list[SkillScore], list[SkillDiagram]]:
            return compute_skills_task.fn(
                station_id=SID,
                model_id=MID,
                artifact_id=AID,
                parameter="discharge",
                hindcast_run_id=RUN,
                hindcast_store=case.hindcast_store,
                obs_store=case.obs_store,
                skill_store=case.skill_store,
                station_store=case.station_store,
                flow_regime_store=case.flow_regime_store,
                deployment_config=DeploymentConfig(
                    max_retention_days=2192, enable_skill_generations=enabled
                ),
                clock=lambda: C,
                generation_id=UUID(int=65001),
            )

        # No model, flow engine, serializer patch or pre-store payload conversion.
        scores, diagrams = run_task()

        assert len(scores) == 13
        assert len(diagrams) == 3
        computed: dict[str, dict[str, Any]] = {
            diagram.diagram_type: asdict(diagram)["data"] for diagram in diagrams
        }
        assert computed["rank_histogram"]["counts"] == [2, 0, 0]
        assert (
            sum(math.isnan(value) for value in computed["reliability"]["observed_freq"])
            == 8
        )
        assert computed["roc"]["n_events"] == 1
        assert computed["roc"]["n_non_events"] == 1
        fetched_scores = case.skill_store.fetch_latest_scores(
            SID, MID, parameter="discharge"
        )
        fetched_diagrams = case.skill_store.fetch_latest_diagrams(
            SID, MID, parameter="discharge"
        )
        assert compute_generation_fingerprint(
            fetched_scores, fetched_diagrams
        ) == compute_generation_fingerprint(scores, diagrams)
        assert len(fetched_scores) == 13
        assert len(fetched_diagrams) == 3
        raw = case.connection.execute(
            sa.select(skill_diagrams.c.data).where(
                skill_diagrams.c.diagram_type == "reliability"
            )
        ).scalar_one()
        assert raw["sample_counts"] == [1, 0, 0, 0, 0, 0, 0, 0, 0, 1]
        assert raw["observed_freq"] == [
            0.0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1.0,
        ]
        ledger = case.connection.execute(sa.select(skill_generations)).mappings().all()
        if enabled:
            assert len(ledger) == 1
            assert ledger[0]["score_count"] == 13
            assert ledger[0]["diagram_count"] == 3
            assert {row.generation_id for row in scores + diagrams} == {ledger[0]["id"]}
        else:
            assert not ledger
            assert all(row.generation_id is None for row in scores + diagrams)
        # ON: explicit same-ID/content replay, not a new flow invocation.
        # OFF: unchanged-input first-write-wins only, never a healing claim.
        run_task()
        assert (
            case.connection.scalar(sa.select(sa.func.count()).select_from(skill_scores))
            == 13
        )
        assert (
            case.connection.scalar(
                sa.select(sa.func.count()).select_from(skill_diagrams)
            )
            == 3
        )

    def test_on_diagram_failure_cannot_publish(
        self, skill_persistence_case: SkillPersistenceCase
    ) -> None:
        from sapphire_flow.exceptions import SkillDiagramEncodingError
        from tests.integration.services.skill_persistence_fixture import (
            AID,
            MID,
            RUN,
            SID,
            C,
        )

        class FailingDiagramStore:
            def __init__(self, delegate: PgSkillStore) -> None:
                self.delegate = delegate

            def store_skill_scores(self, scores: list[SkillScore]) -> int:
                return self.delegate.store_skill_scores(scores)

            def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> int:
                raise SkillDiagramEncodingError("injected diagram boundary failure")

            def __getattr__(self, name: str) -> object:
                return getattr(self.delegate, name)

        case = skill_persistence_case
        with pytest.raises(SkillDiagramEncodingError, match="injected diagram"):
            compute_skills_task.fn(
                station_id=SID,
                model_id=MID,
                artifact_id=AID,
                parameter="discharge",
                hindcast_run_id=RUN,
                hindcast_store=case.hindcast_store,
                obs_store=case.obs_store,
                skill_store=cast("SkillStore", FailingDiagramStore(case.skill_store)),
                station_store=case.station_store,
                flow_regime_store=case.flow_regime_store,
                deployment_config=DeploymentConfig(
                    max_retention_days=2192, enable_skill_generations=True
                ),
                clock=lambda: C,
                generation_id=UUID(int=65001),
            )
        assert (
            case.connection.scalar(sa.select(sa.func.count()).select_from(skill_scores))
            == 13
        )
        assert (
            case.connection.scalar(
                sa.select(sa.func.count()).select_from(skill_generations)
            )
            == 0
        )
        assert case.skill_store.fetch_latest_scores(SID, MID) == []
        assert case.skill_store.fetch_latest_diagrams(SID, MID) == []
