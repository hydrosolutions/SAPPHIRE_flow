from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import ObservationSource, QcStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.calculated_station import ComponentWeight
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.observation import Observation

# Deterministic component-source precedence, highest-trust first (Plan 015 §Missing
# component observations). ``component_derived`` is last so a calculated-of-calculated
# row — which the two-tier formula invariant forbids as a current component anyway —
# can never be silently preferred over a real measurement.
SOURCE_PRECEDENCE: tuple[ObservationSource, ...] = (
    ObservationSource.MEASURED,
    ObservationSource.RATING_CURVE_DERIVED,
    ObservationSource.MANUAL_IMPORT,
    ObservationSource.COMPONENT_DERIVED,
)
_PRECEDENCE_RANK: dict[ObservationSource, int] = {
    source: rank for rank, source in enumerate(SOURCE_PRECEDENCE)
}

# QC provenance rule for derived observations (Plan 015 §D6).
DERIVATION_RULE_ID = "upstream_propagated"
DERIVATION_RULE_VERSION = "component_derivation/v1"

# A component observation may only feed a derivation when it has really been QC'd and
# passed (or is flagged-but-usable). RAW / QC_FAILED / MISSING all trigger a skip.
_USABLE_STATUSES: frozenset[QcStatus] = frozenset(
    {QcStatus.QC_PASSED, QcStatus.QC_SUSPECT}
)


@dataclass(frozen=True, kw_only=True, slots=True)
class DerivedPoint:
    """The outcome of deriving a calculated station's value at a single timestamp.

    ``value is None`` (with ``qc_status == MISSING``) is the placeholder written when
    the derivation was skipped — a component was absent, ineligible, or not usable.
    """

    value: float | None
    qc_status: QcStatus
    qc_flags: list[QcFlag]


def select_by_precedence(observations: Sequence[Observation]) -> Observation | None:
    """The single highest-precedence observation for one (station, timestamp, param).

    A component may carry several rows for the same timestamp (e.g. ``measured`` plus a
    ``rating_curve_derived`` backfill) because ``source`` is part of the observations
    natural key. Returns ``None`` for an empty input.
    """
    ranked = [o for o in observations if o.source in _PRECEDENCE_RANK]
    if not ranked:
        return None
    return min(ranked, key=lambda o: _PRECEDENCE_RANK[o.source])


def propagate_qc_status(component_statuses: Sequence[QcStatus]) -> QcStatus:
    """Any component ``QC_SUSPECT`` ⇒ derived ``QC_SUSPECT``, else ``QC_PASSED`` (§D6).

    Weights never enter the aggregation. Only usable (PASSED/SUSPECT) components reach
    here — ``derive_point`` skips the derivation otherwise.
    """
    if any(status == QcStatus.QC_SUSPECT for status in component_statuses):
        return QcStatus.QC_SUSPECT
    return QcStatus.QC_PASSED


def _provenance_flags(
    resolved: Sequence[tuple[ComponentWeight, Observation]],
) -> list[QcFlag]:
    from sapphire_flow.types.domain import QcFlag

    return [
        QcFlag(
            rule_id=DERIVATION_RULE_ID,
            rule_version=DERIVATION_RULE_VERSION,
            status=obs.qc_status,
            detail=json.dumps(
                {
                    "component_station_id": str(weight.component_station_id),
                    "component_status": obs.qc_status.value,
                    "weight": weight.weight,
                },
                sort_keys=True,
            ),
        )
        for weight, obs in resolved
    ]


def derive_point(
    resolved: Sequence[tuple[ComponentWeight, Observation | None]],
) -> DerivedPoint:
    """Derive ``Q_virtual = Σ(wᵢ · Qᵢ)`` for one timestamp from resolved components.

    ``resolved`` carries one ``(weight, observation)`` pair per formula component;
    ``observation is None`` marks a component that is missing or ineligible at this
    timestamp. If any component is missing or not usable (RAW / QC_FAILED / MISSING /
    value ``None``) the whole derivation is skipped and a MISSING placeholder is
    returned — a partial weighted sum is not a valid derivation.
    """
    usable: list[tuple[ComponentWeight, Observation]] = []
    value = 0.0
    for weight, obs in resolved:
        if obs is None or obs.qc_status not in _USABLE_STATUSES or obs.value is None:
            return DerivedPoint(value=None, qc_status=QcStatus.MISSING, qc_flags=[])
        value += weight.weight * obs.value  # obs.value narrowed to float by the guard
        usable.append((weight, obs))
    status = propagate_qc_status([obs.qc_status for _, obs in usable])
    return DerivedPoint(
        value=value, qc_status=status, qc_flags=_provenance_flags(usable)
    )
