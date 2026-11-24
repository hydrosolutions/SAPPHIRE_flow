from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sapphire_flow.exceptions import ArtifactIntegrityError
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.flows.run_hindcast import run_hindcast_flow
from sapphire_flow.services.fine_tuning_evidence import canonical_json
from sapphire_flow.services.hindcast import resolve_hindcast_issue_batch_size
from sapphire_flow.types.training import TrainingResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sapphire_flow.protocols.forecast_model import GroupForecastModel
    from sapphire_flow.protocols.stores import ModelArtifactStore, SkillStore
    from sapphire_flow.services.fine_tuning_evidence import (
        CandidateReceipt,
        RecordingReanalysisSource,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId
    from sapphire_flow.types.training import HindcastStepResult, TrainingUnit


def evaluate_candidate(
    receipt: CandidateReceipt,
    *,
    unit: TrainingUnit,
    candidate_run_id: UUID,
    donor_run_id: UUID,
    model: GroupForecastModel,
    artifacts: ModelArtifactStore,
    source: RecordingReanalysisSource,
    obs_store: object,
    hindcast_store: object,
    station_store: object,
    group_store: object,
    basin_store: object,
    skill_store: SkillStore,
    flow_regime_store: object,
    deployment_config: object,
    clock: Callable[[], UtcDatetime],
) -> tuple[CandidateReceipt, TrainingResult]:
    request = receipt.request
    issue_batch_size = resolve_hindcast_issue_batch_size(model)

    def evaluate(
        artifact_id: ArtifactId,
        run_id: UUID,
    ) -> tuple[dict[str, Any], list[HindcastStepResult]]:
        retained = artifacts.fetch_artifact(artifact_id)
        if retained is None:
            raise ValueError(f"evaluation artifact {artifact_id} is unavailable")
        expected_hash = (
            receipt.artifact_sha256
            if artifact_id == receipt.artifact_id
            else receipt.donor_sha256
        )
        if hashlib.sha256(retained[1]).hexdigest() != expected_hash:
            raise ArtifactIntegrityError(
                f"evaluation artifact {artifact_id} differs from its receipt"
            )
        source.reset()
        steps_by_station = run_hindcast_flow(
            model_id=request.model_id,
            artifact_id=artifact_id,
            group_id=request.group_id,
            period_start=request.evaluation_start,
            period_end=request.evaluation_end,
            time_step=timedelta(hours=request.time_step_hours),
            model=model,
            artifact=model.deserialize_artifact(retained[1]),
            forcing_source=source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            group_store=group_store,
            basin_store=basin_store,
            clock=clock,
            rng=random.Random(request.seed),
            hindcast_run_id=run_id,
            issue_batch_size=issue_batch_size,
        )
        if not isinstance(steps_by_station, dict):
            raise ValueError(
                "group fine-tuning evaluation did not return station coverage"
            )
        coverage = [
            {
                "station_id": str(sid),
                "attempted_steps": len(steps_by_station.get(sid, [])),
                "successful_steps": sum(
                    step.success for step in steps_by_station.get(sid, [])
                ),
            }
            for sid in receipt.requested_stations
        ]
        metrics: list[dict[str, Any]] = []
        for sid in receipt.requested_stations:
            for parameter in sorted(model.data_requirements.target_parameters):
                scores, _ = compute_skills_task(
                    station_id=sid,
                    model_id=request.model_id,
                    artifact_id=artifact_id,
                    parameter=parameter,
                    hindcast_run_id=run_id,
                    hindcast_store=hindcast_store,
                    obs_store=obs_store,
                    skill_store=skill_store,
                    station_store=station_store,
                    flow_regime_store=flow_regime_store,
                    deployment_config=deployment_config,
                    clock=clock,
                    generation_id=uuid4(),
                )
                # Keep the overall strata in the review report; all strata remain
                # in the skill store with their exact identities and sample counts.
                metrics.extend(
                    {
                        "score_id": str(score.id),
                        "generation_id": str(score.generation_id)
                        if score.generation_id
                        else None,
                        "station_id": str(sid),
                        "parameter": parameter,
                        "lead_time_hours": score.lead_time_hours,
                        "time_step_seconds": score.time_step_seconds,
                        "phase_offset_seconds": score.phase_offset_seconds,
                        "metric": score.metric,
                        "score": score.score if math.isfinite(score.score) else None,
                        "sample_size": score.sample_size,
                        "eval_period_start": score.eval_period_start.isoformat(),
                        "eval_period_end": score.eval_period_end.isoformat(),
                    }
                    for score in scores
                    if score.season is None and score.flow_regime is None
                )
        steps = [step for batch in steps_by_station.values() for step in batch]
        return {
            "artifact_id": str(artifact_id),
            "hindcast_run_id": str(run_id),
            "issue_batch_size": issue_batch_size,
            "coverage": coverage,
            "metrics": metrics,
            "source_evidence": json.loads(source.snapshot()),
        }, steps

    candidate, steps = evaluate(receipt.artifact_id, candidate_run_id)
    donor, _ = evaluate(request.base_artifact_id, donor_run_id)
    complete = bool(candidate["metrics"] and donor["metrics"])
    report = {
        "state": "complete" if complete else "incomplete",
        "candidate": candidate,
        "donor": donor,
        "period_semantics": ("issue-time range; scores state their valid-time bounds"),
        "coverage_semantics": ("completed evaluation can contain station/date gaps"),
        "comparison": "no automatic ranking; common observation samples not verified",
        "donor_pretraining_exposure": "not_verified",
    }
    return replace(receipt, evaluation_json=canonical_json(report)), TrainingResult(
        training_unit=unit,
        artifact_id=receipt.artifact_id,
        hindcast_steps=steps,
        skill_computed=complete,
        error=None
        if complete
        else "evaluation has no comparable stored skill; candidate remains inactive",
    )
