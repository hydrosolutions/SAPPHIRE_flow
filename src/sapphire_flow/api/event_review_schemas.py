# ruff: noqa: TC001 -- Pydantic resolves boundary annotations.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.types.enums import ForcingType
from sapphire_flow.types.event_review import (
    MAX_REVIEW_SOURCES,
    ReviewAvailability,
    ReviewCompleteness,
    ReviewKind,
    ReviewOptions,
)
from sapphire_flow.types.ids import CLIMATOLOGY_FALLBACK_MODEL_ID

LEAD_DEFINITION = (
    "Lead is the elapsed time from the forecast issue anchor to the valid timestamp. "
    "A 48-hour lead uses the issue anchored exactly 48 hours earlier. "
    "Hindcast issue anchors are retrospective, not forecasts published then."
)


class ReviewSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ReviewKind
    model_id: str
    model_name: str
    model_role: Literal["model", "baseline"]
    artifact_id: UUID | None
    artifact_sha256: str | None
    model_version: str | None = Field(
        default=None,
        description="Recorded model version, otherwise null; not a forecast revision.",
    )
    training_period_start: datetime | None
    training_period_end: datetime | None
    hindcast_run_id: UUID | None
    forcing_type: ForcingType | None = Field(
        description="Null means unknown, not absence of weather forcing."
    )
    time_step_seconds: int | None = Field(
        gt=0,
        description="Recorded cadence, or null. Does not establish interval support.",
    )
    units: str
    lead_hours: tuple[int, ...] = Field(
        description="Exact nonnegative integer leads stored; not the model horizon."
    )
    first_valid_time: datetime | None
    last_valid_time: datetime | None = Field(
        description="Inclusive last valid time; bounds do not promise full coverage."
    )
    temporal_support: Literal["unknown"] = "unknown"
    completeness: ReviewCompleteness
    reasons: tuple[str, ...]


class ReviewOptionsResponse(BaseModel):
    station_id: UUID
    parameter: Literal["discharge"] = "discharge"
    data_use: Literal["standard"] = "standard"
    lead_definition: str = LEAD_DEFINITION
    operational_availability: ReviewAvailability
    hindcast_availability: ReviewAvailability
    message: str | None
    sources: tuple[ReviewSourceResponse, ...] = Field(max_length=MAX_REVIEW_SOURCES)

    @classmethod
    def from_domain(cls, options: ReviewOptions) -> ReviewOptionsResponse:
        return cls(
            station_id=options.station_id,
            operational_availability=options.operational_availability,
            hindcast_availability=options.hindcast_availability,
            message=(
                "Hindcast review is unavailable under the publication policy."
                if options.hindcast_availability is ReviewAvailability.UNSUPPORTED
                else "No stored discharge forecasts or hindcasts for this station."
                if not options.sources
                else None
            ),
            sources=tuple(
                ReviewSourceResponse(
                    **asdict(source),
                    model_role="baseline"
                    if source.model_id == CLIMATOLOGY_FALLBACK_MODEL_ID
                    else "model",
                )
                for source in options.sources
            ),
        )


class ReviewErrorDetail(BaseModel):
    code: Literal["review_catalog_limit_exceeded", "review_query_timeout"]
    message: str


class ReviewErrorResponse(BaseModel):
    detail: ReviewErrorDetail
