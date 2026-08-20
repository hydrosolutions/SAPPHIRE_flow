"""Forecast-cycle redesign Phase 3 domain types (Plan 151 T1).

Realizes the LOCKED shapes from ``docs/spec/types-and-protocols.md`` — this
module does not redesign them (Plan 151 D1). ``ForcingTrackKey`` is the
hashable dedup key at which one recap fetch operates; it carries NO horizon
values (see the module docstring in the spec for why a ``Mapping`` field
would have made the key both wrong and unhashable).

``OutputHorizon`` is deliberately NOT created here (D23) — it has no
consumer in Phase 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, NewType

# Runtime import (not TYPE_CHECKING-only): AssignmentKey below is a NewType
# built at IMPORT time, which needs a real `tuple[StationId, ModelId]` type
# object — `from __future__ import annotations` only defers ANNOTATIONS, not
# module-level statements (mirrors types/model.py's ArtifactId precedent).
from sapphire_flow.types.ids import ModelId, StationId

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import (
        EnsembleMode,
        NwpCycleSource,
        SpatialRepresentation,
    )
    from sapphire_flow.types.weather import (
        GriddedForecast,
        WeatherForecastRecord,
        WeatherForecastResult,
    )

FeatureName = NewType("FeatureName", str)

# (station_id, model_id) — the join key T3's projector groups by. Concrete
# realization of the spec's "assignment: object (station_id|group_id,
# model_id) key; concrete in build plan" for the STATION scope Phase 3
# handles (group is out of scope, D8-group).
AssignmentKey = NewType("AssignmentKey", tuple[StationId, ModelId])


@dataclass(frozen=True, kw_only=True, slots=True)
class FutureSteps:
    """Validated per-feature horizon length — NOT a ``NewType``, which cannot
    enforce ``> 0``."""

    value: int

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError(f"FutureSteps must be > 0, got {self.value}")


# Three DISTINCT horizon types (not interchangeable ints, D1).
FeatureFetchHorizons = Mapping[FeatureName, FutureSteps]


@dataclass(frozen=True, kw_only=True, slots=True)
class InputFrameHorizon:
    steps: FutureSteps


@dataclass(frozen=True, kw_only=True, slots=True)
class ForcingTrackKey:
    """Hashable dedup key — exactly the axes at which ONE recap fetch
    operates. Carries NO horizon values (D1/D5): two requirements differing
    only in per-feature horizons project to the SAME key."""

    nwp_source: str  # D12: the repo's existing `str`; no `NwpSource` type exists
    ensemble_mode: EnsembleMode
    time_step: timedelta
    spatial_representation: SpatialRepresentation
    features: frozenset[FeatureName]


@dataclass(frozen=True, kw_only=True, slots=True)
class ForcingRequired:
    """Per-assignment PROJECTION (before dedup): the track an assignment
    needs, plus its OWN per-feature fetch horizons.

    Invariant: ``set(assignment_horizons) == key.features``.
    """

    key: ForcingTrackKey
    assignment_horizons: FeatureFetchHorizons
    assignment: AssignmentKey


@dataclass(frozen=True, kw_only=True, slots=True)
class NoForcingRequired:
    """Covers fallback/skill models with no future forcing, and any
    assignment whose SELECTED branch declares no ``future_known`` (D2)."""

    assignment: AssignmentKey


TrackProjection = ForcingRequired | NoForcingRequired


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolvedTrackRequest:
    """The dedup RESULT — exactly one per distinct key.

    ``fetch_horizons`` is the per-feature MAX across every assignment sharing
    the key (D5); ``assignments`` retains each assignment's own horizons for
    later per-assignment slicing (<= max, D9).
    """

    key: ForcingTrackKey
    fetch_horizons: FeatureFetchHorizons
    assignments: tuple[ForcingRequired, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ForcingResolutionPolicy:
    """Plan 151 T8a: the flow-level carrier that supplies all THREE
    resolver-facing policy values to the services layer as ONE object (D7 —
    the adapter must never be the carrier of services-layer policy, so the
    resolver must not read any of these off the adapter, private or
    public).

    ``cycle_cadence_hours`` is :func:`resolve_candidate`'s walk-back
    candidate spacing (ruling 4 / D24); ``max_cycle_age_hours`` is its
    walk-back bound; ``max_retries`` is the retry count the retrying
    candidate-fetch task (D28) is configured with. Constructed on all three
    flow-level paths (production config, injected client, injected
    adapter) — see ``flows/run_forecast_cycle.py``.
    """

    cycle_cadence_hours: float
    max_cycle_age_hours: float
    max_retries: int

    def __post_init__(self) -> None:
        if self.cycle_cadence_hours <= 0:
            raise ValueError(
                f"cycle_cadence_hours must be > 0, got {self.cycle_cadence_hours!r}"
            )
        if self.max_cycle_age_hours <= 0:
            raise ValueError(
                f"max_cycle_age_hours must be > 0, got {self.max_cycle_age_hours!r}"
            )
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries!r}")


class StationUnavailableReason(Enum):
    NO_DATA_AT_CYCLE = auto()
    EXTRACTION_EMPTY = auto()
    NOT_SUBSCRIBED = auto()
    # --- additive, Phase 3 (D8, D27) ---
    INCOMPLETE_AT_CYCLE = auto()
    MISSING_POLYGON_COLUMN = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class StationTrackAvailable:
    cycle: UtcDatetime
    records: list[WeatherForecastRecord]
    provenance: NwpCycleSource


@dataclass(frozen=True, kw_only=True, slots=True)
class StationTrackUnavailable:
    reason: StationUnavailableReason


StationTrackOutcome = StationTrackAvailable | StationTrackUnavailable


@dataclass(frozen=True, kw_only=True, slots=True)
class TrackFetchResult:
    resolved_cycle: UtcDatetime
    station_outcomes: Mapping[StationId, StationTrackOutcome]


class CandidateFetchStatus(Enum):
    """Candidate-fetch outcome taxonomy — the SERVICES-side verdict (D31)."""

    COMPLETE = auto()
    ABSENT_INCOMPLETE = auto()
    TRANSIENT = auto()
    AUTH_CONFIG = auto()
    STORE = auto()
    # (per-station availability is NOT a candidate status — StationTrackOutcome)


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidateFetchResult:
    """Candidate-LOCAL: fresh per candidate, never reused/mutated.

    Constructed SERVICES-side, after the per-station completeness predicate
    (D8) — never by the adapter (D31-candidate-ownership).
    """

    status: CandidateFetchStatus
    cycle: UtcDatetime
    raw: GriddedForecast | dict[StationId, WeatherForecastResult] | None


class RawFetchStatus(Enum):
    """The ADAPTER's transport-only classification (D31) — never a
    completeness verdict, which is a services-side concern (D8)."""

    FETCHED = auto()
    ABSENT_AT_CYCLE = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class RawFetchOutcome:
    """``CandidateAwareForecastSource.fetch_requirement``'s return type
    (D31-candidate-ownership): a raw typed fetch outcome — per-station
    payload plus a TRANSPORT classification only. The adapter never judges
    completeness; ``services/`` constructs the immutable
    ``CandidateFetchResult`` from this after applying the per-station
    predicate (D8), which needs the track's ``fetch_horizons`` the adapter
    cannot see.
    """

    status: RawFetchStatus
    cycle: UtcDatetime
    stations: Mapping[StationId, WeatherForecastResult]
    # D27: a station explicitly excluded because its polygon column was
    # missing from an otherwise-committed HRU response — distinct from a
    # station simply absent from `stations` for another reason. Never
    # silently dropped: recorded here so services/ can later attribute
    # StationTrackUnavailable(MISSING_POLYGON_COLUMN) to it (T5).
    missing_polygon_column: frozenset[StationId] = frozenset()
    # Diagnostic text for ABSENT_AT_CYCLE (e.g. the chained Gateway error for
    # a total-loss-after-containment candidate); None for a well-formed-empty
    # candidate or any FETCHED outcome. Never free-text on a persisted type —
    # this is the adapter's raw outcome, not StationTrackUnavailable (D27).
    absent_detail: str | None = None


# The two Phase 3 additive `AssignmentFailureCause` members (MISSING_CONTEXT,
# TRACK_UNAVAILABLE, D3-mapping) are added directly onto the landed enum in
# `services/run_station_forecast.py` (Plan 150 D1) — not redeclared here.
