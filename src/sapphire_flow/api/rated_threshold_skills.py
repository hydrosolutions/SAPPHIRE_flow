# ruff: noqa: TC001 -- Pydantic resolves boundary annotations.
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 -- Pydantic boundary annotation

from pydantic import AwareDatetime, BaseModel

from sapphire_flow.services.skill.rated_thresholds import prepare_ratings
from sapphire_flow.store.rated_threshold_codec import (
    RatedCompatibilityPayload,
    RatedDiagnosticPayload,
    RatedEvaluationPayload,
    rated_evaluation_content,
)
from sapphire_flow.store.skill_period_codec import SkillPeriodPayload
from sapphire_flow.types.rated_threshold_skill import ThresholdReferenceBasis

if TYPE_CHECKING:
    from sapphire_flow.types.rated_threshold_skill import RatedThresholdEvaluation


class RatedCurveReferenceResponse(BaseModel):
    physical_sha256: str
    deliveries: tuple[tuple[str, int], ...]
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    water_level_min: float
    water_level_max: float


class RatedThresholdEvaluationResponse(BaseModel):
    generation_id: UUID
    station_id: UUID
    manifest_sha256: str
    input_sha256: str
    source_url: str
    retrieved_at: AwareDatetime
    reference_basis: ThresholdReferenceBasis
    reference_operator: str
    reference_evidence: str
    reference_approved_at: AwareDatetime
    compatibility: RatedCompatibilityPayload
    thresholds_m: dict[str, float]
    curves: tuple[RatedCurveReferenceResponse, ...]
    hindcast_count: int
    hindcast_run_ids: tuple[UUID, ...]
    issue_start: AwareDatetime
    issue_end: AwareDatetime
    observation_count: int
    diagnostics: tuple[RatedDiagnosticPayload, ...]
    evaluation_contexts: dict[str, SkillPeriodPayload]


def evaluation_response(
    generation_id: UUID, evaluation: RatedThresholdEvaluation
) -> RatedThresholdEvaluationResponse:
    raw = rated_evaluation_content(evaluation)
    assert raw is not None
    parsed = RatedEvaluationPayload.model_validate(raw)
    manifest = evaluation.manifest
    return RatedThresholdEvaluationResponse(
        generation_id=generation_id,
        station_id=manifest.station_id,
        manifest_sha256=manifest.digest.value,
        input_sha256=evaluation.input_sha256.value,
        source_url=manifest.source_url,
        retrieved_at=manifest.retrieved_at,
        reference_basis=manifest.reference_basis,
        reference_operator=manifest.reference_operator,
        reference_evidence=manifest.reference_evidence,
        reference_approved_at=manifest.reference_approved_at,
        compatibility=RatedCompatibilityPayload.model_validate(raw["compatibility"]),
        thresholds_m={item.level: item.value for item in manifest.thresholds},
        curves=tuple(
            RatedCurveReferenceResponse(
                physical_sha256=curve.content_sha256,
                deliveries=curve.references,
                valid_from=curve.valid_from,
                valid_to=curve.valid_to,
                water_level_min=curve.converter.stages[0],
                water_level_max=curve.converter.stages[-1],
            )
            for curve in prepare_ratings(manifest)
        ),
        hindcast_count=evaluation.hindcast_count,
        hindcast_run_ids=evaluation.hindcast_run_ids,
        issue_start=evaluation.issue_start,
        issue_end=evaluation.issue_end,
        observation_count=evaluation.observation_count,
        diagnostics=parsed.diagnostics,
        evaluation_contexts=parsed.evaluation_contexts,
    )
