"""Plan 198 T5 — `GET /api/v1/forecast-lab/snapshot`, the REST surface for
`services.forecast_lab.snapshot.build_snapshot()` (D1). The CLI export
(T6) is the other surface; both call the same assembly function and
serialise the same Pydantic model.

Scoping (D8/D17a): `station_code` is repeatable. Every requested code is
resolved through the ELIGIBLE set first (network=bafu, kind=river,
status=operational, D17a) — an ineligible or unknown code is
indistinguishable from a typo — and only THEN checked against the
principal's scope. Any bad code among several requested `404`s the whole
request (no partial result). With no `station_code` at all, the route
narrows to the principal's scoped eligible stations (empty for a
stationless non-admin — `200` with `stations: []`, matching
`list_stations`).

`500` is not raised explicitly here for a poisoned transaction or a
missing config — both simply propagate as unhandled exceptions, which
`api/errors.py::unhandled_exception_handler` already converts to `500`
(D13).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_stores

# NOT moved under TYPE_CHECKING (would trip ruff TC001): FastAPI resolves
# the return-type annotation at runtime via `typing.get_type_hints()` to
# build the response model — a string-only ForwardRef left unresolved
# breaks every response with a PydanticUserError ("not fully defined").
from sapphire_flow.api.forecast_lab_schemas import ForecastLabSnapshot  # noqa: TC001
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    require_principal,
)
from sapphire_flow.config.deployment import load_config
from sapphire_flow.services.forecast_lab.db_sources import (
    ForecastLabStores,
    fetch_eligible_station_by_code,
    fetch_eligible_stations,
)
from sapphire_flow.services.forecast_lab.snapshot import build_snapshot
from sapphire_flow.types.datetime import ensure_utc

# NOT moved under TYPE_CHECKING, for the same reason as ForecastLabSnapshot
# above: FastAPI's `Depends()` resolution reads this dependency's own
# parameter/return annotations at runtime via `typing.get_type_hints()`.
from sapphire_flow.types.enums import ModelCombinationStrategy  # noqa: TC001

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.station import StationConfig

router = APIRouter(prefix="/api/v1", tags=["forecast-lab"])

_MIN_OBSERVATION_HOURS = 1
_MAX_OBSERVATION_HOURS = 720
_DEFAULT_OBSERVATION_HOURS = 168


def _forecast_lab_stores(stores: dict[str, Any]) -> ForecastLabStores:
    return ForecastLabStores(
        station_store=stores["station_store"],
        observation_store=stores["obs_store"],
        forecast_store=stores["forecast_store"],
        model_store=stores["model_store"],
        artifact_store=stores["artifact_store"],
        provenance_store=stores["provenance_store"],
        basin_store=stores["basin_store"],
    )


def get_bafu_forecast_archive_path() -> Path | None:
    """D2 — a dependency of its own (not folded into `get_stores`) so tests
    can override it in isolation. A `load_config()` failure (missing/
    unreadable `SAPPHIRE_CONFIG`) is NOT caught — it propagates to `500`
    (D13), never a fabricated "missing" partial snapshot."""
    return load_config().bafu_forecast_archive_path


def get_forecast_combination_strategy() -> ModelCombinationStrategy:
    """Plan 204 T1 — a dependency of its own, alongside
    `get_bafu_forecast_archive_path`, so tests can override it in isolation.
    Same `load_config()` failure-propagation contract."""
    return load_config().forecast_combination_strategy


def _resolve_requested_stations(
    stores: ForecastLabStores, principal: Principal, station_codes: list[str]
) -> list[StationConfig]:
    if station_codes:
        resolved: list[StationConfig] = []
        for code in station_codes:
            # D17a — eligibility is resolved BEFORE scope, and independent
            # of `principal.is_admin` (D8's admin-bypass warning): an
            # ineligible or unknown code 404s the same way for every
            # principal.
            station = fetch_eligible_station_by_code(stores, code)
            if station is None:
                raise HTTPException(status_code=404, detail="Station not found")
            ensure_station_in_scope(principal, station.id)
            resolved.append(station)
        return resolved

    # D8 — no explicit code: narrow to the principal's scope, exactly like
    # `list_stations` (empty for a stationless non-admin -> `200` with
    # `stations: []`, handled downstream by `build_snapshot`/D16 rule 2).
    eligible = fetch_eligible_stations(stores)
    if principal.is_admin:
        return eligible
    return [s for s in eligible if principal.station_in_scope(s.id)]


@router.get(
    "/forecast-lab/snapshot",
    summary="Forecast Lab snapshot (forecast-lab-snapshot/v2)",
    description=(
        "A versioned, read-only JSON export of BAFU observations, archived "
        "BAFU forecasts and SAPPHIRE forecasts for one or more eligible "
        "stations (Plan 198, extended by Plan 204). See "
        "docs/spec/forecast-lab-snapshot.md for the full contract — "
        "timestamp/quantile conventions, the F3 percentile-band sign "
        "correction, partial-vs-500 semantics, and offline-caching "
        "guidance."
    ),
)
def get_forecast_lab_snapshot(
    station_code: list[str] = Query(
        default=[],
        description=(
            "Repeatable. Omit to return every eligible station in the "
            "caller's scope (D8)."
        ),
    ),
    observation_hours: int = Query(
        _DEFAULT_OBSERVATION_HOURS,
        ge=_MIN_OBSERVATION_HOURS,
        le=_MAX_OBSERVATION_HOURS,
        description="Lookback window for the observations section, in hours.",
    ),
    stores: dict[str, Any] = Depends(get_stores),
    archive_base_path: Path | None = Depends(get_bafu_forecast_archive_path),
    combination_strategy: ModelCombinationStrategy = Depends(
        get_forecast_combination_strategy
    ),
    principal: Principal = Depends(require_principal),
) -> ForecastLabSnapshot:
    """`GET /api/v1/forecast-lab/snapshot` — see the module docstring for
    the scoping contract (D8/D17a)."""
    fl_stores = _forecast_lab_stores(stores)
    stations = _resolve_requested_stations(fl_stores, principal, station_code)
    return build_snapshot(
        fl_stores,
        stations=stations,
        archive_base_path=archive_base_path,
        observation_hours=observation_hours,
        combination_strategy=combination_strategy,
        clock=lambda: ensure_utc(datetime.now(UTC)),
    )
