"""Plan 198 T2b — database readers for the Forecast Lab snapshot.

Thin, pure query functions over the EXISTING store Protocols
(`protocols/stores.py`) — no new store code, no new query surface (D14), no
migration. `build_snapshot()` (T3) constructs a `ForecastLabStores` bundle
once per invocation and passes it down.

D17 — the eligible station set is narrowed BEFORE principal scoping, on
BOTH the list-all sweep and the explicitly-requested-code path:
``network='bafu' AND station_kind='river' AND station_status='operational'``.
Model assignments are filtered to ``ACTIVE`` (D17b) — an inactive
assignment must never be exported or win `is_primary`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sapphire_flow.types.enums import ModelAssignmentStatus, StationKind, StationStatus

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ForecastStore,
        ModelArtifactStore,
        ModelStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ArtifactId, BasinId, ModelId, StationId
    from sapphire_flow.types.model import ModelArtifactProvenance, ModelRecord
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.station import ModelAssignment, StationConfig

_ELIGIBLE_NETWORK = "bafu"
_ELIGIBLE_KIND = StationKind.RIVER
_ELIGIBLE_STATUS = StationStatus.OPERATIONAL
_DISCHARGE_PARAMETER = "discharge"


class ArtifactProvenanceStore(Protocol):
    """Minimal shape of `store/model_artifact_provenance.py`'s
    `PgArtifactProvenanceStore`/`FakeArtifactProvenanceStore` — no repo-wide
    Protocol exists for this one (it is only ever called from the import
    path today), so this is scoped locally to what T2b needs (F6)."""

    def fetch(self, artifact_id: ArtifactId) -> ModelArtifactProvenance | None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastLabStores:
    station_store: StationStore
    observation_store: ObservationStore
    forecast_store: ForecastStore
    model_store: ModelStore
    artifact_store: ModelArtifactStore
    provenance_store: ArtifactProvenanceStore
    basin_store: BasinStore


def _is_eligible(station: StationConfig) -> bool:
    return (
        station.network == _ELIGIBLE_NETWORK
        and station.station_kind is _ELIGIBLE_KIND
        and station.station_status is _ELIGIBLE_STATUS
    )


def fetch_eligible_stations(stores: ForecastLabStores) -> list[StationConfig]:
    """D17a — every operational BAFU river station, sorted by `code` (D12)."""
    all_stations = stores.station_store.fetch_all_stations(kind=_ELIGIBLE_KIND)
    return sorted(
        (s for s in all_stations if _is_eligible(s)),
        key=lambda s: s.code,
    )


def fetch_eligible_station_by_code(
    stores: ForecastLabStores, code: str
) -> StationConfig | None:
    """D17a — resolves against the canonical `bafu` network and re-checks
    kind/status, so an explicitly requested lake/onboarding/other-network
    station is indistinguishable from a typo (the caller maps `None` to
    `404`, D8)."""
    station = stores.station_store.fetch_station_by_code(code, _ELIGIBLE_NETWORK)
    if station is None or not _is_eligible(station):
        return None
    return station


def fetch_basin_area_km2(
    stores: ForecastLabStores, basin_id: BasinId | None
) -> float | None:
    if basin_id is None:
        return None
    basin = stores.basin_store.fetch_basin(basin_id)
    return basin.area_km2 if basin is not None else None


def fetch_observation_window(
    stores: ForecastLabStores,
    station_id: StationId,
    *,
    window_start: UtcDatetime,
    window_end: UtcDatetime,
) -> list[Observation]:
    """`measured` + `qc_passed` + `discharge` only (F8/AC7), ordered by
    `timestamp` ascending (D12)."""
    from sapphire_flow.types.enums import ObservationSource, QcStatus

    observations = stores.observation_store.fetch_observations(
        station_id,
        _DISCHARGE_PARAMETER,
        window_start,
        window_end,
        qc_status=QcStatus.QC_PASSED,
        source=ObservationSource.MEASURED,
    )
    # AC5, at the source — the same treatment the BAFU traces and the
    # ensemble members already get. A qc_passed reading whose value is
    # non-finite is corrupt, not "missing": the contract has no per-point
    # null for an observation, so the honest rendering is no point at all.
    # Doing it HERE and not only at the schema keeps the required-float
    # validator an unreachable invariant rather than a live raise on a code
    # path that has no D13 guard (which would 500 the whole request).
    finite = [o for o in observations if o.value is not None and math.isfinite(o.value)]
    return sorted(finite, key=lambda o: o.timestamp)


def fetch_active_model_assignments(
    stores: ForecastLabStores, station_id: StationId
) -> list[ModelAssignment]:
    """D17b — ACTIVE only, pre-sorted `(priority asc, model_key asc)`
    (D12's `is_primary` tiebreak — `priority` has no uniqueness
    constraint and a `server_default` of 0)."""
    assignments = stores.station_store.fetch_model_assignments(station_id)
    active = [a for a in assignments if a.status is ModelAssignmentStatus.ACTIVE]
    return sorted(active, key=lambda a: (a.priority, a.model_id))


def fetch_latest_forecast_for_model(
    stores: ForecastLabStores, station_id: StationId, model_id: ModelId
) -> OperationalForecast | None:
    """D14 — reuses `ForecastStore.fetch_latest_forecast()` (no new query
    surface, no query-count contract)."""
    return stores.forecast_store.fetch_latest_forecast(
        station_id, model_id, _DISCHARGE_PARAMETER
    )


def fetch_model_display(
    stores: ForecastLabStores, model_id: ModelId
) -> ModelRecord | None:
    return stores.model_store.fetch_model(model_id)


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactInfo:
    artifact_sha256: str | None
    source_commit: str | None


def fetch_artifact_info(
    stores: ForecastLabStores, artifact_id: ArtifactId | None
) -> ArtifactInfo:
    """F6 — `source_commit` from `model_artifact_provenance` when present
    (0 rows on the mini today, so `null` in practice), never the running
    image tag (a property of *now*, not of the forecast)."""
    if artifact_id is None:
        return ArtifactInfo(artifact_sha256=None, source_commit=None)
    record = stores.artifact_store.fetch_artifact_record(artifact_id)
    sha256 = record.sha256_hash if record is not None else None
    provenance = stores.provenance_store.fetch(artifact_id)
    source_commit = provenance.source_commit if provenance is not None else None
    return ArtifactInfo(artifact_sha256=sha256, source_commit=source_commit)
