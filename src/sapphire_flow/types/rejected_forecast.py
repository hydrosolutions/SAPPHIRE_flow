"""Plan 404 — the record a QC-rejected member or group-station forecast is
kept in, never in `forecasts` (D1/D2). Two layers:

* `RejectedParameterPayload`/`RejectedAssignmentPayload` — built by
  `services/run_station_forecast.py` and `services/run_group_forecast.py` the
  moment a rejection happens. Plain containers of objects the service
  already holds: no `__post_init__` validation, no copying or encoding, so
  building one cannot raise and cannot change which forecasts or group
  siblings are stored (T2).
* `RejectedForecastEntry`/`PersistedRejectedForecast` — the store boundary
  (T1): `attempt_id` binds a payload to one flow execution;
  `PersistedRejectedForecast` is what a read returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import EnsembleRepresentation
    from sapphire_flow.types.ids import (
        ArtifactId,
        ModelId,
        RejectedForecastId,
        StationGroupId,
        StationId,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class RejectedParameterPayload:
    """One parameter of a rejected assignment. `ensemble` is the RAW
    ensemble, as `forecasts` would store it for water level — not the
    datum-shifted copy QC checked."""

    ensemble: ForecastEnsemble
    qc_status: QcStatus
    qc_flags: tuple[QcFlag, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class RejectedAssignmentPayload:
    """A rejected member or group-station forecast assignment (D1-D3):
    every parameter QC evaluated for it, each with its own verdict.
    `group_id` is None for a member (station) rejection."""

    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId | None
    issued_at: UtcDatetime
    parameters: tuple[RejectedParameterPayload, ...]
    group_id: StationGroupId | None = None
    data_use: ForecastDataUse = ForecastDataUse.STANDARD
    input_lineage: ForecastInputLineage | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class RejectedForecastEntry:
    """One buffered rejection, tagged with the flow run's `attempt_id`
    (D5) — the unit `RejectedForecastStore.write_batch` accepts."""

    attempt_id: UUID
    payload: RejectedAssignmentPayload


@dataclass(frozen=True, kw_only=True, slots=True)
class PersistedRejectedForecast:
    """One row of `rejected_forecasts`, as a read returns it. `values` is
    keyed by member id or quantile level (as a string), each holding its OWN
    `(valid_time, value)` pairs in chronological order — series need not
    share a timeline (`ForecastEnsemble` allows members with different
    timelines)."""

    id: RejectedForecastId
    attempt_id: UUID
    recorded_at: UtcDatetime
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId | None
    group_id: StationGroupId | None
    issued_at: UtcDatetime
    parameter: str
    representation: EnsembleRepresentation
    units: str
    time_step_seconds: int
    qc_status: QcStatus
    qc_flags: tuple[QcFlag, ...]
    values: dict[str, tuple[tuple[UtcDatetime, float], ...]]
    data_use: ForecastDataUse = ForecastDataUse.STANDARD
    input_lineage: ForecastInputLineage | None = None


def validate_rejected_assignment(
    payload: RejectedAssignmentPayload, data_use: ForecastDataUse
) -> None:
    """Validate at capture time, never while constructing a service result."""
    if payload.data_use is not data_use:
        raise ValueError("rejected forecast does not match store purpose")
    if data_use is ForecastDataUse.STANDARD:
        if payload.input_lineage is not None:
            raise ValueError("standard rejection cannot carry test input lineage")
        return
    if not isinstance(payload.input_lineage, ForecastInputLineage):
        raise ValueError("test rejection requires typed consumed input lineage")
    if not any(p.qc_status is QcStatus.QC_FAILED for p in payload.parameters):
        raise ValueError("test rejection requires a QC-failed parameter")
