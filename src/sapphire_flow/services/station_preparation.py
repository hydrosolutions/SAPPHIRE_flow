"""Read-only consistency checks over caller-supplied synthetic evidence."""

from __future__ import annotations

from typing import Protocol

from sapphire_flow.types.station_preparation import (
    EvidenceState,
    OpaqueEvidence,
    PreparationAssessment,
    PreparationHold,
    PreparationRequest,
    StationIdentity,
    VisiblePackage,
    VisibleStation,
)


class PreparationEvidenceRepository(Protocol):
    def station(self, identity: StationIdentity) -> VisibleStation | OpaqueEvidence: ...

    def package(
        self, identity: StationIdentity, package_id: str
    ) -> VisiblePackage | OpaqueEvidence: ...


def assess_station_preparation(
    request: PreparationRequest, repository: PreparationEvidenceRepository
) -> PreparationAssessment:
    station = repository.station(request.station.identity)
    if isinstance(station, VisibleStation):
        hold = (
            PreparationHold.EXISTING_STATION
            if station.identity == request.station.identity
            else PreparationHold.EVIDENCE_UNAVAILABLE
        )
        return PreparationAssessment(request=request, holds=(hold,))
    if station.state is not EvidenceState.ABSENT:
        return PreparationAssessment(
            request=request, holds=(PreparationHold.EVIDENCE_UNAVAILABLE,)
        )

    package = repository.package(request.station.identity, request.package.package_id)
    if (
        not isinstance(package, VisiblePackage)
        or package.identity != request.station.identity
    ):
        return PreparationAssessment(
            request=request, holds=(PreparationHold.EVIDENCE_UNAVAILABLE,)
        )
    holds: list[PreparationHold] = []
    if package.package != request.package:
        holds.append(PreparationHold.IDENTITY_MISMATCH)
    if package.bindings != request.bindings:
        holds.append(PreparationHold.BINDING_MISMATCH)
    return PreparationAssessment(request=request, holds=tuple(holds))
