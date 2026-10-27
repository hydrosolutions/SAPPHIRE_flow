# Pydantic boundary schemas require runtime type imports.
# ruff: noqa: TC001, TC003
from __future__ import annotations

import json
from datetime import datetime, timedelta  # noqa: TC003 -- Pydantic runtime fields.
from typing import cast
from uuid import UUID  # noqa: TC003 -- Pydantic runtime fields.

from pydantic import BaseModel, ConfigDict

from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberReason,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_forecast_recovery import (
    ConfirmedDailyTermination,
    DailyWriterState,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_output import DailyGridAnchor, DailyOutputContract
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import InputQualityFlag
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    FlowRunState,
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
)
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ModelId, PrincipalId, StationId


class _ContractDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parameter: str
    units: str
    representation: EnsembleRepresentation
    time_step: timedelta
    first_lead: timedelta
    horizon_steps: int
    series_keys: tuple[float, ...]
    minimum_horizon_steps: int | None = None
    grid_anchor: DailyGridAnchor = DailyGridAnchor.ISSUE_TIME

    def to_domain(self) -> DailyOutputContract:
        return DailyOutputContract(**self.model_dump())


class _ReferenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    station_id: UUID
    model_id: str
    issued_at: datetime
    parameter: str
    source: DailyOutputSource
    output_id: UUID


class _QualityFlagDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: InputQualityCategory
    level: InputQualityLevel
    detail: str


class _ProvenanceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    nwp_source: NwpCycleSource | None
    nwp_reference_time: datetime | None
    nwp_age_hours: float | None
    observation_staleness_hours: float | None
    input_quality: InputQualityLevel | None
    input_quality_flags: tuple[_QualityFlagDocument, ...]

    def to_domain(self) -> DailyInputProvenance:
        return DailyInputProvenance(
            nwp_source=self.nwp_source,
            nwp_reference_time=ensure_utc(self.nwp_reference_time)
            if self.nwp_reference_time
            else None,
            nwp_age_hours=self.nwp_age_hours,
            observation_staleness_hours=self.observation_staleness_hours,
            input_quality=self.input_quality,
            input_quality_flags=tuple(
                InputQualityFlag(
                    category=flag.category, level=flag.level, detail=flag.detail
                )
                for flag in self.input_quality_flags
            ),
        )


def _provenance_document(value: DailyInputProvenance) -> _ProvenanceDocument:
    return _ProvenanceDocument(
        nwp_source=value.nwp_source,
        nwp_reference_time=value.nwp_reference_time,
        nwp_age_hours=value.nwp_age_hours,
        observation_staleness_hours=value.observation_staleness_hours,
        input_quality=value.input_quality,
        input_quality_flags=tuple(
            _QualityFlagDocument(
                category=flag.category, level=flag.level, detail=flag.detail
            )
            for flag in value.input_quality_flags
        ),
    )


class _OutcomeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    station_id: UUID
    required_parameters: tuple[str, ...]
    state: DailyMemberState
    reason: DailyMemberReason | None
    references: tuple[_ReferenceDocument, ...]
    provenance: _ProvenanceDocument

    def to_domain(self) -> DailyMemberOutcome:
        return DailyMemberOutcome(
            station_id=StationId(self.station_id),
            required_parameters=self.required_parameters,
            state=self.state,
            reason=self.reason,
            provenance=self.provenance.to_domain(),
            references=tuple(
                DailyOutputReference(
                    key=ForecastOutputKey(
                        station_id=StationId(ref.station_id),
                        model_id=ModelId(ref.model_id),
                        issued_at=ensure_utc(ref.issued_at),
                        parameter=ref.parameter,
                    ),
                    source=ref.source,
                    output_id=ref.output_id,
                )
                for ref in self.references
            ),
        )


def contracts_json(contracts: tuple[DailyOutputContract, ...]) -> str:
    return json.dumps(
        [
            _ContractDocument(
                parameter=c.parameter,
                units=c.units,
                representation=c.representation,
                time_step=c.time_step,
                first_lead=c.first_lead,
                horizon_steps=c.horizon_steps,
                series_keys=c.series_keys,
                minimum_horizon_steps=c.minimum_horizon_steps,
                grid_anchor=c.grid_anchor,
            ).model_dump(mode="json")
            for c in contracts
        ]
    )


def parse_contracts(raw: object) -> tuple[DailyOutputContract, ...]:
    if not isinstance(raw, list):
        raise ValueError("daily output contracts must be an array")
    return tuple(
        _ContractDocument.model_validate(item).to_domain()
        for item in cast("list[object]", raw)
    )


def outcomes_json(outcomes: tuple[DailyMemberOutcome, ...]) -> str:
    return json.dumps(
        [
            _OutcomeDocument(
                station_id=o.station_id,
                required_parameters=o.required_parameters,
                state=o.state,
                reason=o.reason,
                provenance=_provenance_document(o.provenance),
                references=tuple(
                    _ReferenceDocument(
                        station_id=r.key.station_id,
                        model_id=r.key.model_id,
                        issued_at=r.key.issued_at,
                        parameter=r.key.parameter,
                        source=r.source,
                        output_id=r.output_id,
                    )
                    for r in o.references
                ),
            ).model_dump(mode="json")
            for o in outcomes
        ]
    )


class _RecoveryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attempt_id: UUID
    owner_id: UUID
    operator: str
    confirmed_at: datetime
    run_state: FlowRunState
    writer_state: DailyWriterState

    def to_domain(self) -> ConfirmedDailyTermination:
        return ConfirmedDailyTermination(
            attempt_id=self.attempt_id,
            owner_id=self.owner_id,
            operator=PrincipalId(self.operator),
            confirmed_at=ensure_utc(self.confirmed_at),
            run_state=self.run_state,
            writer_state=self.writer_state,
        )


def parse_outcomes(raw: object) -> tuple[DailyMemberOutcome, ...]:
    if not isinstance(raw, list):
        raise ValueError("daily outcomes must be an array")
    return tuple(
        _OutcomeDocument.model_validate(item).to_domain()
        for item in cast("list[object]", raw)
    )


def parse_recovery(raw: object) -> ConfirmedDailyTermination:
    return _RecoveryDocument.model_validate(raw).to_domain()
