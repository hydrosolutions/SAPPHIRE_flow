from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.component_derivation import (
    DERIVATION_RULE_VERSION,
    derive_point,
    select_by_precedence,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import (
    GaugingStatus,
    ObservationSource,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.station import StationConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.config.onboarding import CalculatedStationSpec
    from sapphire_flow.protocols.stores import (
        FormulaStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.calculated_station import ComponentWeight
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import BasinId, StationId

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class CalculatedOnboardingOutcome:
    station: StationConfig
    observations_derived: int
    observations_missing: int
    formula_configured: bool  # False when the identical formula already existed
    created: bool  # True when a NEW calc station was stored this run


def _resolve_components(
    spec: CalculatedStationSpec, station_store: StationStore
) -> list[tuple[StationId, float]]:
    """5.C2 — validate + resolve component codes to (station_id, weight).

    Every component must exist and be ``gauging_status = 'gauged'`` AND
    ``station_status = 'operational'`` in the DB (mirrors the D2 trigger + the live
    Flow 2 gate). Cycles are impossible by construction: the gauging-status invariant
    forbids a calculated station referencing another calculated station.
    """
    resolved: list[tuple[StationId, float]] = []
    for component in spec.components:
        station = station_store.fetch_station_by_code(component.code, spec.network)
        if station is None:
            raise ConfigurationError(
                f"calculated station {spec.code!r}: component {component.code!r} "
                f"not found in network {spec.network!r}"
            )
        if station.gauging_status != GaugingStatus.GAUGED:
            raise ConfigurationError(
                f"calculated station {spec.code!r}: component {component.code!r} must "
                f"be gauged (got gauging_status={station.gauging_status.value})"
            )
        if station.station_status != StationStatus.OPERATIONAL:
            raise ConfigurationError(
                f"calculated station {spec.code!r}: component {component.code!r} must "
                f"be operational (got station_status={station.station_status.value})"
            )
        resolved.append((station.id, component.weight))
    return resolved


def build_calculated_station_config(
    spec: CalculatedStationSpec,
    basin_id: BasinId | None,
    station_id: StationId,
    clock: Callable[[], UtcDatetime],
) -> StationConfig:
    now = clock()
    return StationConfig(
        id=station_id,
        code=spec.code,
        name=spec.name,
        location=GeoCoord(lon=spec.lon, lat=spec.lat),
        station_kind=StationKind.RIVER,
        basin_id=basin_id,
        timezone=spec.timezone,
        regulation_type=None,
        forecast_targets=frozenset({spec.parameter}),
        measured_parameters=frozenset({spec.parameter}),
        station_status=StationStatus.ONBOARDING,
        created_at=now,
        updated_at=now,
        network=spec.network,
        ownership=StationOwnership.OWN,
        wigos_id=None,
        gauging_status=GaugingStatus.CALCULATED,
    )


def _fetch_component_history(
    component_ids: Sequence[StationId],
    parameter: str,
    obs_store: ObservationStore,
    start: UtcDatetime,
    end: UtcDatetime,
) -> dict[StationId, dict[UtcDatetime, Observation]]:
    """Per component, the highest-precedence obs at each timestamp in [start, end)."""
    best: dict[StationId, dict[UtcDatetime, Observation]] = {}
    for cid in component_ids:
        grouped: dict[UtcDatetime, list[Observation]] = {}
        for obs in obs_store.fetch_observations(cid, parameter, start, end):
            grouped.setdefault(obs.timestamp, []).append(obs)
        best[cid] = {
            ts: winner
            for ts, group in grouped.items()
            if (winner := select_by_precedence(group)) is not None
        }
    return best


def onboard_calculated_station(
    spec: CalculatedStationSpec,
    basin_id: BasinId | None,
    station_store: StationStore,
    obs_store: ObservationStore,
    formula_store: FormulaStore,
    clock: Callable[[], UtcDatetime],
    window_start: UtcDatetime,
    window_end: UtcDatetime,
) -> CalculatedOnboardingOutcome:
    """Onboard one calculated station (Plan 015 §5.C1–5.C3).

    Validates + resolves components (5.C2), configures the weighted-sum formula with
    ``effective_from`` defaulting to the earliest component observation (5.C1), and
    bootstraps ``component_derived`` history over the validity window (5.C3, applying
    ``fetch_formula_at`` per timestamp so timestamps before ``effective_from`` stay
    underived).

    ALL validation — components, effective_from, formula rows — happens **before** any
    row is written, so a bad spec never leaves an orphan station or partial formula.
    Raises ``ConfigurationError`` on any anticipated failure. Idempotent: a re-run whose
    current formula is byte-for-byte identical is a no-op; a *different* current formula
    is rejected (close it before reconfiguring — this slice does not version formulas).
    """
    from sapphire_flow.types.ids import StationId as _StationId

    resolved = _resolve_components(spec, station_store)

    existing = station_store.fetch_station_by_code(spec.code, spec.network)
    if existing is not None and existing.gauging_status != GaugingStatus.CALCULATED:
        raise ConfigurationError(
            f"calculated station {spec.code!r}: code is already used by a "
            f"non-calculated station in network {spec.network!r}"
        )
    station_id = existing.id if existing is not None else _StationId(uuid4())

    # Validate everything that can fail BEFORE persisting anything. effective_from is
    # resolved up front so it can enter the idempotency comparison below.
    history = _fetch_component_history(
        [cid for cid, _ in resolved],
        spec.parameter,
        obs_store,
        window_start,
        window_end,
    )
    effective_from = _resolve_effective_from(spec, history)
    if effective_from is None:
        raise ConfigurationError(
            f"calculated station {spec.code!r}: no component observations found for "
            f"parameter {spec.parameter!r} and no explicit effective_from given"
        )
    try:
        rows = _build_formula_rows(
            station_id, spec.parameter, resolved, effective_from, clock
        )
    except ValueError as exc:  # ComponentWeight invariants (weight / self-reference)
        raise ConfigurationError(
            f"calculated station {spec.code!r}: invalid formula: {exc}"
        ) from exc

    # Idempotency + no silent reconfiguration (checked before any write). The comparison
    # includes effective_from, so a changed validity start is a reconfiguration too.
    current = formula_store.fetch_current_formula(station_id, spec.parameter)
    if current:
        want = {(cid, weight, effective_from) for cid, weight in resolved}
        have = {
            (row.component_station_id, row.weight, row.effective_from)
            for row in current
        }
        if want != have:
            raise ConfigurationError(
                f"calculated station {spec.code!r}: a different formula is already "
                f"configured for parameter {spec.parameter!r}; close it before "
                "reconfiguring"
            )
        log.info(
            "onboarding.calculated_formula_exists",
            station_id=str(station_id),
            code=spec.code,
        )
        station = existing or build_calculated_station_config(
            spec, basin_id, station_id, clock
        )
        return CalculatedOnboardingOutcome(
            station=station,
            observations_derived=0,
            observations_missing=0,
            formula_configured=False,
            created=False,
        )

    # All validation passed — persist.
    config = build_calculated_station_config(spec, basin_id, station_id, clock)
    if existing is not None:
        station_store.update_station(config)
    else:
        station_store.store_station(config)
    formula_store.store_formula(rows)
    log.info(
        "onboarding.calculated_formula_configured",
        station_id=str(config.id),
        code=spec.code,
        components=len(rows),
        effective_from=effective_from.isoformat(),
    )

    derived, missing = _bootstrap_history(
        config.id,
        spec.parameter,
        formula_store,
        history,
        effective_from,
        obs_store,
        clock,
    )
    log.info(
        "onboarding.calculated_bootstrap_complete",
        station_id=str(config.id),
        code=spec.code,
        derived=derived,
        missing=missing,
    )
    return CalculatedOnboardingOutcome(
        station=config,
        observations_derived=derived,
        observations_missing=missing,
        formula_configured=True,
        created=existing is None,
    )


def _resolve_effective_from(
    spec: CalculatedStationSpec,
    history: dict[StationId, dict[UtcDatetime, Observation]],
) -> UtcDatetime | None:
    if spec.effective_from is not None:
        try:
            return ensure_utc(datetime.fromisoformat(spec.effective_from))
        except ValueError as exc:
            raise ConfigurationError(
                f"calculated station {spec.code!r}: invalid effective_from "
                f"{spec.effective_from!r}: {exc}"
            ) from exc
    timestamps = [ts for per_ts in history.values() for ts in per_ts]
    return min(timestamps) if timestamps else None


def _build_formula_rows(
    calc_id: StationId,
    parameter: str,
    resolved: Sequence[tuple[StationId, float]],
    effective_from: UtcDatetime,
    clock: Callable[[], UtcDatetime],
) -> list[ComponentWeight]:
    from sapphire_flow.types.calculated_station import ComponentWeight
    from sapphire_flow.types.ids import FormulaId

    now = clock()
    return [
        ComponentWeight(
            id=FormulaId(uuid4()),
            calculated_station_id=calc_id,
            component_station_id=component_id,
            parameter=parameter,
            weight=weight,
            effective_from=effective_from,
            effective_to=None,
            created_at=now,
        )
        for component_id, weight in resolved
    ]


def _bootstrap_history(
    calc_id: StationId,
    parameter: str,
    formula_store: FormulaStore,
    history: dict[StationId, dict[UtcDatetime, Observation]],
    effective_from: UtcDatetime,
    obs_store: ObservationStore,
    clock: Callable[[], UtcDatetime],
) -> tuple[int, int]:
    candidate_ts = sorted(
        {ts for per_ts in history.values() for ts in per_ts if ts >= effective_from}
    )
    now = clock()
    to_store: list[Observation] = []
    derived = 0
    missing = 0
    from sapphire_flow.types.ids import ObservationId

    for ts in candidate_ts:
        # Apply the formula VALID AT ts, not blindly the just-configured row: timestamps
        # before effective_from return no formula and are left underived.
        weights_at = formula_store.fetch_formula_at(calc_id, parameter, ts)
        if not weights_at:
            continue
        resolved_point = [
            (w, history[w.component_station_id].get(ts)) for w in weights_at
        ]
        point = derive_point(resolved_point)
        to_store.append(
            Observation(
                id=ObservationId(uuid4()),
                station_id=calc_id,
                timestamp=ts,
                parameter=parameter,
                value=point.value,
                source=ObservationSource.COMPONENT_DERIVED,
                rating_curve_id=None,
                rating_curve_correction_version=None,
                qc_status=point.qc_status,
                qc_flags=point.qc_flags,
                qc_rule_version=(
                    DERIVATION_RULE_VERSION if point.value is not None else None
                ),
                created_at=now,
            )
        )
        if point.value is None:
            missing += 1
        else:
            derived += 1
    if to_store:
        obs_store.store_observations(to_store)
    return derived, missing
