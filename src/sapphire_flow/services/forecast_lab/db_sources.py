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


def fetch_latest_publication_cycle_time(
    stores: ForecastLabStores,
    *,
    data_cutoff_at: UtcDatetime,
) -> UtcDatetime | None:
    """Plan 222 T7 (D7, revised after implement round 2) — the ONE
    snapshot-wide publication cycle every combined-forecast fetch is pinned
    to: `MAX(forecasts.issued_at) WHERE combination_strategy IS NULL AND
    issued_at <= data_cutoff_at`, across every station and model.

    NOT the `FORECAST_FRESHNESS` heartbeat. That append is best-effort —
    its producer catches every failure and continues
    (`flows/run_forecast_cycle.py`) — so a cycle that correctly writes no
    combined row but whose heartbeat append silently fails would leave the
    export pinned to the PREVIOUS cycle, still serving its stale
    `_pooled`/`_bma` row while the flow reports success. The forecast rows
    are the authoritative record of what was actually published and need
    no separate marker that can itself fail to write.

    Deliberately not a timestamp comparison against `clock()`: a scheduled
    cycle's issue time is a runtime read the export cannot reconstruct
    ahead of time, so "current" is answered by looking up what was
    actually published, bounded by `data_cutoff_at` so a future-dated
    backfill/replay override can never publish early. `None` means no
    ordinary forecast has ever been recorded — there is no cycle to pin to
    yet."""
    return stores.forecast_store.fetch_latest_uncombined_issued_at(data_cutoff_at)


def fetch_combined_forecast_for_cycle(
    stores: ForecastLabStores,
    station_id: StationId,
    model_id: ModelId,
    publication_cycle_time: UtcDatetime | None,
) -> OperationalForecast | None:
    """Plan 222 T7 (D6/D7) — fetches the combined (`_pooled`/`_bma`) row
    for the EXACT selected publication cycle
    (`fetch_latest_publication_cycle_time`), never
    `ForecastStore.fetch_latest_forecast()` (which returns whatever
    `_pooled`/`_bma` row was written last, however old, and would keep
    serving it as `available` forever once the pooling intersection goes
    empty — see Plan 222's D7). `publication_cycle_time=None` (no
    ordinary forecast recorded yet) means there is no current cycle to
    serve, so no row is ever returned."""
    if publication_cycle_time is None:
        return None
    candidates = stores.forecast_store.fetch_forecasts_for_cycle(
        publication_cycle_time, station_id, _DISCHARGE_PARAMETER
    )
    return next((f for f in candidates if f.model_id == model_id), None)


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
